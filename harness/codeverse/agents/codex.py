"""``codex:<model>`` — headless OpenAI Codex CLI session on a workspace.

argv: ``codex exec --json -C <ws> --sandbox workspace-write --skip-git-repo-check
[--model <model>] [-c model_reasoning_effort=<effort>]
[-c mcp_servers.3dcv.command=... -c mcp_servers.3dcv.args=[...]] <prompt>``
(prompt on stdin when long).  stdout is JSONL:

* ``{"type":"thread.started","thread_id":...}`` / ``turn.started``
* ``{"type":"item.completed","item":{"type":"agent_message"|"reasoning"|
  "command_execution"|"file_change"|"mcp_tool_call", ...}}``
* ``{"type":"turn.completed","usage":{"input_tokens","cached_input_tokens","output_tokens",
  "reasoning_output_tokens","cache_write_input_tokens"}}``
* ``{"type":"turn.failed","error":{"message":...}}`` / ``{"type":"error","message":...}``
"""

from __future__ import annotations

import json

from codeverse.agents.cli_common import (
    Session,
    begin_session,
    estimate_cost_safe,
    exists_on_path,
    failed,
    finish_session,
    hardened_env,
    mcp_command_for,
    tail,
)
from codeverse.agents.materialize import codex_mcp_overrides
from codeverse.agents.watchdog import run_with_watchdog
from codeverse.config import get_settings
from codeverse.contracts.agent import AgentJob, AgentResult
from codeverse.contracts.common import Usage

IDLE_GRACE_S = 300.0
STDIN_PROMPT_BYTES = 100_000
_TOOL_ITEMS = ("command_execution", "file_change", "mcp_tool_call", "web_search", "tool_call")

#: what ``-c model_reasoning_effort=`` accepts; ``""`` means "leave it to ~/.codex/config.toml"
REASONING_EFFORTS = ("", "minimal", "low", "medium", "high", "xhigh")


def split_model_effort(model: str, effort: str | None = None) -> tuple[str, str]:
    """``('gpt-5.6-sol@low', None) -> ('gpt-5.6-sol', 'low')``.

    Precedence: an explicit ``effort`` argument, then an ``@<effort>`` suffix on the
    model id, then ``Settings.agents.codex_reasoning_effort`` (default ``high`` — the
    codex CLI's own default is *medium*, and the harness would rather pay for thinking).
    ``""`` at any level means "pass no override and let ~/.codex/config.toml decide".
    """
    name, _, suffix = model.partition("@")
    chosen = effort if effort is not None else (suffix or get_settings().agents.codex_reasoning_effort)
    chosen = chosen.strip().lower()
    if chosen not in REASONING_EFFORTS:
        raise ValueError(f"bad codex reasoning effort {chosen!r}; expected one of {REASONING_EFFORTS}")
    return name.strip(), chosen


def effort_overrides(effort: str) -> list[str]:
    """The ``-c`` pair for a reasoning effort (empty when nothing should be forced)."""
    return ["-c", f"model_reasoning_effort={effort}"] if effort else []


class CodexEvents:
    """Folded view over the JSONL event stream."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.errors: list[str] = []
        self.tool_calls = 0
        self.turns_completed = 0
        self.usage_raw: dict[str, int] = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
                                          "reasoning_output_tokens": 0, "cache_write_input_tokens": 0}
        self.thread_id = ""
        self.n_events = 0

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return
        self.n_events += 1
        t = ev.get("type", "")
        if t == "thread.started":
            self.thread_id = str(ev.get("thread_id", ""))
        elif t == "item.completed":
            item = ev.get("item") or {}
            it = item.get("type", "")
            if it == "agent_message":
                self.messages.append(str(item.get("text", "")))
            elif it in _TOOL_ITEMS:
                self.tool_calls += 1
        elif t == "turn.completed":
            self.turns_completed += 1
            for k in self.usage_raw:
                self.usage_raw[k] += int((ev.get("usage") or {}).get(k) or 0)
        elif t == "turn.failed":
            self.errors.append(str((ev.get("error") or {}).get("message") or ev))
        elif t == "error":
            self.errors.append(str(ev.get("message") or ev))

    def usage(self, model: str) -> Usage:
        # `output_tokens` from the Responses API already CONTAINS
        # `reasoning_output_tokens` (measured: output − reasoning tracks the answer
        # length across every recorded cell).  `estimate_cost` bills output +
        # thoughts, so the reasoning share is subtracted here instead of being
        # charged twice — it stays visible in `thoughts_tokens`.
        reasoning = self.usage_raw["reasoning_output_tokens"]
        u = Usage(
            backend="codex", model=model,
            input_tokens=self.usage_raw["input_tokens"],
            output_tokens=max(0, self.usage_raw["output_tokens"] - reasoning),
            cached_tokens=self.usage_raw["cached_input_tokens"], tool_calls=self.tool_calls,
            thoughts_tokens=reasoning,
        )
        u.cost_usd = estimate_cost_safe("openai", model, u)
        return u


def parse_codex_jsonl(stdout: str) -> CodexEvents:
    ev = CodexEvents()
    for line in stdout.splitlines():
        ev.feed(line)
    return ev


class CodexAgent:
    kind = "codex"

    def __init__(self, model: str, binary: str | None = None, reasoning_effort: str | None = None):
        self.model, self.reasoning_effort = split_model_effort(model, reasoning_effort)
        self.binary = binary or get_settings().binaries.codex_cli

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.model}"

    def available(self) -> tuple[bool, str]:
        if not exists_on_path(self.binary):
            return False, f"codex CLI not found: {self.binary!r}"
        return True, "ok"

    def build_argv(self, s: Session, prompt: str | None) -> list[str]:
        """``prompt=None`` means 'read it from stdin' (``-`` positional)."""
        job = s.job
        argv = [self.binary, "exec", "--json", "-C", str(s.ws.root), "--sandbox", "workspace-write",
                "--skip-git-repo-check", "--ephemeral", "--color", "never"]
        if self.model:
            argv += ["--model", self.model]
        argv += effort_overrides(self.reasoning_effort)
        if job.spatial_tools:
            argv += codex_mcp_overrides(mcp_command_for(s.ws, job))
        argv.append(prompt if prompt is not None else "-")
        return argv

    def build_env(self, s: Session) -> dict[str, str]:
        return hardened_env(s.ws, s.job, keep={"OPENAI_API_KEY", "CODEX_API_KEY"})

    def run(self, job: AgentJob) -> AgentResult:
        ok, why = self.available()
        s = begin_session(job, self.kind)
        if not ok:
            return failed(s, "error", why)
        prompt = _compose_prompt(job)
        via_stdin = len(prompt.encode("utf-8")) > STDIN_PROMPT_BYTES
        argv = self.build_argv(s, None if via_stdin else prompt)
        events = CodexEvents()
        s.traj.append("invoke", argv=[a if a != prompt else f"<prompt {len(prompt)} chars>" for a in argv], stdin=via_stdin)

        def on_line(stream: str, line: str) -> None:
            s.traj.append("line", stream=stream, text=line[:4000])
            if stream == "stdout":
                events.feed(line)

        proc = run_with_watchdog(
            argv, cwd=s.ws.root, env=self.build_env(s), soft_timeout_s=job.timeout_s, idle_grace_s=IDLE_GRACE_S,
            on_line=on_line, stdin=prompt if via_stdin else None, activity_dirs=[s.ws.src, s.ws.public],
        )
        s.traj.write_text("stdout.jsonl", proc.stdout)
        s.traj.write_text("stderr.log", proc.stderr)
        usage = events.usage(self.model)
        usage.latency_ms = int(proc.duration_s * 1000)
        text = "\n\n".join(m for m in events.messages if m.strip())
        errors = list(events.errors)
        if proc.timed_out:
            ok, reason = False, "timeout"
            errors.append(f"killed by watchdog ({proc.killed_reason}) after {proc.duration_s:.0f}s")
        elif proc.rc != 0 or events.n_events == 0:
            ok, reason = False, "error"
            errors.append(f"rc={proc.rc}; events={events.n_events}; stderr tail: {tail(proc.stderr, 1500)}")
        elif events.errors and events.turns_completed == 0:
            ok, reason = False, "error"
        else:
            ok, reason = True, "completed"
        return finish_session(
            s, ok=ok, exit_reason=reason, text=text, usage=usage, tool_calls=events.tool_calls, errors=errors,
            rc=proc.rc, killed_reason=proc.killed_reason, thread_id=events.thread_id,
            turns_completed=events.turns_completed, usage_raw=events.usage_raw,
        )


def _compose_prompt(job: AgentJob) -> str:
    """codex exec has no system-prompt flag: prepend ``system_append``."""
    if not job.system_append:
        return job.prompt
    return f"<harness_instructions>\n{job.system_append}\n</harness_instructions>\n\n{job.prompt}"


__all__ = ["REASONING_EFFORTS", "CodexAgent", "CodexEvents", "effort_overrides", "parse_codex_jsonl",
           "split_model_effort"]
