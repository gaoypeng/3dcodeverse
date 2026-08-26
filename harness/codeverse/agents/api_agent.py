"""``api-agent:<provider>:<model>`` — in-process agentic loop on any ChatModel.

Tools: workspace-confined file tools (+ policy-filtered ``run_shell``) and every spatial registry tool
(``build``, ``measure``, ``render_views`` ...) as native function calls.  The
loop runs until the model stops calling tools or ``job.max_turns``; it writes a
per-turn transcript JSONL, accumulates ``Usage``, compacts old tool results
when the context grows, and applies the *freshness* rule: a model that claims
to be done after editing files without a subsequent ``build`` is nudged once.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from codeverse.agents.api_skills import SkillTools
from codeverse.agents.api_tools import FileTools, SpatialTools, ToolOutcome
from codeverse.agents.cli_common import Session, begin_session, failed, finish_session
from codeverse.agents.context import compact_messages, message_chars
from codeverse.contracts.agent import AgentJob, AgentResult
from codeverse.contracts.chat import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)
from codeverse.contracts.common import Usage
from codeverse.skills.delivery import delivery_for

log = logging.getLogger(__name__)

BUILD_TOOL = "build"
COMPACT_AT_CHARS = 350_000
MODEL_RETRIES = 3
DEFAULT_SYSTEM = (
    "You are an expert 3D programmer working inside a harness-managed workspace. "
    "Use the tools to read, write and verify code under src/ and public/. Write RAW code in the "
    "language the task names; never import from `codeverse` or any helper SDK. Call `build` after "
    "every batch of edits and only finish on a clean build. Reply with a short summary when done; "
    "never ask questions — decide and proceed."
)
FRESHNESS_NUDGE = (
    "You edited files after your last `build`. Run the `build` tool now, read its result, fix any "
    "errors, and only then give your final summary."
)


class ApiAgent:
    kind = "api-agent"

    def __init__(self, model: str, chat_model: Any | None = None):
        self.model = model  # '<provider>:<model>'
        self._chat_model = chat_model

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.model}"

    def _chat(self):
        if self._chat_model is None:
            from codeverse.models import get_chat_model

            self._chat_model = get_chat_model(self.model)
        return self._chat_model

    def available(self) -> tuple[bool, str]:
        try:
            from codeverse.models import parse_model_id

            parse_model_id(self.model)
        except Exception as e:  # noqa: BLE001 - report, don't raise
            return False, str(e)
        try:
            self._chat()
        except Exception as e:  # noqa: BLE001
            return False, f"chat model unavailable: {e}"
        return True, "ok"

    # ------------------------------------------------------------------ run
    def run(self, job: AgentJob) -> AgentResult:
        s = begin_session(job, self.kind)
        try:
            chat = self._chat()
        except Exception as e:  # noqa: BLE001
            return failed(s, "error", f"chat model unavailable: {e}")
        loop = _Loop(self, s, chat)
        return loop.run()


class _Loop:
    """One session's state: tools, messages, usage, freshness, compaction."""

    def __init__(self, agent: ApiAgent, s: Session, chat: Any):
        self.agent, self.s, self.chat = agent, s, chat
        job = s.job
        self.files = FileTools(
            s.ws,
            job.write_roots,
            allow_shell=job.api.allow_shell,
            edit_only=job.files_hint if job.edit_only else None,
            always_writable=job.always_writable,
        )
        self.spatial: SpatialTools | None = None
        if job.spatial_tools:
            self.spatial = SpatialTools(
                s.ws, track=job.track, language=job.language, round_index=job.round
            )
            if not self.spatial.tools:
                s.notes.append("spatial registry is empty; running with file tools only")
                self.spatial = None
        # the routed bundles as a TOOL, not as prose: measured, api-agent read 0 of 5 from a
        # MANDATORY paragraph in AGENTS.md while the CLI backends read 5 of 5 through their own
        # loaders.  It responds to tool specs; the bundles also sit in hidden dirs list_files
        # skips, so it could not have found them by looking (codeverse/agents/api_skills.py).
        # a backend WITHOUT a native loader gets the routed set as a tool; the policy lives
        # in skills/delivery.py so a new backend is one row there, not a branch here
        self.skills = SkillTools(s.ws) if delivery_for(agent.kind).needs_tool else None
        self.specs: list[ToolSpec] = (
            self.files.specs()
            + (self.skills.specs() if self.skills else [])
            + (self.spatial.specs() if self.spatial else [])
        )
        self.usage = Usage(backend="api-agent", model=agent.model)
        self.tool_calls = 0
        self.last_write_turn = -1
        self.last_build_turn = -1
        self.nudged = False
        self.errors: list[str] = []
        # the images ride on the first message only: reference photos, and for a refine
        # session the contact sheet the judge scored.  Until 2026-08-26 no image reached an
        # agent session at all — GenerationTask.images fed the single-shot path only, so a
        # `--image` reference was seen by the planner and the judge and never by the code
        # writer, which had to infer it back from compare_reference's IoU number.
        self.messages: list[ChatMessage] = [ChatMessage.user(job.prompt, images=job.images or None)]
        self.system = self._system_prompt()
        self.max_usd = job.api.max_usd
        self.t_deadline = time.monotonic() + job.timeout_s

    def _system_prompt(self) -> str:
        agents_md = self.s.ws.root / "AGENTS.md"
        base = agents_md.read_text() if agents_md.is_file() else DEFAULT_SYSTEM
        if self.s.job.system_append:
            base += "\n\n" + self.s.job.system_append
        return base

    # ------------------------------------------------------------------ loop
    def run(self) -> AgentResult:
        job = self.s.job
        traj = self.s.traj
        traj.append("system", text=self.system[:20000], tools=[t.name for t in self.specs])
        ok, reason, text = False, "budget", ""
        for turn in range(job.max_turns):
            if time.monotonic() > self.t_deadline:
                reason, self.errors = (
                    "timeout",
                    self.errors + [f"wall clock exceeded {job.timeout_s}s"],
                )
                break
            resp = self._generate(turn)
            if resp is None:
                reason = "error"
                break
            text = resp.text
            self.messages.append(_assistant_message(resp))
            traj.append(
                "assistant",
                turn=turn,
                text=resp.text[:20000],
                tool_calls=[
                    {"id": c.id, "name": c.name, "arguments": c.arguments} for c in resp.tool_calls
                ],
                usage=resp.usage.model_dump(),
                finish_reason=resp.finish_reason,
            )
            if not resp.tool_calls:
                if self._needs_build_nudge():
                    self.nudged = True
                    self.messages.append(ChatMessage.user(FRESHNESS_NUDGE))
                    traj.append("nudge", turn=turn, text=FRESHNESS_NUDGE)
                    continue
                ok, reason = True, "completed"
                break
            self._execute(turn, resp.tool_calls)
            if self.max_usd and self.usage.cost_usd > self.max_usd:
                reason, self.errors = (
                    "budget",
                    self.errors + [f"cost {self.usage.cost_usd:.3f} > max_usd {self.max_usd}"],
                )
                break
            if message_chars(self.messages) > COMPACT_AT_CHARS:
                self.messages = compact_messages(self.messages, keep_recent=6, facts=self._facts())
                traj.append("compact", turn=turn, chars=message_chars(self.messages))
        else:
            self.errors.append(f"max_turns ({job.max_turns}) reached")
        return finish_session(
            self.s,
            ok=ok,
            exit_reason=reason,
            text=text,
            usage=self.usage,
            tool_calls=self.tool_calls,
            errors=self.errors,
            turns=len(self.messages),
            nudged=self.nudged,
            tools=[t.name for t in self.specs],
        )

    def _generate(self, turn: int) -> ChatResponse | None:
        req = ChatRequest(
            messages=self.messages,
            system=self.system,
            tools=self.specs,
            temperature=self.s.job.api.temperature,
            thinking=self.s.job.api.thinking,
            label=f"api-agent:{self.s.label}:t{turn}",
        )
        delay = 2.0
        for attempt in range(MODEL_RETRIES):
            try:
                resp = self.chat.generate(req)
                self.usage = self.usage + resp.usage
                return resp
            except Exception as e:  # ModelError or provider glitch
                retryable = bool(getattr(e, "retryable", False))
                self.s.traj.append(
                    "model_error",
                    turn=turn,
                    attempt=attempt,
                    error=str(e)[:2000],
                    retryable=retryable,
                )
                if not retryable or attempt == MODEL_RETRIES - 1:
                    self.errors.append(f"model error on turn {turn}: {type(e).__name__}: {e}")
                    return None
                time.sleep(delay)
                delay *= 2
        return None

    # ------------------------------------------------------------------ tools
    def _execute(self, turn: int, calls: list[ToolCallPart]) -> None:
        parts: list[ToolResultPart] = []
        for call in calls:
            self.tool_calls += 1
            out = self._dispatch(call.name, dict(call.arguments or {}))
            if call.name in ("write_file", "edit_file") and not out.is_error:
                self.last_write_turn = turn
            if call.name == "read_file" and not out.is_error:
                # signal 3 of the read probe: api-agent is the only backend where we own
                # the read tool, so it is the calibration arm for the atime probe.
                from codeverse.skills.telemetry import record_exact_read

                record_exact_read(
                    self.s.ws.root,
                    str((call.arguments or {}).get("path", "")),
                    turn=turn,
                    label=getattr(self.s, "label", "") or "",
                )
            if call.name in (BUILD_TOOL, "run_build"):
                self.last_build_turn = turn
            parts.append(
                ToolResultPart(
                    call_id=call.id,
                    name=call.name,
                    content=out.text,
                    images=out.images,
                    is_error=out.is_error,
                )
            )
            self.s.traj.append(
                "tool_result",
                turn=turn,
                name=call.name,
                call_id=call.id,
                ok=not out.is_error,
                content=out.text[:8000],
                images=[i.path for i in out.images],
                numbers=out.numbers,
            )
        self.messages.append(ChatMessage(role="tool", parts=parts))

    def _dispatch(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        if name == "run_build" and self.spatial and BUILD_TOOL in self.spatial.names():
            name = BUILD_TOOL
        if self.skills and (out := self.skills.dispatch(name, args)) is not None:
            return out
        if name in self.files.names():
            return self.files.call(name, args)
        if self.spatial and name in self.spatial.names():
            return self.spatial.call(name, args)
        return ToolOutcome(
            f"unknown tool {name!r}; available: {sorted(t.name for t in self.specs)}", is_error=True
        )

    def _needs_build_nudge(self) -> bool:
        has_build = bool(self.spatial and BUILD_TOOL in self.spatial.names())
        return has_build and not self.nudged and self.last_write_turn > self.last_build_turn

    def _facts(self) -> str:
        writes = list(dict.fromkeys(self.files.writes))
        lines = [
            f"- tool calls so far: {self.tool_calls}",
            f"- files written/edited (in order): {', '.join(writes) if writes else 'none'}",
            f"- last build turn: {self.last_build_turn} (last write turn: {self.last_write_turn})",
        ]
        return "\n".join(lines)


def _assistant_message(resp: ChatResponse) -> ChatMessage:
    parts: list[Any] = []
    if resp.text:
        parts.append(TextPart(text=resp.text))
    parts.extend(resp.tool_calls)
    if not parts:
        parts.append(TextPart(text=""))
    return ChatMessage(role="assistant", parts=parts)


__all__ = ["ApiAgent", "DEFAULT_SYSTEM", "FRESHNESS_NUDGE"]
