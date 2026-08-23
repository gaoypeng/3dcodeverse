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
from typing import Any

from codeverse.agents.cli_common import (
    Session,
    begin_session,
    deliver_prompt,
    exists_on_path,
    failed,
    finish_session,
    hardened_env,
    tail,
)
from codeverse.agents.watchdog import run_with_watchdog
from codeverse.config import get_settings
from codeverse.contracts.agent import AgentJob, AgentResult
from codeverse.contracts.common import Usage

IDLE_GRACE_S = 300.0


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
            argv += ["--model", self.model]
        return argv

    def build_env(self, s: Session) -> dict[str, str]:
        return hardened_env(s.ws, s.job)

    def run(self, job: AgentJob) -> AgentResult:
        ok, why = self.available()
        s = begin_session(job, self.kind)
        if not ok:
            return failed(s, "error", why)
        prompt = deliver_prompt(s, _compose_prompt(s))
        argv = self.build_argv(s, prompt)
        s.traj.append("invoke", argv=[a if a != prompt else f"<prompt {len(prompt)} chars>" for a in argv])
        proc = run_with_watchdog(
            argv, cwd=s.ws.root, env=self.build_env(s), soft_timeout_s=job.timeout_s, idle_grace_s=IDLE_GRACE_S,
            on_line=lambda stream, line: s.traj.append("line", stream=stream, text=line[:4000]),
            activity_dirs=[s.ws.src, s.ws.public],
        )
        s.traj.write_text("stdout.json", proc.stdout)
        s.traj.write_text("stderr.log", proc.stderr)
        env = parse_agy_json(proc.stdout)
        usage = usage_from_agy(env, self.model) if env else Usage(backend=self.kind, model=self.model)
        usage.latency_ms = usage.latency_ms or int(proc.duration_s * 1000)
        text = str(env.get("response", "")) if env else proc.stdout.strip()
        errors: list[str] = []
        if env is None:
            s.notes.append("no JSON envelope from agy; usage unknown (zeros), response taken from plain stdout")
        s.notes.append("agy is subscription-billed: cost_usd=0")
        if proc.timed_out:
            ok, reason = False, "timeout"
            errors.append(f"killed by watchdog ({proc.killed_reason}) after {proc.duration_s:.0f}s")
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
