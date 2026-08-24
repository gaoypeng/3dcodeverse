"""``gemini-cli:<model>`` — headless Gemini CLI session on a workspace.

argv: ``gemini -p <prompt> -m <model> --approval-mode yolo --skip-trust --output-format json``

* env hardening: host secrets stripped, one pool key as ``GEMINI_API_KEY``,
  ``GEMINI_CLI_SYSTEM_SETTINGS_PATH`` forcing api-key auth + dynamic model
  configuration (otherwise an unknown model is *silently* substituted), Node
  heap cap, no self-relaunch.
* JSON envelope ``{session_id, response, stats:{models:{<m>:{tokens:{prompt,
  input, candidates, cached, thoughts}}}, tools:{totalCalls}}}`` → Usage (+ cost
  via pricing).  ``tokens.prompt`` is the TOTAL prompt size and ``tokens.input``
  the UNCACHED part (``prompt - cached``); ``Usage.input_tokens`` follows the
  pricing convention (total prompt, cached re-priced).  Each served model is
  priced at its own rate.  Requested model absent from ``stats.models`` →
  ``model_substituted``.
* one retry (rotated key when the pool has one, else the same key) on transient
  failures (429 / 503 / empty response); a throttled/empty pool never raises.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from codeverse.agents.cli_common import (
    Session,
    begin_session,
    deliver_prompt,
    estimate_cost_safe,
    exists_on_path,
    failed,
    finish_session,
    hardened_env,
    is_quota_failure,
    is_transient_failure,
    tail,
)
from codeverse.agents.watchdog import CompletedProc, run_with_watchdog
from codeverse.config import get_settings
from codeverse.contracts.agent import AgentJob, AgentResult
from codeverse.contracts.common import Usage
from codeverse.models.keypool import KeyPool, KeyPoolExhausted

log = logging.getLogger(__name__)

SYSTEM_SETTINGS = {
    # folderTrust must be OFF: with it on, gemini-cli silently disables the workspace
    # .gemini/settings.json mcpServers (even with --skip-trust) → no 3dcv tools.
    "security": {"auth": {"selectedType": "gemini-api-key"}, "folderTrust": {"enabled": False}},
    "experimental": {"dynamicModelConfiguration": True},
    "general": {"topicUpdateNarration": False},
}
IDLE_GRACE_S = 300.0
#: how long a retry waits for a *different* healthy key before reusing the same one
RETRY_KEY_WAIT_S = 10.0


_POOLS: dict[tuple[str, ...], KeyPool] = {}


def _key_pool(keys: list[str]) -> KeyPool:
    """Process-wide pool per key set so cooldowns persist across agent runs."""
    tkey = tuple(keys)
    pool = _POOLS.get(tkey)
    if pool is None:
        pool = _POOLS[tkey] = KeyPool(keys)
    return pool


def write_system_settings(path: Path | None = None) -> Path:
    """Write the gemini-cli system-settings json (api-key auth + dynamic models) and return its path."""
    path = path or (get_settings().cache_dir / "gemini_cli_settings.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    want = json.dumps(SYSTEM_SETTINGS, indent=2)
    if not path.is_file() or path.read_text() != want:
        path.write_text(want)
    return path


def parse_gemini_json(stdout: str) -> dict[str, Any] | None:
    """The CLI prints one JSON object (possibly after log noise); find it."""
    s = stdout.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start = s.find("{")
    while start != -1:
        try:
            obj = json.loads(s[start:])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            start = s.find("{", start + 1)
    return None


def _model_usage(tok: dict[str, Any], name: str) -> Usage:
    """One served model's counters in pricing semantics (``input_tokens`` = total prompt)."""
    cached = int(tok.get("cached") or 0)
    prompt = tok.get("prompt")
    # gemini-cli: tokens.prompt = promptTokenCount (total), tokens.input = prompt - cached
    total = int(prompt) if prompt is not None else int(tok.get("input") or 0) + cached
    return Usage(backend="gemini-cli", model=name, input_tokens=total, cached_tokens=cached,
                 output_tokens=int(tok.get("candidates") or 0), thoughts_tokens=int(tok.get("thoughts") or 0))


def usage_from_stats(stats: dict[str, Any], model: str) -> Usage:
    """Sum tokens across every served model (normally just ``model``); cost is the
    sum of each served model priced at its own rate (unknown → requested model's)."""
    u = Usage(backend="gemini-cli", model=model)
    cost = 0.0
    for name, m in (stats.get("models") or {}).items():
        part = _model_usage((m or {}).get("tokens") or {}, str(name))
        c = estimate_cost_safe("gemini", str(name), part)
        if c == 0.0 and name != model and (part.input_tokens or part.output_tokens):
            c = estimate_cost_safe("gemini", model, part)  # utility model missing from the price table
        cost += c
        u.input_tokens += part.input_tokens
        u.output_tokens += part.output_tokens
        u.cached_tokens += part.cached_tokens
        u.thoughts_tokens += part.thoughts_tokens
    u.tool_calls = int(((stats.get("tools") or {}).get("totalCalls")) or 0)
    u.cost_usd = cost
    return u


def _retry_key(pool: KeyPool, used: set[str]) -> str | None:
    """Key for a retry: a different healthy key if one frees up within
    ``RETRY_KEY_WAIT_S``, else the same key again (single-key pools, 5xx), else
    ``None`` when every key is throttled (429 cooldown) — the caller then stops."""
    for exclude in (used, None):
        try:
            return pool.acquire(exclude=exclude or None, timeout_s=RETRY_KEY_WAIT_S)
        except KeyPoolExhausted:
            continue
    return None


class GeminiCliAgent:
    kind = "gemini-cli"

    def __init__(self, model: str, binary: str | None = None):
        self.model = model
        self.binary = binary or get_settings().binaries.gemini_cli

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.model}"

    def available(self) -> tuple[bool, str]:
        if not exists_on_path(self.binary):
            return False, f"gemini CLI not found: {self.binary!r}"
        if not get_settings().gemini_api_keys:
            return False, "no Gemini API keys configured"
        return True, "ok"

    # ------------------------------------------------------------------ build
    def build_argv(self, prompt: str) -> list[str]:
        return [self.binary, "-p", prompt, "-m", self.model, "--approval-mode", "yolo",
                "--skip-trust", "--output-format", "json"]

    def build_env(self, s: Session, api_key: str) -> dict[str, str]:
        env = hardened_env(s.ws, s.job)
        env["GEMINI_API_KEY"] = api_key
        env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(write_system_settings())
        env["GEMINI_CLI_NO_RELAUNCH"] = "1"
        node_opts = env.get("NODE_OPTIONS", "")
        if "--max-old-space-size" not in node_opts:
            env["NODE_OPTIONS"] = f"{node_opts} --max-old-space-size=6144".strip()
        return env

    # ------------------------------------------------------------------ run
    def run(self, job: AgentJob) -> AgentResult:
        ok, why = self.available()
        s = begin_session(job, self.kind)
        if not ok:
            return failed(s, "error", why)
        prompt = deliver_prompt(s, _compose_prompt(job))
        pool = _key_pool(get_settings().gemini_api_keys)
        try:
            key = pool.acquire()
        except KeyPoolExhausted as e:  # every key throttled for the whole wait budget
            return failed(s, "budget", f"Gemini key pool exhausted before the first attempt: {e}")
        attempts = 0
        used: set[str] = set()
        usage_total = Usage(backend=self.kind, model=self.model)
        while True:
            attempts += 1
            used.add(key)
            proc = self._invoke(s, prompt, key, attempt=attempts)
            outcome = self._interpret(s, proc)
            usage_total = usage_total + outcome["usage"]
            pool.report(key, "429" if outcome["quota"] else ("ok" if outcome["ok"] else "5xx"))
            pool.release()  # one acquire per attempt: keep the pool's in-flight gauge honest
            if outcome["ok"] or outcome["exit_reason"] in ("timeout", "model_substituted") or not outcome["transient"] or attempts >= 2:
                break
            next_key = _retry_key(pool, used)
            if next_key is None:
                s.notes.append(f"attempt {attempts} transient failure ({outcome['exit_reason']}); no usable key for a retry")
                break
            key = next_key
            how = "rotated key" if key not in used else "same key (no alternative available)"
            s.notes.append(f"attempt {attempts} transient failure ({outcome['exit_reason']}); retrying with {how}")
            s.traj.append("retry", attempt=attempts, reason=outcome["errors"][:1])
        usage_total.cost_usd = round(usage_total.cost_usd, 6)
        return finish_session(
            s, ok=outcome["ok"], exit_reason=outcome["exit_reason"], text=outcome["text"],
            usage=usage_total, tool_calls=usage_total.tool_calls, errors=outcome["errors"],
            attempts=attempts, rc=proc.rc, killed_reason=proc.killed_reason, session_id=outcome.get("session_id", ""),
        )

    def _invoke(self, s: Session, prompt: str, key: str, *, attempt: int) -> CompletedProc:
        argv = self.build_argv(prompt)
        env = self.build_env(s, key)
        s.traj.append("invoke", attempt=attempt, argv=[a if a != prompt else f"<prompt {len(prompt)} chars>" for a in argv],
                      model=self.model, key_tail=key[-4:])
        proc = run_with_watchdog(
            argv, cwd=s.ws.root, env=env, soft_timeout_s=s.job.timeout_s, idle_grace_s=IDLE_GRACE_S,
            on_line=lambda stream, line: s.traj.append("line", stream=stream, text=line[:4000]),
            activity_dirs=[s.ws.src, s.ws.public],
        )
        suffix = "" if attempt == 1 else f".{attempt}"
        s.traj.write_text(f"stdout{suffix}.json", proc.stdout)
        s.traj.write_text(f"stderr{suffix}.log", proc.stderr)
        return proc

    def _interpret(self, s: Session, proc: CompletedProc) -> dict[str, Any]:
        parsed = parse_gemini_json(proc.stdout)
        stats = (parsed or {}).get("stats") or {}
        usage = usage_from_stats(stats, self.model) if stats else Usage(backend=self.kind, model=self.model)
        usage.latency_ms = int(proc.duration_s * 1000)
        text = str((parsed or {}).get("response") or "")
        errors: list[str] = []
        served = list((stats.get("models") or {}).keys())
        out: dict[str, Any] = {"usage": usage, "text": text, "ok": False, "exit_reason": "error",
                               "transient": False, "quota": False, "errors": errors,
                               "session_id": (parsed or {}).get("session_id", "")}
        if proc.timed_out:
            out["exit_reason"] = "timeout"
            errors.append(f"killed by watchdog ({proc.killed_reason}) after {proc.duration_s:.0f}s")
            return out
        if served and self.model not in served:
            out["exit_reason"] = "model_substituted"
            errors.append(f"requested {self.model!r} but CLI served {served}")
            return out
        if parsed is None or proc.rc != 0 or not text.strip():
            out["transient"] = is_transient_failure(proc.stderr, proc.stdout) or (parsed is not None and not text.strip())
            out["quota"] = is_quota_failure(proc.stderr, proc.stdout)
            errors.append(f"rc={proc.rc}; response={'<empty>' if not text.strip() else 'ok'}; stderr tail: {tail(proc.stderr, 1500)}")
            out["exit_reason"] = "budget" if out["quota"] else "error"
            return out
        out["ok"] = True
        out["exit_reason"] = "completed"
        return out


def _compose_prompt(job: AgentJob) -> str:
    """gemini-cli has no system-prompt flag: prepend ``system_append`` to the prompt."""
    if not job.system_append:
        return job.prompt
    return f"<harness_instructions>\n{job.system_append}\n</harness_instructions>\n\n{job.prompt}"


__all__ = ["GeminiCliAgent", "parse_gemini_json", "usage_from_stats", "write_system_settings"]
