"""``gemini-cli:<model>`` — headless Gemini CLI session on a workspace.

argv: ``gemini -p <prompt> -m <model> --approval-mode yolo --skip-trust --output-format json``

* env hardening: host secrets stripped, one pool key as ``GEMINI_API_KEY``,
  ``GEMINI_CLI_SYSTEM_SETTINGS_PATH`` (per session, in the trajectory dir) forcing
  api-key auth + dynamic model configuration (otherwise an unknown model is *silently*
  substituted), ``skills.enabled``, the 3dcode MCP server and ``mcp.allowed``, Node heap
  cap, no self-relaunch.
* tool trace: gemini-cli's own chat record of the session (``~/.gemini/tmp/<project>/
  chats/session-*.jsonl``) is folded into the transcript as ``tool_call`` rows
  (``cli_common.record_tool_calls``) — ``--output-format json`` carries only per-tool
  COUNTS, and ``stream-json`` would cost the per-model ``thoughts`` tokens pricing needs.
* JSON envelope ``{session_id, response, stats:{models:{<m>:{tokens:{prompt,
  input, candidates, cached, thoughts}}}, tools:{totalCalls}}}`` → Usage (+ cost
  via pricing).  ``tokens.prompt`` is the TOTAL prompt size and ``tokens.input``
  the UNCACHED part (``prompt - cached``); ``Usage.input_tokens`` follows the
  pricing convention (total prompt, cached re-priced).  Each served model is
  priced at its own rate.  Requested model absent from ``stats.models`` →
  ``model_substituted``.
* one retry (rotated key when the pool has one, else the same key) on transient
  failures (503 / empty response), two on a quota 429; a throttled/empty pool never raises.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import os
import re
import sqlite3
import time
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from codeverse3d.agents.cli_common import (
    CompletedProc,
    Session,
    ToolCall,
    begin_session,
    deliver_prompt,
    exists_on_path,
    failed,
    find_json_object,
    finish_session,
    hardened_env,
    invoke,
    is_quota_failure,
    is_transient_failure,
    mcp_command_for,
    record_tool_calls,
    release_session,
    tail,
    watchdog_error,
)
from codeverse3d.agents.materialize import MCP_SERVER_NAME, MCP_TOOL_TIMEOUT_MS, codex_mcp_overrides
from codeverse3d.config import get_settings
from codeverse3d.contracts.agent import AgentJob, AgentResult
from codeverse3d.contracts.common import Usage
from codeverse3d.models.pricing import estimate_cost
from codeverse3d.models.retry import KeyPool, KeyPoolExhausted
from codeverse3d.proc import read_jsonl_lenient, run_subprocess

log = logging.getLogger(__name__)

SYSTEM_SETTINGS = {
    # folderTrust must be OFF: with it on, gemini-cli silently disables the workspace
    # .gemini/settings.json mcpServers (even with --skip-trust) → no 3dcode tools.  It is
    # also what keeps the WORKSPACE skill roots visible: 0.53's SkillManager.discoverSkills
    # skips <ws>/.gemini/skills and <ws>/.agents/skills when the folder is not trusted, and
    # isTrustedFolder() is true exactly when folderTrust is off.
    "security": {"auth": {"selectedType": "gemini-api-key"}, "folderTrust": {"enabled": False}},
    "experimental": {"dynamicModelConfiguration": True},
    "general": {"topicUpdateNarration": False},
    # default true in 0.53, but a user's ~/.gemini/settings.json (or an agent-written
    # ws/.gemini/settings.json) could turn it off; this file is merged LAST, so the routed
    # bundles stay indexed and ``activate_skill`` stays registered (2026-09-22)
    "skills": {"enabled": True},
}
#: gemini-cli's skill-activation tool (0.53: ``activate_skill``, arg ``name``); the body
#: comes back in the tool result, so its call — not a file read — is the activation
GEMINI_SKILL_TOOL = "activate_skill"
#: how long a retry waits for a *different* healthy key before reusing the same one
RETRY_KEY_WAIT_S = 10.0
#: attempts allowed when the failure is a quota 429 (transient non-quota failures keep 2)
QUOTA_MAX_ATTEMPTS = 3


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
    same cooldowns and dead-key bench the API path uses (a session takes its key with
    ``session_key``: no in-flight slot).  This module used to build its own
    ``KeyPool(keys)`` with library defaults, so the CLI agent and the API path scheduled
    the same 22 keys under two different quotas (found by review 2026-08-28)."""
    from codeverse3d.models.gemini import shared_pool

    return shared_pool(keys)


def write_system_settings(path: Path | None = None, *, mcp_command: list[str] | None = None) -> Path:
    """Write the gemini-cli system-settings json (api-key auth + dynamic models) and return its path.

    gemini-cli applies this file LAST and ``mcp.allowed`` REPLACES rather than merges, so the
    3dcode server and the allow-list belong here, not in the agent-writable
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
    """The CLI prints one (indented, multi-line) JSON object, possibly after log noise."""
    return find_json_object(stdout, lambda d: True)


def gemini_chat_records(ws_root: Path, since: float, env: Mapping[str, str] | None = None) -> list[Path]:
    """gemini-cli's OWN record of the sessions it ran in ``ws_root`` since ``since`` (epoch s).

    0.53 appends every session to ``<home>/.gemini/tmp/<project>/chats/session-<ts>-<id8>.jsonl``
    (``<home>`` is ``GEMINI_CLI_HOME`` or ``~``) and names the project dir's workspace in its
    ``.project_root``.  Found by that marker plus mtime rather than by ``session_id``: a
    session the watchdog killed printed no JSON envelope, and its record is still on disk.
    Sessions on one workspace are serialised (``cli_common.EXCLUSIVE_KINDS``), so every
    record written after ``since`` is this session's."""
    home = Path((env if env is not None else os.environ).get("GEMINI_CLI_HOME") or Path.home())
    want = {str(ws_root), str(Path(ws_root).resolve())}
    for marker in (home / ".gemini" / "tmp").glob("*/.project_root"):
        try:
            if marker.read_text().strip() not in want:
                continue
        except OSError:
            continue
        return sorted(p for p in (marker.parent / "chats").rglob("session-*.json*")
                      if p.is_file() and p.stat().st_mtime >= since)
    return []


def gemini_tool_calls(records: Iterable[Path]) -> list[ToolCall]:
    """Every tool call in those chat records, once per call id (the record is append-only:
    a message is re-written as it progresses, and the last copy wins)."""
    calls: dict[str, ToolCall] = {}
    for path in records:
        for row in read_jsonl_lenient(path, dicts_only=True):
            update = row.get("$set") if isinstance(row.get("$set"), dict) else {}
            for msg in (row, *(update.get("messages") or [])):
                for tc in (msg.get("toolCalls") if isinstance(msg, dict) else None) or []:
                    if not isinstance(tc, dict):
                        continue
                    name = str(tc.get("name") or "")
                    args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
                    calls[str(tc.get("id") or f"{path.name}#{len(calls)}")] = ToolCall(
                        tool=name, args=args, skill=str(args.get("name") or "") if name == GEMINI_SKILL_TOOL else "",
                        failed=tc.get("status") in ("error", "cancelled"))
    return list(calls.values())


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
        c = estimate_cost("gemini", str(name), part)
        if c == 0.0 and name != model and (part.input_tokens or part.output_tokens):
            c = estimate_cost("gemini", model, part)  # utility model missing from the price table
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
            return pool.session_key(exclude=exclude or None, timeout_s=RETRY_KEY_WAIT_S)
        except KeyPoolExhausted:
            continue
    return None


class _CliAgent:
    """What all four vendor backends share.

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


class GeminiCliAgent(_CliAgent):
    kind = "gemini-cli"
    cli_label = "gemini"

    def __init__(self, model: str, binary: str | None = None):
        self.model = model
        self.binary = binary or get_settings().binaries.gemini_cli

    def available(self) -> tuple[bool, str]:
        ok, why = super().available()
        if not ok:
            return ok, why
        if not get_settings().gemini_api_keys:
            return False, "no Gemini API keys configured"   # the only backend with a key pool
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
                key = pool.session_key()   # no in-flight slot: the CLI makes its own calls
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
                started, env = time.time(), self.build_env(s, key)
                proc = invoke(s, self.build_argv(prompt), env, prompt=prompt, attempt=attempts,
                              soft_timeout_s=next_soft, model=self.model, key_tail=key[-4:])
                self._record_trace(s, started, env)
                outcome = self._interpret(s, proc)
                usage_total = usage_total + outcome["usage"]
                # the pool is shared with every API call: a hang, a substituted model or the
                # agent's own failure is not the key's fault ("skip" = counters untouched)
                pool.report(key, "429" if outcome["quota"] else "ok" if outcome["ok"]
                            else "5xx" if outcome["transient"] else "skip")
                # a per-minute 429 on the single key a CLI process holds clears within the
                # rotated retry's wait; ab_fewer_turns (2026-08-29) lost 4 of 10 cells to
                # two 429s in a row, so a quota failure gets one more rotated attempt
                max_attempts = QUOTA_MAX_ATTEMPTS if outcome["quota"] else 2
                if outcome["ok"] or outcome["exit_reason"] in ("timeout", "model_substituted") or not outcome["transient"] or attempts >= max_attempts:
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
            # turns stays 0: gemini-cli's stats carry tools.totalCalls only, no turn count
            return finish_session(
                s, ok=outcome["ok"], exit_reason=outcome["exit_reason"], text=outcome["text"],
                usage=usage_total, tool_calls=usage_total.tool_calls, errors=outcome["errors"],
                transient=bool(outcome["transient"]) and not outcome["ok"],
                attempts=attempts, rc=proc.rc, killed_reason=proc.killed_reason, session_id=outcome.get("session_id", ""),
            )
        finally:
            release_session(s)

    def _record_trace(self, s: Session, started: float, env: Mapping[str, str]) -> None:
        """This attempt's tool calls, from gemini-cli's own chat record — never fatal."""
        try:
            records = gemini_chat_records(s.ws.root, started, env)
            if records:
                record_tool_calls(s, gemini_tool_calls(records), source="gemini-cli chat record")
            else:
                s.notes.append("no gemini-cli chat record found for this attempt: tool trace missing")
        except Exception as e:  # noqa: BLE001 — the trace is telemetry; a session must never fail on it
            log.debug("gemini-cli chat record unreadable: %s", e)

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
            # the CLI retries 503s itself, with backoff, and never gives up before our wall:
            # measured 2026-09-07, 8-17 "Attempt N failed with status 503" per 12-minute
            # session, nothing produced.  That is a storm, not the task.
            out["transient"] = not text.strip() and is_transient_failure(proc.stderr)
            if out["transient"]:
                errors.append(f"{proc.stderr.count('status 503')} x 503 inside the CLI's own retry loop before the wall; nothing produced")
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
    """gemini-cli and codex have no system-prompt flag: prepend ``system_append``."""
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
    """Folded view over ``--output-format stream-json``: the tool calls the session made,
    and the skill index its ``init`` event reported (the list the model was shown).

    Since 2026-09-22 the backend streams instead of printing one envelope: the envelope
    (the last, ``type == "result"`` line) is unchanged, and the lines before it are the
    only record of which skills the session activated (docs/SKILLS.md §9 — "the logs
    contain zero occurrences of any c3d- name" under ``--output-format json``)."""

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []
        self.skills: list[str] | None = None
        self._at: dict[str, int] = {}  # tool_use id → index in calls, for its tool_result

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
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            self.skills = [str(x) for x in ev.get("skills") or []]
        elif ev.get("type") in ("assistant", "user"):
            for block in (ev.get("message") or {}).get("content") or []:
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


class ClaudeCodeAgent(_CliAgent):
    kind = "claude-code"
    cli_label = "claude"

    def __init__(self, model: str, binary: str | None = None):
        self.model = model
        self.binary = binary or get_settings().binaries.claude_cli

    def build_argv(self, s: Session, prompt: str) -> list[str]:
        job = s.job
        # stream-json (+ the --verbose it requires in -p mode): same final envelope, plus the
        # tool calls the skill read probe reads as ground truth (ClaudeStream)
        argv = [self.binary, "-p", prompt, "--output-format", "stream-json", "--verbose",
                "--dangerously-skip-permissions",
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
            stream = ClaudeStream()
            proc = invoke(s, self.build_argv(s, prompt), self.build_env(s), prompt=prompt, on_stdout=stream.feed)
            record_tool_calls(s, stream.calls, source="claude-code stream-json", skills_index=stream.skills)
            env = parse_claude_json(proc.stdout)
            usage = usage_from_envelope(env, self.model) if env else Usage(backend=self.kind, model=self.model)
            usage.latency_ms = usage.latency_ms or int(proc.duration_s * 1000)
            text = str((env or {}).get("result") or "")
            turns = int((env or {}).get("num_turns") or 0)
            errors: list[str] = []
            transient = False
            if proc.timed_out:
                reason, ok = "timeout", False
                errors.append(watchdog_error(proc))
                transient = not text.strip() and is_transient_failure(proc.stderr)
            elif env is None or proc.rc != 0:
                reason, ok = "error", False
                errors.append(f"rc={proc.rc}; no result envelope; stderr tail: {tail(proc.stderr, 1500)}")
                # a 529 / overloaded exit is the provider's, not the task's (AgentResult.transient)
                transient = is_transient_failure(proc.stderr, proc.stdout)
                if transient and is_quota_failure(proc.stderr):
                    reason = "budget"
            elif env.get("is_error") or str(env.get("subtype", "")).startswith("error"):
                ok = False
                sub = str(env.get("subtype", ""))
                reason = "budget" if "max_turns" in sub else "error"
                errors.append(f"claude reported {sub or 'is_error'}: {tail(text, 800)}")
            else:
                reason, ok = "completed", True
            return finish_session(
                s, ok=ok, exit_reason=reason, text=text, usage=usage, tool_calls=max(turns - 1, 0), turns=turns,
                errors=errors, transient=transient, rc=proc.rc, killed_reason=proc.killed_reason, num_turns=turns,
                session_id=(env or {}).get("session_id", ""), subtype=(env or {}).get("subtype", ""),
                model_usage=(env or {}).get("modelUsage", {}),
            )
        finally:
            release_session(s)


# ===================================================================== codex
STDIN_PROMPT_BYTES = 100_000
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
        u.cost_usd = estimate_cost("openai", model, u)
        return u


def parse_codex_jsonl(stdout: str) -> CodexEvents:
    ev = CodexEvents()
    for line in stdout.splitlines():
        ev.feed(line)
    return ev


class CodexAgent(_CliAgent):
    kind = "codex"
    cli_label = "codex"

    def __init__(self, model: str, binary: str | None = None, reasoning_effort: str | None = None):
        self.model, self.reasoning_effort = split_model_effort(model, reasoning_effort)
        self.binary = binary or get_settings().binaries.codex_cli

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
        try:
            if not ok:
                return failed(s, "error", why)
            prompt = _compose_prompt(job)
            via_stdin = len(prompt.encode("utf-8")) > STDIN_PROMPT_BYTES
            argv = self.build_argv(s, None if via_stdin else prompt)
            events = CodexEvents()
            proc = invoke(s, argv, self.build_env(s), prompt=prompt, stdout_name="stdout.jsonl",
                          on_stdout=events.feed, stdin=prompt if via_stdin else None)
            record_tool_calls(s, events.calls, source="codex exec --json")
            usage = events.usage(self.model)
            usage.latency_ms = int(proc.duration_s * 1000)
            text = "\n\n".join(m for m in events.messages if m.strip())
            errors = list(events.errors)
            if proc.timed_out:
                ok, reason = False, "timeout"
                errors.append(watchdog_error(proc))
            elif proc.rc != 0 or events.n_events == 0:
                ok, reason = False, "error"
                errors.append(f"rc={proc.rc}; events={events.n_events}; stderr tail: {tail(proc.stderr, 1500)}")
            elif events.errors and events.turns_completed == 0:
                ok, reason = False, "error"
            else:
                ok, reason = True, "completed"
            return finish_session(
                s, ok=ok, exit_reason=reason, text=text, usage=usage, tool_calls=events.tool_calls,
                turns=events.turns_completed, errors=errors,
                rc=proc.rc, killed_reason=proc.killed_reason, thread_id=events.thread_id,
                turns_completed=events.turns_completed, usage_raw=events.usage_raw,
            )
        finally:
            release_session(s)


# ===================================================================== antigravity

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
    """agy's envelope is the JSON line carrying ``response`` or ``status``."""
    return find_json_object(stdout, lambda d: "response" in d or "status" in d)


#: where agy 1.2.2 keeps each conversation: one SQLite file named by the envelope's
#: ``conversation_id``, a ``steps`` table whose ``step_payload`` is protobuf
AGY_CONVERSATIONS = Path.home() / ".gemini" / "antigravity-cli" / "conversations"
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


class AntigravityAgent(_CliAgent):
    kind = "agy"
    cli_label = "agy"

    def __init__(self, model: str, binary: str | None = None):
        self.model = model
        self.binary = binary or get_settings().binaries.agy_cli

    def served_model(self) -> str:
        """The id actually sent to agy (a bare one gains its effort suffix)."""
        return resolve_model(self.model, self.binary)

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

    def _record_trace(self, s: Session, conversation_id: str) -> int:
        """The session's tool calls, from agy's own conversation database — never fatal.
        Returns how many (agy's JSON envelope carries no tool count of its own)."""
        db = AGY_CONVERSATIONS / f"{conversation_id}.db" if conversation_id else None
        if db is None or not db.is_file():
            s.notes.append("no agy conversation record found for this session: tool trace missing")
            return 0
        try:
            return record_tool_calls(s, agy_tool_calls(db), source="agy conversation db")
        except Exception as e:  # noqa: BLE001 — the trace is telemetry; a session must never fail on it
            log.debug("agy conversation %s unreadable: %s", db, e)
            return 0

    def run(self, job: AgentJob) -> AgentResult:
        ok, why = self.available()
        s = begin_session(job, self.kind)
        try:
            if not ok:
                return failed(s, "error", why)
            prompt = deliver_prompt(s, _compose_prompt_agy(s))
            proc = invoke(s, self.build_argv(s, prompt), self.build_env(s), prompt=prompt)
            env = parse_agy_json(proc.stdout)
            n_calls = self._record_trace(s, str((env or {}).get("conversation_id") or ""))
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
            usage.tool_calls = n_calls
            return finish_session(
                s, ok=ok, exit_reason=reason, text=text, usage=usage, tool_calls=n_calls,
                turns=int((env or {}).get("num_turns") or 0), errors=errors,
                rc=proc.rc, killed_reason=proc.killed_reason,
                conversation_id=(env or {}).get("conversation_id", ""), num_turns=(env or {}).get("num_turns", 0),
            )
        finally:
            release_session(s)


def _compose_prompt_agy(s: Session) -> str:
    """agy has no system-prompt flag; prepend harness instructions + the absolute workspace path."""
    head = f"Workspace root: {s.ws.root}\nWork ONLY inside it, using your file tools."
    if (s.ws.root / "AGENTS.md").is_file():
        head += " Read AGENTS.md there first and follow it."
    return f"{head}\n\n{_compose_prompt(s.job)}"
