"""``claude-code:<model>`` — headless Claude Code session on a workspace.

argv: ``claude -p <prompt> --output-format json --dangerously-skip-permissions
--model <model> --max-turns N --allowedTools ... [--mcp-config ws/.mcp.json
--strict-mcp-config] [--append-system-prompt ...]``

JSON envelope (claude 2.1.x, observed): ``{type:"result", subtype:"success",
is_error, result, session_id, num_turns, duration_ms, total_cost_usd,
stop_reason, terminal_reason, usage:{input_tokens, output_tokens,
cache_read_input_tokens, cache_creation_input_tokens,
output_tokens_details:{thinking_tokens}}, modelUsage:{<model>:{costUSD,...}}}``.
Auth: whatever the user's ``claude`` login is (subscription or
``ANTHROPIC_API_KEY``); we pass that variable through untouched if present.
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
    invoke,
    is_transient_failure,
    mcp_command_for,
    release_session,
    tail,
    watchdog_error,
)
from codeverse.agents.materialize import MCP_SERVER_NAME, MCP_TOOL_TIMEOUT_MS
from codeverse.config import get_settings
from codeverse.contracts.agent import AgentJob, AgentResult
from codeverse.contracts.common import Usage

#: ``--allowedTools``.  "Skill" is claude-code 2.1's model-invoked skill tool: without it
#: the bundles the harness materialises into ``ws/.claude/skills/`` are listed at session
#: start and then DENIED on activation, which reads in the transcript as the model
#: ignoring them.  It only ever opens files already inside the workspace.
ALLOWED_TOOLS = ("Read", "Edit", "Write", "MultiEdit", "Glob", "Grep", "Skill",
                 "Bash(node:*)", "Bash(python:*)", "Bash(python3:*)", "Bash(ls:*)", "mcp__c3v__*")


def parse_claude_json(stdout: str) -> dict[str, Any] | None:
    """Envelope is one JSON object; tolerate leading log noise and stream-json arrays."""
    s = stdout.strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        obj = None
        for line in reversed(s.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    cand = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(cand, dict) and cand.get("type") == "result":
                    return cand
                obj = obj or cand
        return obj if isinstance(obj, dict) else None
    if isinstance(obj, list):  # stream-json array: last result event wins
        results = [e for e in obj if isinstance(e, dict) and e.get("type") == "result"]
        return results[-1] if results else None
    return obj if isinstance(obj, dict) else None


def usage_from_envelope(env: dict[str, Any], model: str) -> Usage:
    u = env.get("usage") or {}
    usage = Usage(
        backend="claude-code", model=model,
        input_tokens=int(u.get("input_tokens") or 0),
        output_tokens=int(u.get("output_tokens") or 0),
        cached_tokens=int(u.get("cache_read_input_tokens") or 0),
        thoughts_tokens=int(((u.get("output_tokens_details") or {}).get("thinking_tokens")) or 0),
        tool_calls=max(int(env.get("num_turns") or 0) - 1, 0),
        cost_usd=float(env.get("total_cost_usd") or 0.0),
        latency_ms=int(env.get("duration_ms") or 0),
    )
    usage.model = primary_served_model(env, model)
    return usage


def primary_served_model(env: dict[str, Any], model: str) -> str:
    """The model that actually did the WORK, of the (up to two) that one session bills.

    ``claude -p`` bills the work model PLUS the small background model the CLI uses for
    its own housekeeping, and claude 2.1.243 lists the AUXILIARY one first — so taking
    ``served[0]`` recorded a model that did ~1% of the tokens whenever ``--model`` was an
    ALIAS ('sonnet', 'opus', or the default arm).  A sonnet-vs-default comparison was
    therefore labelled haiku-vs-haiku, in CallCost.model and in OneShotResult.usage.

    The envelope's top-level ``usage`` block reports the MAIN conversation only, so the
    entry whose token counts match it is the work model; otherwise take the dearest.
    An id we passed verbatim is already a served name and is kept as-is.
    """
    served: dict[str, Any] = env.get("modelUsage") or {}
    if not served or model in served:
        return model
    u = env.get("usage") or {}
    want = (int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0))
    if any(want):
        for name, row in served.items():
            r = row or {}
            if (int(r.get("inputTokens") or 0), int(r.get("outputTokens") or 0)) == want:
                return name
    return max(served, key=lambda n: float((served[n] or {}).get("costUSD") or 0.0))


class ClaudeCodeAgent:
    kind = "claude-code"

    def __init__(self, model: str, binary: str | None = None):
        self.model = model
        self.binary = binary or get_settings().binaries.claude_cli

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.model}"

    def available(self) -> tuple[bool, str]:
        if not exists_on_path(self.binary):
            return False, f"claude CLI not found: {self.binary!r}"
        return True, "ok"

    def build_argv(self, s: Session, prompt: str) -> list[str]:
        job = s.job
        argv = [self.binary, "-p", prompt, "--output-format", "json", "--dangerously-skip-permissions",
                "--max-turns", str(job.max_turns), "--no-session-persistence",
                "--allowedTools", ",".join(ALLOWED_TOOLS)]
        if self.model:
            argv += ["--model", self.model]
        if job.spatial_tools:
            # written fresh into the harness-owned trajectory dir for THIS session, from
            # the typed job — never the workspace's .mcp.json, which the agent can rewrite
            # between rounds to choose what the next session launches (audit 2026-08-27)
            cfg = s.traj.dir / "mcp.json"
            cmd = mcp_command_for(s.ws, job)
            cfg.write_text(json.dumps(
                {"mcpServers": {MCP_SERVER_NAME: {"type": "stdio", "command": cmd[0], "args": cmd[1:],
                                                  "timeout": MCP_TOOL_TIMEOUT_MS}}}, indent=2))
            argv += ["--mcp-config", str(cfg), "--strict-mcp-config"]
        else:
            argv += ["--strict-mcp-config"]  # never inherit the user's ambient MCP servers
        if job.system_append:
            argv += ["--append-system-prompt", job.system_append]
        return argv

    def build_env(self, s: Session) -> dict[str, str]:
        # keep ANTHROPIC_API_KEY if the user relies on it; subscription auth needs nothing
        return hardened_env(s.ws, s.job, keep={"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"})

    def run(self, job: AgentJob) -> AgentResult:
        ok, why = self.available()
        s = begin_session(job, self.kind)
        try:
            if not ok:
                return failed(s, "error", why)
            prompt = deliver_prompt(s, job.prompt)
            proc = invoke(s, self.build_argv(s, prompt), self.build_env(s), prompt=prompt)
            env = parse_claude_json(proc.stdout)
            usage = usage_from_envelope(env, self.model) if env else Usage(backend=self.kind, model=self.model)
            usage.latency_ms = usage.latency_ms or int(proc.duration_s * 1000)
            text = str((env or {}).get("result") or "")
            turns = int((env or {}).get("num_turns") or 0)
            errors: list[str] = []
            if proc.timed_out:
                reason, ok = "timeout", False
                errors.append(watchdog_error(proc))
            elif env is None or proc.rc != 0:
                reason, ok = "error", False
                errors.append(f"rc={proc.rc}; no result envelope; stderr tail: {tail(proc.stderr, 1500)}")
                if is_transient_failure(proc.stderr, proc.stdout):
                    reason = "budget" if "rate" in proc.stderr.lower() else "error"
            elif env.get("is_error") or str(env.get("subtype", "")).startswith("error"):
                ok = False
                sub = str(env.get("subtype", ""))
                reason = "budget" if "max_turns" in sub else "error"
                errors.append(f"claude reported {sub or 'is_error'}: {tail(text, 800)}")
            else:
                reason, ok = "completed", True
            return finish_session(
                s, ok=ok, exit_reason=reason, text=text, usage=usage, tool_calls=max(turns - 1, 0),
                errors=errors, rc=proc.rc, killed_reason=proc.killed_reason, num_turns=turns,
                session_id=(env or {}).get("session_id", ""), subtype=(env or {}).get("subtype", ""),
                model_usage=(env or {}).get("modelUsage", {}),
            )
        finally:
            release_session(s)


__all__ = ["ClaudeCodeAgent", "parse_claude_json", "primary_served_model", "usage_from_envelope", "ALLOWED_TOOLS"]
