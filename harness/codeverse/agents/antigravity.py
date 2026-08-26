"""``agy:<model>`` — Antigravity CLI (Google) headless session on a workspace.

argv: ``agy --print <prompt> --model <model> --dangerously-skip-permissions
--print-timeout <N>m --output-format json --add-dir <ws>``

Observed (agy 1.1.x) JSON envelope::

    {"conversation_id": "...", "status": "SUCCESS" | "ERROR", "response": "...", ["error": "..."],
     "duration_seconds": 2.3, "num_turns": 1,
     "usage": {"input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"}}

MCP: agy only supports *global* registration (``agy mcp add``), so no
per-workspace server is wired; the materialised body tells the agent to call
``python -m codeverse.cli.main tools <name> --json ...`` from the shell instead.
Cost: subscription-billed → ``cost_usd`` 0 with a note.  Plain-text output is
accepted when the JSON envelope is missing.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from codeverse.agents.cli_common import (
    Session,
    begin_session,
    deliver_prompt,
    exists_on_path,
    failed,
    finish_session,
    hardened_env,
    invoke,
    tail,
    watchdog_error,
)
from codeverse.config import get_settings
from codeverse.contracts.agent import AgentJob, AgentResult
from codeverse.contracts.common import Usage
from codeverse.proc import run_subprocess

log = logging.getLogger(__name__)

#: reasoning efforts agy exposes for the models that have them
EFFORTS = ("low", "medium", "high")
#: what a bare id is resolved to when agy offers only effort-suffixed spellings
DEFAULT_EFFORT = "medium"
_EFFORT_SUFFIXES = tuple(f"-{e}" for e in EFFORTS)


@lru_cache(maxsize=4)
def available_models(binary: str) -> tuple[str, ...]:
    """The model ids ``agy models`` offers, or ``()`` when it cannot be reached.

    Cached: one lookup per binary per process, and only for an id that needs it.
    """
    try:
        r = run_subprocess([binary, "models"], cwd=Path.cwd(), timeout_s=30.0)
    except OSError as e:  # pragma: no cover - defensive
        log.debug("agy models unavailable: %s", e)
        return ()
    if r.returncode != 0:
        return ()
    ids = []
    for line in r.stdout.splitlines():
        head = line.split("\t", 1)[0].strip()
        if head and " " not in head:
            ids.append(head)
    return tuple(ids)


def resolve_model(model: str, binary: str) -> str:
    """Map a bare model id onto the effort-suffixed one agy actually accepts.

    agy 1.1.19 rejects ``--model gemini-3.7-flash`` outright ("requires --effort
    (available: low, medium, high)") and equally rejects ``--effort`` for a model that
    has none ("--effort is not supported for model claude-sonnet-4-6"), so the effort
    cannot be a blanket flag — it belongs in the MODEL ID, which is the only spelling
    ``agy models`` lists (gemini-3.7-flash-low/-medium/-high).

    A bare id agy DOES offer (claude-sonnet-4-6) is passed through untouched, and so is
    anything we cannot check, so this can only turn a guaranteed failure into a run.
    """
    if not model or model.endswith(_EFFORT_SUFFIXES):
        return model
    offered = available_models(binary)
    if not offered or model in offered:
        return model
    for effort in (DEFAULT_EFFORT, *EFFORTS):
        if f"{model}-{effort}" in offered:
            log.info("agy has no bare %r; using the %s-effort id %r", model, effort, f"{model}-{effort}")
            return f"{model}-{effort}"
    return model


def parse_agy_json(stdout: str) -> dict[str, Any] | None:
    s = stdout.strip()
    if not s:
        return None
    for cand in (s, *reversed([ln for ln in s.splitlines() if ln.strip().startswith("{")])):
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ("response" in obj or "status" in obj):
            return obj
    return None


def usage_from_agy(env: dict[str, Any], model: str) -> Usage:
    u = env.get("usage") or {}
    return Usage(
        backend="agy", model=model,
        input_tokens=int(u.get("input_tokens") or 0),
        output_tokens=int(u.get("output_tokens") or 0),
        cached_tokens=int(u.get("cache_read_tokens") or 0),
        thoughts_tokens=int(u.get("thinking_tokens") or 0),
        cost_usd=0.0,  # subscription; no USD exposed
        latency_ms=int(float(env.get("duration_seconds") or 0.0) * 1000),
    )


class AntigravityAgent:
    kind = "agy"

    def __init__(self, model: str, binary: str | None = None):
        self.model = model
        self.binary = binary or get_settings().binaries.agy_cli

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.model}"

    def served_model(self) -> str:
        """The id actually sent to agy (a bare one gains its effort suffix)."""
        return resolve_model(self.model, self.binary)

    def available(self) -> tuple[bool, str]:
        if not exists_on_path(self.binary):
            return False, f"agy CLI not found: {self.binary!r}"
        return True, "ok"

    def build_argv(self, s: Session, prompt: str) -> list[str]:
        minutes = max(1, int(s.job.timeout_s // 60) + 1)
        argv = [self.binary, "--print", prompt, "--dangerously-skip-permissions",
                "--print-timeout", f"{minutes}m", "--output-format", "json",
                "--add-dir", str(s.ws.root), "--disable-slash-commands"]
        if self.model:
            argv += ["--model", self.served_model()]
        return argv

    def build_env(self, s: Session) -> dict[str, str]:
        return hardened_env(s.ws, s.job)

    def run(self, job: AgentJob) -> AgentResult:
        ok, why = self.available()
        s = begin_session(job, self.kind)
        if not ok:
            return failed(s, "error", why)
        prompt = deliver_prompt(s, _compose_prompt(s))
        proc = invoke(s, self.build_argv(s, prompt), self.build_env(s), prompt=prompt)
        env = parse_agy_json(proc.stdout)
        served = self.served_model()
        usage = usage_from_agy(env, served) if env else Usage(backend=self.kind, model=served)
        usage.latency_ms = usage.latency_ms or int(proc.duration_s * 1000)
        text = str(env.get("response", "")) if env else proc.stdout.strip()
        errors: list[str] = []
        if env is None:
            s.notes.append("no JSON envelope from agy; usage unknown (zeros), response taken from plain stdout")
        s.notes.append("agy is subscription-billed: cost_usd=0")
        if proc.timed_out:
            ok, reason = False, "timeout"
            errors.append(watchdog_error(proc))
        elif proc.rc != 0 or (env is not None and str(env.get("status", "SUCCESS")).upper() not in ("SUCCESS", "OK")):
            ok, reason = False, "error"
            errors.append(f"rc={proc.rc}; status={(env or {}).get('status')}; error={(env or {}).get('error', '')}; "
                          f"stderr tail: {tail(proc.stderr, 1500)}")
        elif not text.strip():
            ok, reason = False, "error"
            errors.append("empty response")
        else:
            ok, reason = True, "completed"
        return finish_session(
            s, ok=ok, exit_reason=reason, text=text, usage=usage, tool_calls=0, errors=errors,
            rc=proc.rc, killed_reason=proc.killed_reason,
            conversation_id=(env or {}).get("conversation_id", ""), num_turns=(env or {}).get("num_turns", 0),
        )


def _compose_prompt(s: Session) -> str:
    """agy has no system-prompt flag; prepend harness instructions + the absolute workspace path."""
    job = s.job
    head = f"Workspace root: {s.ws.root}\nWork ONLY inside it, using your file tools."
    if (s.ws.root / "AGENTS.md").is_file():
        head += " Read AGENTS.md there first and follow it."
    head += "\n"
    if job.system_append:
        head += f"\n<harness_instructions>\n{job.system_append}\n</harness_instructions>\n"
    return f"{head}\n{job.prompt}"


__all__ = ["AntigravityAgent", "parse_agy_json", "usage_from_agy"]
