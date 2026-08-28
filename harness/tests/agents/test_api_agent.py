"""ApiAgent loop with a scripted FakeChatModel (+ one live gemini test)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse.agents.api_agent import FRESHNESS_NUDGE, ApiAgent
from codeverse.agents.api_tools import ToolOutcome
from codeverse.agents.context import FACTS_TAG, compact_messages, message_chars
from codeverse.config import get_settings
from codeverse.contracts.agent import AgentJob
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


class FakeChatModel:
    """Replays scripted responses; records every request."""

    provider, model = "fake", "fake-1"

    def __init__(self, script: list[ChatResponse]):
        self.script = list(script)
        self.requests: list[ChatRequest] = []

    @property
    def id(self) -> str:
        return "fake:fake-1"

    def supports_vision(self) -> bool:
        return True

    def generate(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        if not self.script:
            return ChatResponse(text="out of script", usage=Usage(input_tokens=1, output_tokens=1, cost_usd=0.001))
        r = self.script.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def call(name: str, **args) -> ToolCallPart:
    return ToolCallPart(id=f"c{abs(hash((name, json.dumps(args, sort_keys=True)))) % 10000}", name=name, arguments=args)


def resp(text: str = "", *calls: ToolCallPart) -> ChatResponse:
    return ChatResponse(text=text, tool_calls=list(calls), usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.01))


class FakeSpatial:
    """Stands in for the registry bridge: one `build` tool that records calls."""

    def __init__(self, *a, **k):
        self.calls: list[dict] = []
        self.tools = {"build": True}

    def specs(self):
        return [ToolSpec(name="build", description="build it", parameters={"type": "object", "properties": {}})]

    def names(self):
        return {"build"}

    def call(self, name, args):
        self.calls.append(args)
        return ToolOutcome("build ok: 12 tris", numbers={"tris": 12})


def _job(ws, **kw) -> AgentJob:
    base = dict(workspace=str(ws.root), prompt="make src/hello.txt say hi", label="api", timeout_s=60, max_turns=8,
                spatial_tools=False)
    base.update(kw)
    return AgentJob(**base)


def test_loop_writes_file_and_finishes(tmp_ws):
    fake = FakeChatModel([
        resp("let me look", call("list_files", glob="src/**/*")),
        resp("", call("write_file", path="src/hello.txt", content="hi\n")),
        resp("Done: wrote src/hello.txt"),
    ])
    res = ApiAgent("fake:fake-1", chat_model=fake).run(_job(tmp_ws))
    assert res.ok and res.exit_reason == "completed" and res.text.startswith("Done"), res.errors
    assert (tmp_ws.src / "hello.txt").read_text() == "hi\n"
    assert [f.path for f in res.files_changed] == ["src/hello.txt"] and res.tool_calls == 2
    assert res.usage.input_tokens == 30 and res.usage.cost_usd == pytest.approx(0.03)
    # tool results fed back as role=tool messages
    last_req = fake.requests[-1]
    assert last_req.messages[-1].role == "tool" and isinstance(last_req.messages[-1].parts[0], ToolResultPart)
    assert last_req.tools and {t.name for t in last_req.tools} >= {"read_file", "write_file", "edit_file", "list_files", "run_shell"}
    # transcript
    kinds = [json.loads(ln)["kind"] for ln in Path(res.transcript_path).read_text().splitlines()]
    assert kinds[0] == "system" and kinds.count("assistant") == 3 and kinds.count("tool_result") == 2
    rec = json.loads((Path(res.transcript_path).parent / "result.json").read_text())
    assert rec["nudged"] is False and "write_file" in rec["tools"]


def test_system_prompt_uses_agents_md_and_append(tmp_ws):
    (tmp_ws.root / "AGENTS.md").write_text("RULES HERE")
    fake = FakeChatModel([resp("done")])
    ApiAgent("fake:fake-1", chat_model=fake).run(_job(tmp_ws, system_append="APPEND"))
    assert fake.requests[0].system.startswith("RULES HERE") and fake.requests[0].system.endswith("APPEND")


def test_freshness_nudge_once(tmp_ws, monkeypatch):
    monkeypatch.setattr("codeverse.agents.api_agent.SpatialTools", FakeSpatial)
    fake = FakeChatModel([
        resp("", call("write_file", path="src/a.txt", content="x")),
        resp("all done"),                      # claims done without build → nudge
        resp("", call("build")),               # obeys
        resp("", call("edit_file", path="src/a.txt", old="x", new="y")),
        resp("really done"),                   # edited after build again, but nudged already → accept
    ])
    res = ApiAgent("fake:fake-1", chat_model=fake).run(_job(tmp_ws, spatial_tools=True))
    assert res.ok and res.text == "really done"
    nudges = [m for r in fake.requests for m in r.messages if m.role == "user" and m.text == FRESHNESS_NUDGE]
    assert nudges  # nudge was injected
    rec = json.loads((Path(res.transcript_path).parent / "result.json").read_text())
    assert rec["nudged"] is True and "build" in rec["tools"]


def test_run_build_alias_and_unknown_tool(tmp_ws, monkeypatch):
    monkeypatch.setattr("codeverse.agents.api_agent.SpatialTools", FakeSpatial)
    fake = FakeChatModel([resp("", call("run_build"), call("bogus", x=1)), resp("done")])
    res = ApiAgent("fake:fake-1", chat_model=fake).run(_job(tmp_ws, spatial_tools=True))
    assert res.ok
    tool_msg = fake.requests[1].messages[-1]
    parts = {p.name: p for p in tool_msg.parts}
    assert "build ok" in parts["run_build"].content and parts["bogus"].is_error


def test_max_turns_and_budget(tmp_ws):
    fake = FakeChatModel([resp("", call("list_files")) for _ in range(20)])
    res = ApiAgent("fake:fake-1", chat_model=fake).run(_job(tmp_ws, max_turns=3))
    assert not res.ok and res.exit_reason == "budget" and "max_turns" in res.errors[0]
    fake = FakeChatModel([resp("", call("list_files")) for _ in range(20)])
    res = ApiAgent("fake:fake-1", chat_model=fake).run(_job(tmp_ws, extra={"max_usd": 0.015}))
    assert not res.ok and res.exit_reason == "budget" and "max_usd" in res.errors[0]


def test_model_error_retry_then_fail(tmp_ws):
    class Boom(RuntimeError):
        retryable = True

    fake = FakeChatModel([Boom("503"), resp("", call("list_files")), Boom("bad"), Boom("bad"), Boom("bad")])
    fake.script[2].retryable = False
    import codeverse.agents.api_agent as mod

    orig = mod.time.sleep
    mod.time.sleep = lambda s: None
    try:
        res = ApiAgent("fake:fake-1", chat_model=fake).run(_job(tmp_ws))
    finally:
        mod.time.sleep = orig
    assert not res.ok and res.exit_reason == "error" and "model error" in res.errors[0]


def test_write_outside_roots_is_refused(tmp_ws):
    fake = FakeChatModel([resp("", call("write_file", path="artifacts/x.glb", content="z")), resp("ok")])
    res = ApiAgent("fake:fake-1", chat_model=fake).run(_job(tmp_ws))
    assert res.ok and not (tmp_ws.artifacts / "x.glb").exists()
    assert fake.requests[1].messages[-1].parts[0].is_error


def test_compaction():
    msgs = [ChatMessage.user("task")]
    for i in range(12):
        msgs.append(ChatMessage(role="assistant", parts=[TextPart(text=f"t{i}"), call("read_file", path=f"f{i}")]))
        msgs.append(ChatMessage(role="tool", parts=[ToolResultPart(call_id=f"c{i}", name="read_file", content="X" * 1000)]))
    before = message_chars(msgs)
    out = compact_messages(msgs, keep_recent=4, facts="- nothing yet")
    assert message_chars(out) < before / 2
    assert out[0].text == "task" and out[1].text.startswith(FACTS_TAG)
    assert out[-1] is msgs[-1] and "compacted" in out[3].parts[0].content
    again = compact_messages(out, keep_recent=4, facts="- new facts")
    assert sum(1 for m in again if m.role == "user" and m.text.startswith(FACTS_TAG)) == 1


def test_compaction_triggers_in_loop(tmp_ws, monkeypatch):
    monkeypatch.setattr("codeverse.agents.api_agent.COMPACT_AT_CHARS", 2000)
    (tmp_ws.src / "big.txt").write_text("Z" * 1500)
    fake = FakeChatModel([resp("", call("read_file", path="src/big.txt")) for _ in range(8)] + [resp("done")])
    res = ApiAgent("fake:fake-1", chat_model=fake).run(_job(tmp_ws, max_turns=12))
    assert res.ok
    kinds = [json.loads(ln)["kind"] for ln in Path(res.transcript_path).read_text().splitlines()]
    assert "compact" in kinds
    assert any(m.text.startswith(FACTS_TAG) for m in fake.requests[-1].messages if m.role == "user")


@pytest.mark.live
def test_live_api_agent_gemini(tmp_ws):
    if not get_settings().gemini_api_keys:
        pytest.skip("no gemini keys")
    agent = ApiAgent("gemini:gemini-3.7-flash")
    res = agent.run(AgentJob(workspace=str(tmp_ws.root), label="live", timeout_s=240, max_turns=8, spatial_tools=False,
                             prompt="Create the file src/hello.txt containing exactly the word 'hi' (use write_file), "
                                    "then read it back to verify, then reply DONE."))
    assert res.ok, res.errors
    assert (tmp_ws.src / "hello.txt").read_text().strip() == "hi"
    assert res.usage.input_tokens > 0 and res.tool_calls >= 1


def test_job_images_ride_on_the_first_user_message(tmp_ws, tmp_path):
    """Until 2026-08-26 no image reached an agent session: GenerationTask.images fed the
    single-shot path only, so a `--image` reference was seen by the planner and the judge
    and never by the code writer, and a refine session got "what the judge saw" as prose
    without the sheet the prose was written about."""
    from codeverse.contracts.chat import ImagePart

    png = tmp_path / "sheet.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    fake = FakeChatModel([resp("Done")])
    job = _job(tmp_ws)
    job = job.model_copy(update={"images": [ImagePart(path=str(png), label="the contact sheet the judge scored (round 0)")]})
    ApiAgent("fake:fake-1", chat_model=fake).run(job)
    first = fake.requests[0].messages[0]
    imgs = [pt for pt in first.parts if isinstance(pt, ImagePart)]
    assert imgs and imgs[0].path == str(png), "the image must be a part of the FIRST user message"
    assert "judge scored" in (imgs[0].label or "")


# ------------------------------------------------------ per-turn retry budget (audit 2026-08-26 §5.1)
class _Clock:
    """Fake ``time`` for the agent loop: ``monotonic()`` moves only when a test moves it."""

    def __init__(self, t: float = 1000.0):
        self.t = t
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def test_each_turn_gets_a_retry_budget_clipped_to_what_the_session_can_afford(tmp_ws, monkeypatch):
    """Audit 2026-08-26 §5.1: 66 give-up spans of the model's 900 s deadline (up to 3 in a row on
    one turn: median 923 s, p90 2 743 s) were 30 % of the storm day's waiting, ~1 430 s per run.
    A turn's call may now retry for at most TURN_WAIT_MAX_S = 120 s (a storm-day call succeeds in
    8.3 s p50 / 31 s p90) and never past the session deadline, floored at 20 s so a late turn
    still gets one real attempt."""
    import codeverse.agents.api_agent as mod

    for timeout_s, want in ((600, 120.0), (60, 60.0), (5, 20.0)):
        monkeypatch.setattr(mod, "time", _Clock())
        fake = FakeChatModel([resp("", call("list_files")), resp("done")])
        res = ApiAgent("fake:fake-1", chat_model=fake).run(_job(tmp_ws, timeout_s=timeout_s))
        assert res.ok, res.errors
        assert [r.max_wait_s for r in fake.requests] == [want, want], timeout_s


def test_a_turn_stops_retrying_once_the_session_deadline_has_passed(tmp_ws, monkeypatch):
    """The deadline used to be checked only at the top of a turn: a turn that met it inside the
    retry loop still slept 2 s + 4 s and re-entered the model's whole retry machine twice more
    (97 sessions overshot their timeout on 2026-08-26 by 182 s median / 1 154 s p90)."""
    import codeverse.agents.api_agent as mod

    clock = _Clock()
    monkeypatch.setattr(mod, "time", clock)

    class Boom(RuntimeError):
        retryable = True

    class StormModel(FakeChatModel):
        def generate(self, request: ChatRequest) -> ChatResponse:
            self.requests.append(request)
            clock.t += request.max_wait_s  # the model spent its whole budget retrying
            raise Boom("503 high demand")

    fake = StormModel([])
    res = ApiAgent("fake:fake-1", chat_model=fake).run(_job(tmp_ws, timeout_s=100))
    assert not res.ok and res.exit_reason == "error"
    assert fake.requests[0].max_wait_s == 100.0
    assert len(fake.requests) == 1, "no retry once the deadline has passed"
    assert clock.slept == [], "and no 2 s / 4 s sleep past it"
    assert "deadline passed" in res.errors[0]
    rows = [json.loads(ln) for ln in Path(res.transcript_path).read_text().splitlines()]
    (err_row,) = [r for r in rows if r["kind"] == "model_error"]
    assert err_row["deadline_passed"] is True and err_row["retryable"] is True


def cut(text: str = "thinking...") -> ChatResponse:
    """A turn that ran out of output tokens mid-thought: partial text, no tool call."""
    return ChatResponse(text=text, tool_calls=[], finish_reason="MAX_TOKENS",
                        usage=Usage(input_tokens=10, output_tokens=637, cost_usd=0.02))


def test_a_turn_truncated_while_thinking_is_nudged_not_treated_as_finished(tmp_ws):
    """art_med_tool_chest, 2026-08-27: turn 5 spent 15 359 thinking tokens, hit
    MAX_TOKENS, emitted partial text and no tool call — and `if not resp.tool_calls`
    recorded the session as completed with zero files written."""
    from codeverse.agents.api_agent import TRUNCATED_NUDGE

    fake = FakeChatModel([
        cut("<thought>let me work out the hinge axis and"),
        resp("", call("write_file", path="src/model.py", content="import bpy\n")),
        resp("done"),
    ])
    res = ApiAgent("fake:fake-1", chat_model=fake).run(_job(tmp_ws))
    assert res.ok and res.files_changed, "a truncated turn must not end the session"
    assert (tmp_ws.src / "model.py").is_file()
    assert any(m.text == TRUNCATED_NUDGE for r in fake.requests for m in r.messages if m.role == "user")


def test_repeated_truncation_fails_the_session_instead_of_looping(tmp_ws):
    from codeverse.agents.api_agent import MAX_TRUNCATED_NUDGES

    fake = FakeChatModel([cut()] * (MAX_TRUNCATED_NUDGES + 2))
    res = ApiAgent("fake:fake-1", chat_model=fake).run(_job(tmp_ws))
    assert not res.ok and res.exit_reason == "truncated"
    assert any("output tokens" in e for e in res.errors)


def test_a_truncated_turn_widens_the_output_envelope(tmp_ws):
    """The model was not misbehaving: thinking grew 13 -> 3 144 -> 15 359 tokens on a hard
    problem and 15 359 + 637 hit the 16 000 default exactly.  planner has grown its budget
    on truncation for months; the agent loop took the default and never moved."""
    from codeverse.agents.api_agent import TURN_TOKENS, TURN_TOKENS_GROWTH

    fake = FakeChatModel([
        cut("<thought>the hinge axis and"),
        resp("", call("write_file", path="src/model.py", content="import bpy\n")),
        resp("done"),
    ])
    ApiAgent("fake:fake-1", chat_model=fake).run(_job(tmp_ws))
    asked = [r.max_output_tokens for r in fake.requests]
    assert asked[0] == TURN_TOKENS
    assert asked[1] == int(TURN_TOKENS * TURN_TOKENS_GROWTH), "the turn after a truncation gets room"
