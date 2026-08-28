"""``gemini-cli:<model>`` — headless Gemini CLI session on a workspace.

argv: ``gemini -p <prompt> -m <model> --approval-mode yolo --skip-trust --output-format json``

* env hardening: host secrets stripped, one pool key as ``GEMINI_API_KEY``,
  ``GEMINI_CLI_SYSTEM_SETTINGS_PATH`` (per session, in the trajectory dir) forcing
  api-key auth + dynamic model configuration (otherwise an unknown model is *silently*
  substituted), the 3dcv MCP server and ``mcp.allowed``, Node heap cap, no self-relaunch.
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
import time
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
    invoke,
    is_quota_failure,
    is_transient_failure,
    mcp_command_for,
    release_session,
    tail,
    watchdog_error,
)
from codeverse.agents.materialize import MCP_SERVER_NAME, MCP_TOOL_TIMEOUT_MS
from codeverse.agents.watchdog import CompletedProc
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
#: how long a retry waits for a *different* healthy key before reusing the same one
RETRY_KEY_WAIT_S = 10.0


def retry_window_left(timeout_s: float, elapsed_s: float) -> float | None:
    """Seconds of the session window a retry may still use, or ``None`` when too little
    is left to be worth an invoke.  A retry never restarts the window: chair_bl
    (loop_w1, 2026-08-28) got a FULL fresh window on attempt 2 and ran 32 more minutes
    against a run wall that had expired before the retry began."""
    min_window = min(120.0, float(timeout_s) * 0.25)
    left = float(timeout_s) - float(elapsed_s)
    return None if left < min_window else left


def _key_pool(keys: list[str]) -> KeyPool:
    """THE process-wide pool for these keys — ``models.gemini.shared_pool``, i.e. the
    same limiter the API path uses, with quotas from ``Settings.rate``.  This module
    used to build its own ``KeyPool(keys)`` with library defaults (900 RPM, no TPM
    bucket), so the CLI agent and the API path scheduled the same 22 keys under two
    different quotas (found by review 2026-08-28)."""
    from codeverse.models.gemini import shared_pool

    return shared_pool(keys)


def write_system_settings(path: Path | None = None, *, mcp_command: list[str] | None = None) -> Path:
    """Write the gemini-cli system-settings json (api-key auth + dynamic models) and return its path.

    gemini-cli applies this file LAST and ``mcp.allowed`` REPLACES rather than merges, so the
    3dcv server and the allow-list belong here, not in the agent-writable
    ``ws/.gemini/settings.json``: a server the agent planted there is Blocked."""
    path = path or (get_settings().cache_dir / "gemini_cli_settings.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {**SYSTEM_SETTINGS, "mcp": {"allowed": [MCP_SERVER_NAME]}}
    if mcp_command:
        data["mcpServers"] = {MCP_SERVER_NAME: {"command": mcp_command[0], "args": list(mcp_command[1:]),
                                                "timeout": MCP_TOOL_TIMEOUT_MS}}
    want = json.dumps(data, indent=2)
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
        # per SESSION, in the harness-owned trajectory dir: the command is per-run
        # (``--workspace <ws>``) and it is rewritten immediately before every invoke
        env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(write_system_settings(
            s.traj.dir / "gemini_settings.json",
            mcp_command=mcp_command_for(s.ws, s.job) if s.job.spatial_tools else None))
        env["GEMINI_CLI_NO_RELAUNCH"] = "1"
        node_opts = env.get("NODE_OPTIONS", "")
        if "--max-old-space-size" not in node_opts:
            env["NODE_OPTIONS"] = f"{node_opts} --max-old-space-size=6144".strip()
        return env

    # ------------------------------------------------------------------ run
    def run(self, job: AgentJob) -> AgentResult:
        ok, why = self.available()
        s = begin_session(job, self.kind)
        try:
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
            t0 = time.monotonic()
            next_soft: float | None = None  # attempt 1 uses the job's own window
            usage_total = Usage(backend=self.kind, model=self.model)
            while True:
                attempts += 1
                used.add(key)
                proc = self._invoke(s, prompt, key, attempt=attempts, soft_timeout_s=next_soft)
                outcome = self._interpret(s, proc)
                usage_total = usage_total + outcome["usage"]
                pool.report(key, "429" if outcome["quota"] else ("ok" if outcome["ok"] else "5xx"))
                pool.release()  # one acquire per attempt: keep the pool's in-flight gauge honest
                if outcome["ok"] or outcome["exit_reason"] in ("timeout", "model_substituted") or not outcome["transient"] or attempts >= 2:
                    break
                next_soft = retry_window_left(job.timeout_s, time.monotonic() - t0)
                if next_soft is None:
                    s.notes.append(f"attempt {attempts} transient failure ({outcome['exit_reason']}); "
                                   "no session window left — not retrying past the wall")
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
        finally:
            release_session(s)

    def _invoke(self, s: Session, prompt: str, key: str, *, attempt: int,
                soft_timeout_s: float | None = None) -> CompletedProc:
        return invoke(s, self.build_argv(prompt), self.build_env(s, key), prompt=prompt, attempt=attempt,
                      soft_timeout_s=soft_timeout_s, model=self.model, key_tail=key[-4:])

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
            errors.append(watchdog_error(proc))
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
