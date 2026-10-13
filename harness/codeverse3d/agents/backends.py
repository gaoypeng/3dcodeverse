"""The three vendor CLI backends: ``claude-code:<model>``, ``codex:<model>``, ``agy:<model>``.

Each one runs ONE headless session with the prompt on stdin (``cli_common.invoke``), reads its
CLI's own wire format back into an ``AgentResult`` and leaves retrying to the CLI itself: the
harness never loops over a session (one round-level transport retry lives in
``tracks/lifecycle``).  Google's models go through ``agy`` — Antigravity in Gemini-API mode,
one key per session from the shared pool (see the antigravity section).
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
    is_transient_failure,
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
    """What the vendor backends share.

    Everything that MATTERS about a CLI backend differs — argv vocabulary, JSON wire
    format, usage/pricing shape, key handling — and stays in the subclass.  What they
    share is their id and "is the binary there?", which was four and three verbatim
    copies until 2026-08-28.  Subclasses set ``kind`` / ``cli_label`` and assign
    ``model`` / ``binary`` in __init__.
    """

    kind: str
    cli_label: str
    model: str
    binary: str

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.model}"

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
#: ``--allowedTools``.  "Skill" is claude-code 2.1's model-invoked skill tool: without it
#: the bundles the harness materialises into ``ws/.claude/skills/`` are listed at session
#: start and then DENIED on activation, which reads in the transcript as the model
#: ignoring them.  It only ever opens files already inside the workspace.
ALLOWED_TOOLS = ("Read", "Edit", "Write", "MultiEdit", "Glob", "Grep", "Skill",
                 "Bash(node:*)", "Bash(python:*)", "Bash(python3:*)", "Bash(ls:*)", f"mcp__{MCP_SERVER_NAME}__*")
#: claude-code's skill-activation tool (2.1.280: input ``{"skill": <name>, "args"?}``)
CLAUDE_SKILL_TOOL = "Skill"
#: A harness session sees the bundles the harness routed into ``<ws>/.claude/skills`` and
#: nothing else.  claude-code 2.1.280 otherwise lists 26 more (2026-09-22 rig): the account-
#: synced skills and plugins (``anthropic-skills:*``, ``deep-research``, …) come with the
#: "user" setting source, so ``--setting-sources project`` drops them — and with them the
#: owner's ~/.claude/settings.json (hooks, env, effortLevel): a session no longer inherits
#: whoever runs the harness, and its effort is stated instead (``Settings.agents.claude_effort``,
#: default "xhigh" — what sessions ran at while they inherited the owner's settings).  The bundled skills go with ``CLAUDE_CODE_DISABLE_BUNDLED_SKILLS`` (build_env);
#: ``design`` and ``doctor`` survive that switch by design and are hidden by name through the
#: documented ``skillOverrides`` setting.  Checked against a local fake API and in one live
#: subscription session (2026-09-22): the init event lists exactly the routed bundles, and
#: subscription auth does not need the user setting source.
CLAUDE_SETTING_SOURCES = "project"
CLAUDE_SETTINGS = {"skillOverrides": {"design": "off", "doctor": "off"}}


def parse_claude_json(stdout: str) -> dict[str, Any] | None:
    """The ``type == "result"`` envelope, else the one JSON document ``--output-format
    json`` printed.  A stream (a stream-json array, or the JSONL ``--output-format
    stream-json`` writes) without its result event is no envelope: its last line is an
    assistant turn of a session that never finished."""
    env = find_json_object(stdout, lambda d: d.get("type") == "result")
    if env is not None or stdout.lstrip().startswith("["):
        return env
    if sum(1 for line in stdout.splitlines() if line.lstrip().startswith("{")) > 1:
        return None
    return find_json_object(stdout, lambda d: True)


class ClaudeStream:
    """Folded view over ``--output-format stream-json``, fed live: the tool calls the
    session made, the skill index its ``init`` event reported (the list the model was
    shown), each message's usage block (``messages``: the last copy per message id — what a
    session killed before its result event billed), the CLI's own ``api_retry`` events
    (``retries``: ``(t, announced back-off s, HTTP status)``) and when every other event
    arrived (``progress``) — the two ``cli_common.provider_wait`` reads.

    Since 2026-09-22 the backend streams instead of printing one envelope: the envelope
    (the last, ``type == "result"`` line) is unchanged, and the lines before it are the
    only record of which skills the session activated (docs/SKILLS.md §9 — "the logs
    contain zero occurrences of any c3d- name" under ``--output-format json``)."""

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []
        self.skills: list[str] | None = None
        self._at: dict[str, int] = {}  # tool_use id → index in calls, for its tool_result
        self.messages: dict[str, dict[str, Any]] = {}
        self.served = ""
        self.retries: list[tuple[float, float, int]] = []
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
            # {"attempt", "max_retries", "retry_delay_ms", "error_status": 529, "error": "overloaded"}
            self.retries.append((time.time(), float(ev.get("retry_delay_ms") or 0) / 1000.0, int(ev.get("error_status") or 0)))
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
        """What a session that never printed its result event billed: every message's own
        usage block, summed.  The input side is exact (a message knows its prompt when it
        starts); ``output_tokens`` is a FLOOR — stream-json repeats the block each message
        started with, before its output was counted (a finished 2026-09-22 session: 154 of
        30 106).  Priced from the table: the ``total_cost_usd`` event never came."""
        if not self.messages:   # died before its first reply: nothing billed, nothing to price
            return Usage(backend="claude-code", model=model)
        keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
        total = {k: sum(int(u.get(k) or 0) for u in self.messages.values()) for k in keys}
        usage = usage_from_envelope({"usage": total}, self.served or model)
        usage.cost_usd = (estimate_cost("anthropic", usage.model, usage)
                          + cache_write_surcharge("anthropic", usage.model, total["cache_creation_input_tokens"]))
        return usage


def usage_from_envelope(env: dict[str, Any], model: str) -> Usage:
    """``input_tokens`` is the TOTAL prompt (uncached + cache reads + cache writes), the
    convention pricing.py and anthropic.py share.  The envelope's own ``input_tokens`` is the
    uncached part only, and CostBucket clamps ``cached`` to ``input``: every cache-read token
    past it and every cache-write token used to vanish from the ledger."""
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


def claude_end(proc: CompletedProc, env: dict[str, Any] | None, stream: ClaudeStream, text: str) -> SessionEnd:
    """How a ``claude -p`` process ended (``cli_common.classify_end`` with claude's evidence: its
    ``api_retry`` events, its result envelope) — the harness session and the bench one-shot alike."""
    sub = str((env or {}).get("subtype", ""))
    if proc.timed_out:
        return classify_end(proc, ok=False, text=text, provider_retried=bool(stream.retries))
    if env is None or proc.rc != 0:
        # a 529 / overloaded exit is the provider's, not the task's (AgentResult.transient)
        return classify_end(proc, ok=False, text=text, said=(proc.stderr, proc.stdout))
    if env.get("is_error") or sub.startswith("error"):
        # the turn cap is the task's budget, never the provider's
        return SessionEnd("budget") if "max_turns" in sub else classify_end(proc, ok=False, text=text, said=(text,))
    return classify_end(proc, ok=True, text=text)


class ClaudeCodeAgent(_CliAgent):
    kind = "claude-code"
    cli_label = "claude"

    def __init__(self, model: str, binary: str | None = None):
        self.model = model
        self.binary = binary or get_settings().binaries.claude_cli

    def build_argv(self, s: Session) -> list[str]:
        """``-p`` with no prompt argument: the prompt comes on stdin, byte for byte."""
        job = s.job
        # stream-json (+ the --verbose it requires in -p mode): same final envelope, plus the
        # tool calls the skill read probe reads as ground truth (ClaudeStream)
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
            end = claude_end(proc, env, stream, text)
            if proc.timed_out:
                errors.append(watchdog_error(proc))
            elif env is None or proc.rc != 0:
                errors.append(f"rc={proc.rc}; no result envelope; stderr tail: {proc.stderr[-1500:]}")
            elif end.exit_reason != "completed":
                errors.append(f"claude reported {sub or 'is_error'}: {text[-800:]}")
            return finish_session(
                s, ok=end.exit_reason == "completed", exit_reason=end.exit_reason, text=text, usage=usage,
                tool_calls=n_calls, turns=turns, errors=errors, transient=end.transient, quota=end.quota,
                provider_wait_s=provider_wait([(t, b) for t, b, _ in stream.retries], ended, stream.progress),
                rc=proc.rc, killed_reason=proc.killed_reason, num_turns=turns,
                session_id=(env or {}).get("session_id", ""), subtype=(env or {}).get("subtype", ""),
                model_usage=(env or {}).get("modelUsage", {}),
                usage_from="envelope" if env else "stream messages" if stream.messages else "none",
            )
        finally:
            release_session(s)


# ===================================================================== codex
#: ``-c`` for every codex session: the routed bundles in ``<ws>/.agents/skills`` only.  codex
#: 0.155.1 otherwise lists its five bundled ``.system`` skills (imagegen, openai-docs,
#: plugin-creator, skill-creator, skill-installer) next to them — gone with this switch
#: (``codex debug prompt-input``, 2026-09-22).  User-level roots (``~/.codex/skills``,
#: ``~/.agents/skills``) have no stable switch; none exist on this machine.
CODEX_SKILL_OVERRIDES = ("-c", "skills.bundled.enabled=false")
_TOOL_ITEMS = ("command_execution", "file_change", "mcp_tool_call", "web_search", "tool_call")
#: the item fields a tool trace keeps: what was run, never what came back.  codex has no
#: skill tool — it lists skills in its prompt and opens SKILL.md with a shell command, so
#: the command string IS the activation record (0.155.1)
_TRACE_KEYS = ("command", "server", "tool", "arguments", "changes", "query")

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


#: A session killed before its turn completed books no usage (codex reports it per TURN and
#: ``--ephemeral`` leaves no rollout), so its cost is ESTIMATED from what the stream shows: every
#: completed item is one more request that re-reads the context, which grows by that item.
#: Constants fitted on the 356 completed codex sessions on record (2026-09-23): codex's own
#: system prompt + tool schemas ≈ 20k tokens, ≈ 1k tokens per returned image, 4 chars/token,
#: output ≈ 1.13 % of input.  Estimated input / actual: median 1.01, p10–p90 0.66–1.35;
#: estimated $ / actual $: median 0.93, p10–p90 0.62–1.34.
_EST_SYSTEM_TOKENS = 20_000
_EST_IMAGE_TOKENS = 1_000
_EST_CHARS_PER_TOKEN = 4.0
_EST_OUTPUT_SHARE = 0.0113
_B64_DATA = re.compile(r'"data"\s*:\s*"[A-Za-z0-9+/=]{200,}"')


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
        self.calls: list[ToolCall] = []
        #: (text tokens, images) each completed item added to the context — the estimate's input
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
        """What a session whose turn never completed most likely spent (see ``_EST_*``):
        one request per completed item plus the one in flight, each reading the whole context
        so far; everything but each request's new content is a cache hit."""
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
    """How a ``codex exec --json`` process ended (``cli_common.classify_end`` with codex's evidence:
    its own error events — a dropped stream, a 5xx it gave up on) — the harness session and the
    bench one-shot alike."""
    ok = not proc.timed_out and proc.rc == 0 and events.n_events > 0 and not (events.errors and events.turns_completed == 0)
    return classify_end(proc, ok=ok, text=text, said=(*events.errors, proc.stderr),
                        provider_retried=is_transient_failure(*events.errors))


class CodexAgent(_CliAgent):
    kind = "codex"
    cli_label = "codex"

    def __init__(self, model: str, binary: str | None = None, reasoning_effort: str | None = None):
        self.model, self.reasoning_effort = split_model_effort(model, reasoning_effort)
        self.binary = binary or get_settings().binaries.codex_cli

    def build_argv(self, s: Session) -> list[str]:
        """``-`` as the prompt: codex exec reads it from stdin."""
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
        """``usage`` arrives per TURN (``turn.completed``), and ``--ephemeral`` leaves no rollout,
        so a session killed mid-turn (1 of 357 recorded codex sessions) books an ESTIMATE from its
        stream (:meth:`CodexEvents.estimated_usage`, ``usage_estimated``).  ``provider_wait_s`` stays 0.0: codex retries a 5xx inside its HTTP
        client without an event (two fake 503s, 2026-09-22: nothing on the stream)."""
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
            # no turn completed but requests were made (items, or a watchdog kill): an estimate,
            # flagged on the result and booked as source="estimate" — never a silent $0
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
                turns=events.turns_completed, errors=errors, transient=end.transient, quota=end.quota,
                rc=proc.rc, killed_reason=proc.killed_reason, thread_id=events.thread_id,
                turns_completed=events.turns_completed, usage_raw=events.usage_raw, usage_estimated=estimated,
            )
        finally:
            release_session(s)


# ===================================================================== antigravity

def api_model_effort(model: str) -> tuple[str, str]:
    """``gemini-4-argon-low`` → ``('gemini-4-argon', 'low')``; a bare id runs at ``AGY_API_EFFORT``."""
    for effort in EFFORTS:
        if model.endswith(f"-{effort}"):
            return model[: -len(effort) - 1], effort
    return model, AGY_API_EFFORT


def parse_agy_json(stdout: str) -> dict[str, Any] | None:
    """agy's envelope is the JSON line carrying ``response`` or ``status``."""
    return find_json_object(stdout, lambda d: "response" in d or "status" in d)


#: the per-session CLI log agy writes into the trajectory dir (``--log-file``)
AGY_LOG = "agy.log"
#: where agy keeps each conversation under its HOME: one SQLite file named by the envelope's
#: ``conversation_id``, a ``steps`` table whose ``step_payload`` is protobuf
AGY_CONVERSATIONS = Path(".gemini") / "antigravity-cli" / "conversations"
#: agy's Gemini-API mode (1.2+, checked live on 1.3.4, 2026-10-13): ``modelProvider: "gemini"``
#: in its settings plus ``GEMINI_API_KEY`` in the env — no sign-in, billed to the key, and the
#: catalogue is the API's.  Without the setting the key alone is ignored and the CLI asks for a
#: login.  It is the ONLY way the harness runs agy (owner, 2026-10-13): no subscription sign-in,
#: no non-Gemini model.
AGY_SETTINGS = Path(".gemini") / "antigravity-cli" / "settings.json"
AGY_API_SETTINGS = {"modelProvider": "gemini"}
#: in API mode ``--model gemini-api://<id>`` sends ``<id>`` to the API verbatim — any model the
#: key serves, listed by ``agy models`` or not (gemini-4-argon) — and ``--effort`` sets its
#: thinking level (low → 1, high → 3 in the CLI log, 2026-10-13)
AGY_API_SCHEME = "gemini-api://"
#: the effort an API-mode id without a ``-low/-medium/-high`` suffix runs at: the Gemini API's
#: own default thinking level
AGY_API_EFFORT = "high"
#: the model an ``agy`` generator with no model runs (owner, 2026-10-13)
DEFAULT_AGY_MODEL = "gemini-4-argon"
#: agy's ONLY MCP config is per user (``~/.gemini/config/mcp_config.json``, the shape
#: ``agy mcp add`` writes) — so an API-key session gets a HOME of its own, and this file in it
AGY_MCP_CONFIG = Path(".gemini") / "config" / "mcp_config.json"
#: a tool call inside a step payload is three consecutive strings — its id, the tool name,
#: the JSON arguments (``call_4419682`` · ``view_file`` · ``{"AbsolutePath": ...}``), read out
#: of recorded conversations; no schema needed, and a tool result step that echoes the
#: call is folded by the id
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
    """Every printable UTF-8 string in a protobuf message, nested messages included, in
    wire order — a schema-free walk; raises ValueError on bytes that are not protobuf."""
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

    agy 1.2.2 has no skill tool: its prompt lists the skills and the model opens
    ``SKILL.md`` with ``view_file``, so the file path IS the activation record."""
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


#: agy 1.2.8's CLI log (``--log-file``; glog format, LOCAL time, no year) reports every failed
#: model call it is about to retry: ``I0922 14:53:40.358454  284 run.go:395] Run: attempt 1 failed
#: (Error 503, Message: ..., Status: UNAVAILABLE, Details: []), retrying in 4s``
_AGY_RETRY = re.compile(r"^[IWEF](?P<md>\d{4}) (?P<hms>\d\d:\d\d:\d\d)(?P<frac>\.\d+)?\s.*\battempt \d+ failed "
                        r"\((?P<why>.*)\), retrying in (?P<wait>[0-9.hmsuµn]+)\s*$")
_GO_DURATION = re.compile(r"([0-9.]+)(h|ms|m|s|us|µs|ns)")
_GO_UNIT = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 1e-3, "us": 1e-6, "µs": 1e-6, "ns": 1e-9}


def agy_retries(log_path: Path) -> list[tuple[float, float, str]]:
    """``(t, announced back-off s, why)`` for every retried model call in one agy CLI log."""
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
    """agy's own verdict on a turn that died (1.2.x, exit code 3): the ``AGY_ERROR: {...}`` line —
    ``status``, ``error_code``, ``retryable`` — typed, where the envelope has only prose."""
    for line in reversed(stderr.splitlines()):
        if line.startswith("AGY_ERROR:"):
            with contextlib.suppress(json.JSONDecodeError):
                got = json.loads(line[len("AGY_ERROR:"):])
                return got if isinstance(got, dict) else {}
    return {}


def usage_from_agy(env: dict[str, Any], model: str) -> Usage:
    """The envelope's tokens, priced at the Gemini list rate.  agy 1.3.4 splits
    neither way the pricing convention does: ``input_tokens`` EXCLUDES ``cache_read_tokens`` (a
    live 18-minute session, 2026-10-13: 582 179 input against 10 980 664 cache reads), so the total
    prompt is their sum; and ``output_tokens`` CONTAINS ``thinking_tokens`` (``total == input +
    output`` on every envelope), so the thinking share moves to ``thoughts_tokens``, billed once."""
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
    """THE process-wide Gemini pool — ``models.gemini.shared_pool``: the same cooldowns and
    dead-key bench the API path uses; a session takes its key with ``session_key`` (no
    in-flight slot: the CLI makes its own calls for minutes)."""
    from codeverse3d.models.gemini import shared_pool

    return shared_pool(list(get_settings().gemini_api_keys))


def _pool_outcome(end: SessionEnd, ok: bool, died: dict[str, Any]) -> str:
    """What one session tells the pool about its key.  A hang, a bad answer or the agent's own
    failure is not the key's fault (``skip``: counters untouched)."""
    code = died.get("error_code")
    if code in (401, 403) or "API_KEY_INVALID" in json.dumps(died):
        return "dead"
    if end.rate_limited:
        return "429"
    return "ok" if ok else "5xx" if end.transient else "skip"


class AntigravityAgent(_CliAgent):
    """``agy:<gemini model>`` (default ``gemini-4-argon``), always in agy's Gemini-API mode: one
    pool key, a HOME of its own under the cache dir holding the API-mode settings and the 3dcode
    MCP server (deleted after the session; the tool trace is read out first), usage priced."""

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
        """The API model id the session is booked under (the effort suffix stripped)."""
        return api_model_effort(self.model)[0]

    def model_args(self) -> list[str]:
        name, effort = api_model_effort(self.model)
        return ["--model", f"{AGY_API_SCHEME}{name}", "--effort", effort]

    def build_argv(self, s: Session) -> list[str]:
        """No ``--print``: agy reads the prompt from a piped stdin, byte for byte, and still
        answers as print mode (``--print -`` would send the prompt "-").  ``--log-file`` keeps
        its CLI log with the session — the record of the retries it made."""
        minutes = max(1, int(s.job.timeout_s // 60) + 1)
        return [self.binary, "--dangerously-skip-permissions",
                "--print-timeout", f"{minutes}m", "--output-format", "json",
                "--add-dir", str(s.ws.root), "--disable-slash-commands", "--log-file", str(s.traj.dir / AGY_LOG),
                *self.model_args()]

    def build_home(self, s: Session) -> Path:
        """A fresh HOME for one session: the API-mode settings and, when the job has
        spatial tools, the 3dcode MCP server — which keeps the REAL home (the harness reads its
        settings, keys and caches from there)."""
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
        """The session's tool calls, from agy's own conversation database — never fatal.
        Returns how many (agy's JSON envelope carries no tool count of its own)."""
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
        """``usage`` comes from agy's envelope only: a killed session books nothing (its
        conversation database is named by the envelope's id).  ``provider_wait_s`` from its log.
        No retry loop: agy retries its own model calls (waits ≤ 30 s since 1.3.4, stops at once
        on a spent daily quota), and a transient death goes back to the round loop's one retry —
        which takes a fresh key, the 429'd one cooling in the pool."""
        ok, why = self.available()
        s = begin_session(job, self.kind)
        home = None
        try:
            if not ok:
                return failed(s, "error", why)
            pool = _key_pool()
            try:
                key = pool.session_key()
            except KeyPoolExhausted as e:  # every key throttled for the whole wait budget
                return failed(s, "budget", f"Gemini key pool exhausted: {e}")
            home = self.build_home(s)
            proc = invoke(s, self.build_argv(s), self.build_env(s, key=key, home=home), prompt=_compose_prompt_agy(s),
                          model=self.model, key_tail=key[-4:])
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
            if proc.timed_out:
                errors.append(watchdog_error(proc))
            elif proc.rc != 0 or (env is not None and str(env.get("status", "SUCCESS")).upper() not in ("SUCCESS", "OK")):
                errors.append(f"rc={proc.rc}; status={(env or {}).get('status')}; error={(env or {}).get('error', '')}; "
                              f"stderr tail: {proc.stderr[-1500:]}")
            elif not text.strip():
                errors.append("empty response")
            else:
                ok = True
            end = classify_end(proc, ok=ok, text=text, said=said, provider_retried=bool(retries) or bool(died.get("retryable")),
                               provider_failed=bool(died.get("retryable")))
            pool.report(key, _pool_outcome(end, ok, died))
            usage.tool_calls = n_calls
            return finish_session(
                s, ok=ok, exit_reason=end.exit_reason, text=text, usage=usage, tool_calls=n_calls,
                turns=int((env or {}).get("num_turns") or 0), errors=errors, transient=end.transient, quota=end.quota,
                provider_wait_s=provider_wait([(t, b) for t, b, _ in retries], ended),
                rc=proc.rc, killed_reason=proc.killed_reason, agy_error=died,
                conversation_id=(env or {}).get("conversation_id", ""), num_turns=(env or {}).get("num_turns", 0),
            )
        finally:
            if home is not None:
                shutil.rmtree(home, ignore_errors=True)
            release_session(s)


def _compose_prompt_agy(s: Session) -> str:
    """agy has no system-prompt flag; prepend harness instructions + the absolute workspace path."""
    head = f"Workspace root: {s.ws.root}\nWork ONLY inside it, using your file tools."
    if (s.ws.root / "AGENTS.md").is_file():
        head += " Read AGENTS.md there first and follow it."
    return f"{head}\n\n{_compose_prompt(s.job)}"
