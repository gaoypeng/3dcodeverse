"""The three vendor CLI backends: ``claude-code:<model>``, ``codex:<model>``, ``agy:<model>``.

Each runs ONE headless session with the prompt on stdin (``cli_common.invoke``), reads its CLI's
wire format back into an ``AgentResult`` and leaves retrying to the CLI.  Google's models go
through ``agy`` (Antigravity in Gemini-API mode, one key per session from the shared pool).
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from codeverse3d.agents.cli_common import (
    HARD_GRACE_S,
    CompletedProc,
    Session,
    SessionEnd,
    ToolCall,
    begin_session,
    classify_end,
    exists_on_path,
    failed,
    find_json_object,
    finish_session,
    hardened_env,
    invoke,
    mcp_command_for,
    provider_wait,
    record_tool_calls,
    release_session,
    watchdog_error,
)
from codeverse3d.agents.materialize import MCP_SERVER_NAME, MCP_TOOL_TIMEOUT_MS, codex_mcp_overrides
from codeverse3d.config import get_settings
from codeverse3d.contracts.agent import AgentJob, AgentResult
from codeverse3d.contracts.common import Usage
from codeverse3d.models.pricing import EFFORTS, cache_write_surcharge, estimate_cost, openai_usage
from codeverse3d.models.retry import KeyPool, KeyPoolExhausted

log = logging.getLogger(__name__)


class _CliAgent:

    kind: str
    cli_label: str
    model: str
    binary: str

    def available(self) -> tuple[bool, str]:
        if not exists_on_path(self.binary):
            return False, f"{self.cli_label} CLI not found: {self.binary!r}"
        return True, "ok"


def _compose_prompt(job: AgentJob) -> str:
    """codex and agy have no system-prompt flag: prepend ``system_append``."""
    if not job.system_append:
        return job.prompt
    return f"<harness_instructions>\n{job.system_append}\n</harness_instructions>\n\n{job.prompt}"


# ===================================================================== claude_code
#: ``--allowedTools``; "Skill" is needed or the routed bundles in ``ws/.claude/skills/`` are denied on activation.
ALLOWED_TOOLS = ("Read", "Edit", "Write", "MultiEdit", "Glob", "Grep", "Skill",
                 "Bash(node:*)", "Bash(python:*)", "Bash(python3:*)", "Bash(ls:*)", f"mcp__{MCP_SERVER_NAME}__*")
CLAUDE_SKILL_TOOL = "Skill"
#: A session sees only the bundles the harness routed into ``<ws>/.claude/skills``.  ``--setting-sources
#: project`` drops the account-synced skills and the user's settings (effort is stated via
#: ``Settings.agents.claude_effort``); ``CLAUDE_CODE_DISABLE_BUNDLED_SKILLS`` (build_env) drops the
#: bundled ones, and ``design`` / ``doctor`` survive that switch so ``skillOverrides`` hides them by name.
CLAUDE_SETTING_SOURCES = "project"
CLAUDE_SETTINGS = {"skillOverrides": {"design": "off", "doctor": "off"}}


def parse_claude_json(stdout: str) -> dict[str, Any] | None:
    return find_json_object(stdout, lambda d: d.get("type") == "result")


class ClaudeStream:
    """Folded view over ``--output-format stream-json``, fed live: the tool calls, the skill index the
    ``init`` event reported, each message's usage block (last copy per message id), the CLI's
    ``api_retry`` events and the arrival time of every other event (``cli_common.provider_wait``)."""

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []
        self.skills: list[str] | None = None
        self._at: dict[str, int] = {}  # tool_use id → index in calls, for its tool_result
        self.messages: dict[str, dict[str, Any]] = {}
        self.served = ""
        self.retries: list[tuple[float, float]] = []
        self.progress: list[float] = []

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return  # a >1 MB line arrives chunked (proc._LINE_CAP_BYTES): a Write, never a Skill call
        if not isinstance(ev, dict):
            return
        if ev.get("type") == "system" and ev.get("subtype") == "api_retry":
            self.retries.append((time.time(), float(ev.get("retry_delay_ms") or 0) / 1000.0))
            return
        self.progress.append(time.time())
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            self.skills = [str(x) for x in ev.get("skills") or []]
        elif ev.get("type") in ("assistant", "user"):
            msg = ev.get("message") or {}
            if ev.get("type") == "assistant" and msg.get("id") and isinstance(msg.get("usage"), dict):
                self.messages[str(msg["id"])] = msg["usage"]
                self.served = str(msg.get("model") or self.served)
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    name = str(block.get("name") or "")
                    args = block.get("input") if isinstance(block.get("input"), dict) else {}
                    self._at[str(block.get("id"))] = len(self.calls)
                    self.calls.append(ToolCall(tool=name, args=args, skill=str(args.get("skill") or "")
                                               if name == CLAUDE_SKILL_TOOL else ""))
                elif block.get("type") == "tool_result" and block.get("is_error") is True:
                    i = self._at.get(str(block.get("tool_use_id")))
                    if i is not None:
                        self.calls[i] = dataclasses.replace(self.calls[i], failed=True)

    def usage(self, model: str) -> Usage:
        """Usage of a session that never printed its result event: every message's usage block, summed (``output_tokens`` is a floor)."""
        if not self.messages:   # died before its first reply: nothing billed, nothing to price
            return Usage(backend="claude-code", model=model)
        keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
        total = {k: sum(int(u.get(k) or 0) for u in self.messages.values()) for k in keys}
        usage = usage_from_envelope({"usage": total}, self.served or model)
        usage.cost_usd = (estimate_cost("anthropic", usage.model, usage)
                          + cache_write_surcharge("anthropic", usage.model, total["cache_creation_input_tokens"]))
        return usage


def usage_from_envelope(env: dict[str, Any], model: str) -> Usage:
    """``input_tokens`` is the TOTAL prompt (uncached + cache reads + cache writes), the convention
    pricing.py and anthropic.py share; the envelope's own ``input_tokens`` is the uncached part only."""
    u = env.get("usage") or {}
    cache_read = int(u.get("cache_read_input_tokens") or 0)
    usage = Usage(
        backend="claude-code", model=model,
        input_tokens=int(u.get("input_tokens") or 0) + cache_read + int(u.get("cache_creation_input_tokens") or 0),
        output_tokens=int(u.get("output_tokens") or 0),
        cached_tokens=cache_read,
        thoughts_tokens=int(((u.get("output_tokens_details") or {}).get("thinking_tokens")) or 0),
        cost_usd=float(env.get("total_cost_usd") or 0.0),
        latency_ms=int(env.get("duration_ms") or 0),
    )
    usage.model = primary_served_model(env, model)
    return usage


def primary_served_model(env: dict[str, Any], model: str) -> str:
    """The model that did the WORK, of the (up to two) that one session bills.

    ``claude -p`` also bills a small background model, which can be listed first.  The envelope's
    top-level ``usage`` covers the main conversation only, so the entry whose counts match is the
    work model; otherwise the dearest.  An id passed verbatim is kept.
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


def claude_end(proc: CompletedProc, env: dict[str, Any] | None, text: str) -> SessionEnd:
    sub = str((env or {}).get("subtype", ""))
    if proc.timed_out:
        return classify_end(proc, ok=False, text=text)
    if env is None or proc.rc != 0:
        return classify_end(proc, ok=False, text=text, said=(proc.stderr, proc.stdout))
    if env.get("is_error") or sub.startswith("error"):
        return SessionEnd("budget") if "max_turns" in sub else classify_end(proc, ok=False, text=text, said=(text,))
    return classify_end(proc, ok=True, text=text)


class ClaudeCodeAgent(_CliAgent):
    kind = "claude-code"
    cli_label = "claude"

    def __init__(self, model: str, binary: str | None = None):
        self.model = model
        self.binary = binary or get_settings().binaries.claude_cli

    def build_argv(self, s: Session) -> list[str]:
        job = s.job
        argv = [self.binary, "-p", "--output-format", "stream-json", "--verbose",
                "--dangerously-skip-permissions",
                "--max-turns", str(job.max_turns), "--no-session-persistence",
                "--setting-sources", CLAUDE_SETTING_SOURCES, "--settings", json.dumps(CLAUDE_SETTINGS),
                "--allowedTools", ",".join(ALLOWED_TOOLS)]
        if self.model:
            argv += ["--model", self.model]
        if effort := get_settings().agents.claude_effort:
            argv += ["--effort", effort]
        if job.spatial_tools:
            # written fresh into the harness-owned trajectory dir, never the workspace's .mcp.json (agent-writable)
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
        env = hardened_env(s.ws, s.job, keep={"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"})
        env["CLAUDE_CODE_DISABLE_BUNDLED_SKILLS"] = "1"   # see CLAUDE_SETTINGS
        return env

    def run(self, job: AgentJob) -> AgentResult:
        ok, why = self.available()
        s = begin_session(job, self.kind)
        try:
            if not ok:
                return failed(s, "error", why)
            stream = ClaudeStream()
            proc = invoke(s, self.build_argv(s), self.build_env(s), prompt=job.prompt, on_stdout=stream.feed)
            ended = time.time()
            n_calls = record_tool_calls(s, stream.calls, source="claude-code stream-json", skills_index=stream.skills)
            env = parse_claude_json(proc.stdout)
            usage = usage_from_envelope(env, self.model) if env else stream.usage(self.model)
            usage.tool_calls = n_calls
            usage.latency_ms = usage.latency_ms or int(proc.duration_s * 1000)
            text = str((env or {}).get("result") or "")
            turns = int((env or {}).get("num_turns") or 0)
            errors: list[str] = []
            sub = str((env or {}).get("subtype", ""))
            end = claude_end(proc, env, text)
            if proc.timed_out:
                errors.append(watchdog_error(proc))
            elif env is None or proc.rc != 0:
                errors.append(f"rc={proc.rc}; no result envelope; stderr tail: {proc.stderr[-1500:]}")
            elif end.exit_reason != "completed":
                errors.append(f"claude reported {sub or 'is_error'}: {text[-800:]}")
            return finish_session(
                s, ok=end.exit_reason == "completed", exit_reason=end.exit_reason, text=text, usage=usage,
                tool_calls=n_calls, turns=turns, errors=errors, transient=end.transient, quota=end.quota, stalled=end.stalled,
                provider_wait_s=provider_wait(stream.retries, ended, stream.progress),
                rc=proc.rc, killed_reason=proc.killed_reason, num_turns=turns, effort=get_settings().agents.claude_effort,
                session_id=(env or {}).get("session_id", ""), subtype=(env or {}).get("subtype", ""),
                model_usage=(env or {}).get("modelUsage", {}),
                usage_from="envelope" if env else "stream messages" if stream.messages else "none",
            )
        finally:
            release_session(s)


# ===================================================================== codex
#: ``-c`` for every codex session: only the routed bundles, not codex's bundled ``.system`` skills.
CODEX_SKILL_OVERRIDES = ("-c", "skills.bundled.enabled=false")
_TOOL_ITEMS = ("command_execution", "file_change", "mcp_tool_call", "web_search", "tool_call")
#: the item fields a tool trace keeps: what was run, never what came back.  codex has no
#: skill tool; the shell command that opens SKILL.md is the activation record.
_TRACE_KEYS = ("command", "server", "tool", "arguments", "changes", "query")

REASONING_EFFORTS = ("", "minimal", "low", "medium", "high", "xhigh")


def split_model_effort(model: str, effort: str | None = None) -> tuple[str, str]:
    """``('gpt-5.6-sol@low', None) -> ('gpt-5.6-sol', 'low')``.

    Precedence: ``effort`` argument, ``@<effort>`` suffix, ``Settings.agents.codex_reasoning_effort``;
    ``""`` passes no override (``~/.codex/config.toml`` decides).
    """
    name, _, suffix = model.partition("@")
    chosen = effort if effort is not None else (suffix or get_settings().agents.codex_reasoning_effort)
    chosen = chosen.strip().lower()
    if chosen not in REASONING_EFFORTS:
        raise ValueError(f"bad codex reasoning effort {chosen!r}; expected one of {REASONING_EFFORTS}")
    return name.strip(), chosen


def effort_overrides(effort: str) -> list[str]:
    return ["-c", f"model_reasoning_effort={effort}"] if effort else []


#: A session killed before its turn completed books no usage (codex reports per TURN, ``--ephemeral``
#: leaves no rollout), so cost is ESTIMATED: every completed item is one more request re-reading a
#: growing context.  Constants are fitted to completed sessions.
_EST_SYSTEM_TOKENS = 20_000
_EST_IMAGE_TOKENS = 1_000
_EST_CHARS_PER_TOKEN = 4.0
_EST_OUTPUT_SHARE = 0.0113
_B64_DATA = re.compile(r'"data"\s*:\s*"[A-Za-z0-9+/=]{200,}"')


class CodexEvents:

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.errors: list[str] = []
        self.tool_calls = 0
        self.turns_completed = 0
        self.usage_raw: dict[str, int] = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
                                          "reasoning_output_tokens": 0, "cache_write_input_tokens": 0}
        self.thread_id = ""
        self.n_events = 0
        self.calls: list[ToolCall] = []
        self.items: list[tuple[float, int]] = []

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
            if it != "reasoning":
                images = len(_B64_DATA.findall(line))
                self.items.append((len(_B64_DATA.sub('"data":""', line)) / _EST_CHARS_PER_TOKEN, images))
            if it == "agent_message":
                self.messages.append(str(item.get("text", "")))
            elif it in _TOOL_ITEMS:
                self.tool_calls += 1
                self.calls.append(ToolCall(tool=it, args={k: item[k] for k in _TRACE_KEYS if k in item},
                                           failed=item.get("status") in ("failed", "declined")
                                           or item.get("exit_code") not in (None, 0)))
        elif t == "turn.completed":
            self.turns_completed += 1
            for k in self.usage_raw:
                self.usage_raw[k] += int((ev.get("usage") or {}).get(k) or 0)
        elif t == "turn.failed":
            self.errors.append(str((ev.get("error") or {}).get("message") or ev))
        elif t == "error":
            self.errors.append(str(ev.get("message") or ev))

    def usage(self, model: str) -> Usage:
        r = self.usage_raw   # the Responses API's output_tokens CONTAIN reasoning_output_tokens
        return openai_usage("codex", model, prompt=r["input_tokens"], cached=r["cached_input_tokens"],
                            completion=r["output_tokens"], reasoning=r["reasoning_output_tokens"],
                            tool_calls=self.tool_calls)

    def estimated_usage(self, model: str, prompt_chars: int) -> Usage:
        ctx = prompt_chars / _EST_CHARS_PER_TOKEN + _EST_SYSTEM_TOKENS
        prompt = uncached = ctx
        for text, images in self.items:
            grown = text + images * _EST_IMAGE_TOKENS
            ctx += grown
            uncached += grown
            prompt += ctx
        return openai_usage("codex", model, prompt=int(prompt), cached=int(prompt - uncached),
                            completion=int(prompt * _EST_OUTPUT_SHARE), reasoning=0, tool_calls=self.tool_calls)


def parse_codex_jsonl(stdout: str) -> CodexEvents:
    ev = CodexEvents()
    for line in stdout.splitlines():
        ev.feed(line)
    return ev


def codex_end(proc: CompletedProc, events: CodexEvents, text: str) -> SessionEnd:
    ok = not proc.timed_out and proc.rc == 0 and events.n_events > 0 and not (events.errors and events.turns_completed == 0)
    return classify_end(proc, ok=ok, text=text, said=(*events.errors, proc.stderr))


class CodexAgent(_CliAgent):
    kind = "codex"
    cli_label = "codex"

    def __init__(self, model: str, binary: str | None = None, reasoning_effort: str | None = None):
        self.model, self.reasoning_effort = split_model_effort(model, reasoning_effort)
        self.binary = binary or get_settings().binaries.codex_cli

    def build_argv(self, s: Session) -> list[str]:
        job = s.job
        argv = [self.binary, "exec", "--json", "-C", str(s.ws.root), "--sandbox", "workspace-write",
                "--skip-git-repo-check", "--ephemeral", "--color", "never"]
        if self.model:
            argv += ["--model", self.model]
        argv += effort_overrides(self.reasoning_effort)
        argv += CODEX_SKILL_OVERRIDES
        if job.spatial_tools:
            argv += codex_mcp_overrides(mcp_command_for(s.ws, job))
        argv.append("-")
        return argv

    def build_env(self, s: Session) -> dict[str, str]:
        return hardened_env(s.ws, s.job, keep={"OPENAI_API_KEY", "CODEX_API_KEY"})

    def run(self, job: AgentJob) -> AgentResult:
        """A killed session books an ESTIMATE (:meth:`CodexEvents.estimated_usage`); ``provider_wait_s`` stays 0.0."""
        ok, why = self.available()
        s = begin_session(job, self.kind)
        try:
            if not ok:
                return failed(s, "error", why)
            events = CodexEvents()
            prompt = _compose_prompt(job)
            proc = invoke(s, self.build_argv(s), self.build_env(s), prompt=prompt,
                          stdout_name="stdout.jsonl", on_stdout=events.feed)
            record_tool_calls(s, events.calls, source="codex exec --json")
            # no turn completed but requests were made: an estimate, booked as source="estimate", never a silent $0
            estimated = events.turns_completed == 0 and (bool(events.items) or proc.timed_out)
            usage = (events.estimated_usage(self.model, len(prompt)) if estimated else events.usage(self.model))
            usage.latency_ms = int(proc.duration_s * 1000)
            text = "\n\n".join(m for m in events.messages if m.strip())
            errors = list(events.errors)
            if proc.timed_out:
                errors.append(watchdog_error(proc))
            elif proc.rc != 0 or events.n_events == 0:
                errors.append(f"rc={proc.rc}; events={events.n_events}; stderr tail: {proc.stderr[-1500:]}")
            end = codex_end(proc, events, text)
            ok = end.exit_reason == "completed"
            return finish_session(
                s, ok=ok, exit_reason=end.exit_reason, text=text, usage=usage, tool_calls=events.tool_calls,
                turns=events.turns_completed, errors=errors, transient=end.transient, quota=end.quota, stalled=end.stalled,
                rc=proc.rc, killed_reason=proc.killed_reason, thread_id=events.thread_id, effort=self.reasoning_effort,
                turns_completed=events.turns_completed, usage_raw=events.usage_raw, usage_estimated=estimated,
            )
        finally:
            release_session(s)


# ===================================================================== antigravity

def api_model_effort(model: str, asked: str | None = None) -> tuple[str, str]:
    """``gemini-4-argon-low`` → ``('gemini-4-argon', 'low')``.  Precedence: the id's effort suffix,
    ``asked`` (``AgentJob.effort``), then ``AGY_API_EFFORT``."""
    for effort in EFFORTS:
        if model.endswith(f"-{effort}"):
            return model[: -len(effort) - 1], effort
    return model, asked or AGY_API_EFFORT


def parse_agy_json(stdout: str) -> dict[str, Any] | None:
    return find_json_object(stdout, lambda d: "response" in d or "status" in d)


#: what agy prints on stderr when its own ``--print-timeout`` stops a turn in progress; the
#: envelope still says ``SUCCESS``
AGY_PRINT_TIMEOUT = "print timeout after"
#: the envelope's ``finished_reason``: ``print_timeout`` | ``interrupted`` | ``error``, absent on a
#: normal finish.  Read first; the stderr line is the fallback.
AGY_FINISHED_REASON = "finished_reason"
AGY_LOG = "agy.log"
AGY_CONVERSATIONS = Path(".gemini") / "antigravity-cli" / "conversations"
#: where agy records each step under the session's HOME, for the watchdog's activity check; agy
#: prints nothing until it exits.  Passed by name because the watchdog's walk skips ``.gemini``.
AGY_STEP_DIRS = (Path(".gemini") / "antigravity-cli" / "brain", AGY_CONVERSATIONS)
#: agy's Gemini-API mode: ``modelProvider: "gemini"`` in its settings plus ``GEMINI_API_KEY`` in the
#: env.  Without the setting the key alone is ignored.  It is the only mode the harness runs.
AGY_SETTINGS = Path(".gemini") / "antigravity-cli" / "settings.json"
AGY_API_SETTINGS = {"modelProvider": "gemini"}
#: in API mode ``--model gemini-api://<id>`` sends ``<id>`` to the API verbatim; ``--effort`` sets the thinking level
AGY_API_SCHEME = "gemini-api://"
#: the effort an id without a ``-low/-medium/-high`` suffix runs at when the job asks for none
AGY_API_EFFORT = "high"
DEFAULT_AGY_MODEL = "gemini-4-argon"
#: agy's only MCP config is per user, so an API-key session gets a HOME of its own with this file in it
AGY_MCP_CONFIG = Path(".gemini") / "config" / "mcp_config.json"
_AGY_CALL_ID = re.compile(r"call_\w+")
_AGY_TOOL = re.compile(r"[a-z][a-z0-9_]*")


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise ValueError("bad varint")
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return value, i


def _pb_strings(buf: bytes, depth: int = 0) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        wire = key & 7
        if key >> 3 == 0:
            raise ValueError("field number 0")
        if wire == 0:
            _, i = _varint(buf, i)
        elif wire in (1, 5):
            i += 8 if wire == 1 else 4
        elif wire == 2:
            n, i = _varint(buf, i)
            chunk, i = buf[i:i + n], i + n
            if len(chunk) != n:
                raise ValueError("truncated field")
            try:
                text = chunk.decode("utf-8")
            except UnicodeDecodeError:
                text = ""
            if text and all(ch.isprintable() or ch in "\n\r\t" for ch in text):
                out.append(text)
            elif depth < 8 and chunk:
                with contextlib.suppress(ValueError):  # a bytes field, not a nested message
                    out += _pb_strings(chunk, depth + 1)
        else:
            raise ValueError(f"wire type {wire}")
    return out


def agy_tool_calls(db: Path) -> list[ToolCall]:
    """Every tool call recorded in one agy conversation database, once per call id.

    agy has no skill tool; the model opens ``SKILL.md`` with its file tool, so the path is the activation record.
    """
    calls: dict[str, ToolCall] = {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for (payload,) in con.execute("SELECT step_payload FROM steps ORDER BY idx"):
            try:
                strs = _pb_strings(payload or b"")
            except ValueError:
                continue
            for call_id, tool, raw in zip(strs, strs[1:], strs[2:], strict=False):
                if not (_AGY_CALL_ID.fullmatch(call_id) and _AGY_TOOL.fullmatch(tool) and raw.startswith("{")):
                    continue
                try:
                    args = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                calls.setdefault(call_id, ToolCall(tool=tool, args=args if isinstance(args, dict) else {}))
    finally:
        con.close()
    return list(calls.values())


#: agy's CLI log (``--log-file``; glog format, local time, no year) reports every model call it will retry:
#: ``I0922 14:53:40.358454  284 run.go:395] Run: attempt 1 failed (Error 503, ...), retrying in 4s``
_AGY_RETRY = re.compile(r"^[IWEF](?P<md>\d{4}) (?P<hms>\d\d:\d\d:\d\d)(?P<frac>\.\d+)?\s.*\battempt \d+ failed "
                        r"\((?P<why>.*)\), retrying in (?P<wait>[0-9.hmsuµn]+)\s*$")
_GO_DURATION = re.compile(r"([0-9.]+)(h|ms|m|s|us|µs|ns)")
_GO_UNIT = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 1e-3, "us": 1e-6, "µs": 1e-6, "ns": 1e-9}


def agy_retries(log_path: Path) -> list[tuple[float, float, str]]:
    now = time.time()
    year = time.localtime(now).tm_year
    out: list[tuple[float, float, str]] = []
    try:
        lines = log_path.read_text(errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        m = _AGY_RETRY.match(line)
        if not m:
            continue
        t = time.mktime(time.strptime(f"{year}{m['md']} {m['hms']}", "%Y%m%d %H:%M:%S")) + float(m["frac"] or 0.0)
        if t > now + 86_400:   # a log written last December, read on New Year's Day
            t = time.mktime(time.strptime(f"{year - 1}{m['md']} {m['hms']}", "%Y%m%d %H:%M:%S")) + float(m["frac"] or 0.0)
        out.append((t, sum(float(n) * _GO_UNIT[u] for n, u in _GO_DURATION.findall(m["wait"])), m["why"]))
    return out


def agy_error(stderr: str) -> dict[str, Any]:
    """agy's typed verdict on a turn that died: the ``AGY_ERROR: {...}`` stderr line (``status``, ``error_code``, ``retryable``)."""
    for line in reversed(stderr.splitlines()):
        if line.startswith("AGY_ERROR:"):
            with contextlib.suppress(json.JSONDecodeError):
                got = json.loads(line[len("AGY_ERROR:"):])
                return got if isinstance(got, dict) else {}
    return {}


def usage_from_agy(env: dict[str, Any], model: str) -> Usage:
    """The envelope's tokens, priced at the Gemini list rate.  ``input_tokens`` EXCLUDES
    ``cache_read_tokens`` (total prompt is their sum); ``output_tokens`` CONTAINS ``thinking_tokens``
    (moved to ``thoughts_tokens``, billed once)."""
    u = env.get("usage") or {}
    thinking = int(u.get("thinking_tokens") or 0)
    cached = int(u.get("cache_read_tokens") or 0)
    out = Usage(
        backend="agy", model=model,
        input_tokens=int(u.get("input_tokens") or 0) + cached,
        output_tokens=max(0, int(u.get("output_tokens") or 0) - thinking),
        cached_tokens=cached,
        thoughts_tokens=thinking,
        latency_ms=int(float(env.get("duration_seconds") or 0.0) * 1000),
    )
    out.cost_usd = estimate_cost("gemini", model, out)
    return out


def _key_pool() -> KeyPool:
    from codeverse3d.models.gemini import shared_pool

    return shared_pool(list(get_settings().gemini_api_keys))


def _pool_outcome(end: SessionEnd, ok: bool, died: dict[str, Any]) -> str:
    code = died.get("error_code")
    if code in (401, 403) or "API_KEY_INVALID" in json.dumps(died):
        return "dead"
    if end.rate_limited:
        return "429"
    return "ok" if ok else "5xx" if end.transient else "skip"


class AntigravityAgent(_CliAgent):
    """``agy:<gemini model>`` (default ``gemini-4-argon``) in agy's Gemini-API mode: one pool key and a
    HOME of its own (API-mode settings + the 3dcode MCP server, deleted after the session), usage priced."""

    kind = "agy"
    cli_label = "agy"

    def __init__(self, model: str, binary: str | None = None):
        self.model = model or DEFAULT_AGY_MODEL
        self.binary = binary or get_settings().binaries.agy_cli

    def available(self) -> tuple[bool, str]:
        if not get_settings().gemini_api_keys:
            return False, "agy runs on Gemini API keys only: none configured (GEMINI_API_KEYS)"
        return super().available()

    def served_model(self) -> str:
        return api_model_effort(self.model)[0]

    def effort_for(self, job: AgentJob) -> str:
        return api_model_effort(self.model, job.effort)[1]

    def model_args(self, job: AgentJob) -> list[str]:
        return ["--model", f"{AGY_API_SCHEME}{self.served_model()}", "--effort", self.effort_for(job)]

    def build_argv(self, s: Session) -> list[str]:
        """No ``--print``: agy reads the prompt from piped stdin.  ``--print-timeout`` sits a minute past
        the watchdog's hard limit so agy's own clock never cuts off a session the watchdog still allows."""
        minutes = max(1, int((s.job.timeout_s + HARD_GRACE_S) // 60) + 1)
        return [self.binary, "--dangerously-skip-permissions",
                "--print-timeout", f"{minutes}m", "--output-format", "json",
                "--add-dir", str(s.ws.root), "--disable-slash-commands", "--log-file", str(s.traj.dir / AGY_LOG),
                *self.model_args(s.job)]

    def build_home(self, s: Session) -> Path:
        home = get_settings().cache_dir / "agy_homes" / f"{s.traj.dir.name}-{os.getpid()}-{time.time_ns()}"
        (home / AGY_SETTINGS).parent.mkdir(parents=True, exist_ok=True)
        (home / AGY_SETTINGS).write_text(json.dumps(AGY_API_SETTINGS))
        servers: dict[str, Any] = {}
        if s.job.spatial_tools:
            cmd = mcp_command_for(s.ws, s.job)
            servers[MCP_SERVER_NAME] = {"command": cmd[0], "args": cmd[1:], "env": {"HOME": str(Path.home())}}
        (home / AGY_MCP_CONFIG).parent.mkdir(parents=True, exist_ok=True)
        (home / AGY_MCP_CONFIG).write_text(json.dumps({"mcpServers": servers}, indent=2))
        return home

    def build_env(self, s: Session, *, key: str, home: Path) -> dict[str, str]:
        env = hardened_env(s.ws, s.job)
        env["GEMINI_API_KEY"] = key
        env["HOME"] = str(home)
        # the agent's own shell inherits the fake HOME: keep puppeteer on the real Chrome cache
        env.setdefault("PUPPETEER_CACHE_DIR", str(Path.home() / ".cache" / "puppeteer"))
        return env

    def _record_trace(self, s: Session, home: Path, conversation_id: str) -> int:
        db = home / AGY_CONVERSATIONS / f"{conversation_id}.db" if conversation_id else None
        if db is None or not db.is_file():
            s.notes.append("no agy conversation record found for this session: tool trace missing")
            return 0
        try:
            return record_tool_calls(s, agy_tool_calls(db), source="agy conversation db")
        except Exception as e:  # noqa: BLE001 — the trace is telemetry; a session must never fail on it
            log.debug("agy conversation %s unreadable: %s", db, e)
            return 0

    def run(self, job: AgentJob) -> AgentResult:
        """``usage`` comes from agy's envelope only (a killed session books nothing).  No retry loop: agy
        retries its own model calls, and a transient death goes back to the round loop's one retry with a fresh key."""
        ok, why = self.available()
        s = begin_session(job, self.kind)
        home, lease = None, None
        try:
            if not ok:
                return failed(s, "error", why)
            pool = _key_pool()
            try:
                key, lease = pool.lease_session_key(get_settings().cache_dir / "slots" / "agy_keys")
            except KeyPoolExhausted as e:  # every key throttled for the whole wait budget
                return failed(s, "budget", f"Gemini key pool exhausted: {e}")
            home = self.build_home(s)
            effort = self.effort_for(job)
            proc = invoke(s, self.build_argv(s), self.build_env(s, key=key, home=home), prompt=_compose_prompt_agy(s),
                          activity=[home / d for d in AGY_STEP_DIRS], model=self.model, effort=effort, key_tail=key[-4:])
            ended = time.time()
            env = parse_agy_json(proc.stdout)
            n_calls = self._record_trace(s, home, str((env or {}).get("conversation_id") or ""))
            retries = agy_retries(s.traj.dir / AGY_LOG)
            served = self.served_model()
            usage = usage_from_agy(env, served) if env else Usage(backend=self.kind, model=served)
            usage.latency_ms = usage.latency_ms or int(proc.duration_s * 1000)
            text = str(env.get("response", "")) if env else proc.stdout.strip()
            errors: list[str] = []
            if env is None:
                s.notes.append("no JSON envelope from agy; usage unknown (zeros), response taken from plain stdout")
            died = agy_error(proc.stderr)
            said = (json.dumps(died), str((env or {}).get("error") or ""), proc.stderr)
            ok = False
            reason = str((env or {}).get(AGY_FINISHED_REASON) or "")   # 1.4.11+: why the turn ended early, "" = normally
            if reason == "print_timeout" or (not reason and AGY_PRINT_TIMEOUT in proc.stderr):   # agy's own clock stopped a turn in progress
                proc = dataclasses.replace(proc, timed_out=True, killed_reason="agy_print_timeout")
                errors.append(f"agy's own --print-timeout stopped the turn after {proc.duration_s:.0f}s")
            elif proc.timed_out:
                errors.append(watchdog_error(proc))
            elif proc.rc != 0 or (env is not None and str(env.get("status", "SUCCESS")).upper() not in ("SUCCESS", "OK")):
                errors.append(f"rc={proc.rc}; status={(env or {}).get('status')}; error={(env or {}).get('error', '')}; "
                              f"stderr tail: {proc.stderr[-1500:]}")
            elif not text.strip():
                errors.append("empty response")
            else:
                ok = True
            end = classify_end(proc, ok=ok, text=text, said=said, provider_failed=bool(died.get("retryable")))
            pool.report(key, _pool_outcome(end, ok, died))
            usage.tool_calls = n_calls
            return finish_session(
                s, ok=ok, exit_reason=end.exit_reason, text=text, usage=usage, tool_calls=n_calls,
                turns=int((env or {}).get("num_turns") or 0), errors=errors, transient=end.transient, quota=end.quota, stalled=end.stalled,
                provider_wait_s=provider_wait([(t, b) for t, b, _ in retries], ended),
                rc=proc.rc, killed_reason=proc.killed_reason, agy_error=died, effort=effort, finished_reason=reason,
                conversation_id=(env or {}).get("conversation_id", ""), num_turns=(env or {}).get("num_turns", 0),
            )
        finally:
            if home is not None:
                shutil.rmtree(home, ignore_errors=True)
            KeyPool.end_lease(lease)
            release_session(s)


def _compose_prompt_agy(s: Session) -> str:
    head = f"Workspace root: {s.ws.root}\nWork ONLY inside it, using your file tools."
    if (s.ws.root / "AGENTS.md").is_file():
        head += " Read AGENTS.md there first and follow it."
    return f"{head}\n\n{_compose_prompt(s.job)}"
