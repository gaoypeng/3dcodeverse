"""Metering: every ChatModel.generate and every CodingAgent.run becomes a ledger row."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.contracts.agent import AgentJob, AgentResult
from codeverse3d.contracts.chat import ChatMessage, ChatRequest, ChatResponse
from codeverse3d.contracts.common import Usage
from codeverse3d.cost.instrument import (
    MeteredAgent,
    MeteredChatModel,
    run_ledger,
)
from codeverse3d.cost.ledger import load_ledger
from codeverse3d.cost.types import Role, Stage


def _usage(**kw: object) -> Usage:
    base = {"backend": "gemini", "model": "gemini-3.7-flash", "input_tokens": 12_000,
            "cached_tokens": 8_000, "output_tokens": 300, "latency_ms": 1234}
    base.update(kw)
    return Usage(**base)  # type: ignore[arg-type]


class FakeChat:
    provider = "gemini"
    model = "gemini-3.7-flash"

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.requests: list[ChatRequest] = []

    @property
    def id(self) -> str:
        return "gemini:gemini-3.7-flash"

    def generate(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return ChatResponse(text="ok", usage=_usage())


class FakeAgent:
    """A backend whose individual calls already reach the ledger."""

    kind = "self-metering"
    model = "gemini:gemini-3.6-flash"

    def __init__(self, chat, turns: int = 3):
        self.chat, self.turns, self.seen_turns = chat, turns, 0

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.model}"

    def available(self) -> tuple[bool, str]:
        return True, "ok"

    def run(self, job: AgentJob) -> AgentResult:
        self.seen_turns = job.max_turns
        total = Usage()
        for t in range(self.turns):
            r = self.chat.generate(ChatRequest(messages=[ChatMessage.user("x")],
                                               label=f"api-agent:{job.label}:t{t}"))
            total = total + r.usage
        return AgentResult(ok=True, exit_reason="completed", usage=total, tool_calls=self.turns)


class CliAgent(FakeAgent):
    kind = "gemini-cli"   # a vendor CLI bills as ONE session row

    def run(self, job: AgentJob) -> AgentResult:
        self.seen_turns = job.max_turns
        return AgentResult(ok=True, exit_reason="completed", tool_calls=4,
                           usage=_usage(backend="gemini-cli", input_tokens=50_000, cached_tokens=40_000))


def test_a_metered_call_becomes_one_priced_row(tmp_path: Path):
    chat = FakeChat()
    with run_ledger(tmp_path, run="r1"):
        resp = MeteredChatModel(chat).generate(
            ChatRequest(messages=[ChatMessage.user("x")], label="judge:static_object_v1:r01:s0"))
    assert resp.text == "ok"
    (row,) = load_ledger(tmp_path)
    assert row.run == "r1" and row.stage is Stage.JUDGE and row.role is Role.JUDGE and row.round == 1
    assert row.input_tokens == 12_000 and row.cached_tokens == 8_000 and row.cache_hit is True
    assert row.price_source == "exact" and row.price_input == 0.75 and row.price_checked
    assert row.latency_ms == 1234 and row.cost_usd > 0


def test_a_failed_call_is_recorded_and_re_raised(tmp_path: Path):
    with run_ledger(tmp_path, run="r1"), pytest.raises(TimeoutError):
        MeteredChatModel(FakeChat(TimeoutError("boom"))).generate(
            ChatRequest(messages=[ChatMessage.user("x")], label="planner"))
    (row,) = load_ledger(tmp_path)
    assert row.outcome == "timeout" and row.stage is Stage.PLAN and row.cost_usd == 0.0




def test_get_coding_agent_hands_out_a_metered_agent(monkeypatch: pytest.MonkeyPatch):
    from codeverse3d.agents import registry

    monkeypatch.setattr(registry, "_build_agent", lambda aid: CliAgent(FakeChat()))
    assert isinstance(registry.get_coding_agent("gemini-cli:m"), MeteredAgent)



def test_accounting_never_breaks_a_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("ledger on fire")

    monkeypatch.setattr("codeverse3d.cost.instrument.record_call", boom)
    with run_ledger(tmp_path, run="r1"):
        resp = MeteredChatModel(FakeChat()).generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert resp.text == "ok"


def test_the_proxy_forwards_everything_else():
    chat = FakeChat()
    chat.pool = "the-key-pool"  # type: ignore[attr-defined]
    m = MeteredChatModel(chat)
    assert m.id == "gemini:gemini-3.7-flash" and m.provider == "gemini"
    assert m.pool == "the-key-pool"
    agent = MeteredAgent(FakeAgent(chat))
    assert agent.kind == "self-metering" and agent.available() == (True, "ok")


def test_get_chat_model_hands_out_a_metered_model(monkeypatch: pytest.MonkeyPatch):
    from codeverse3d.models import registry

    monkeypatch.setattr(registry, "build_chat_model", lambda mid: FakeChat())
    registry.get_chat_model.cache_clear()
    try:
        assert isinstance(registry.get_chat_model("gemini:gemini-3.7-flash"), MeteredChatModel)
    finally:
        registry.get_chat_model.cache_clear()


def test_post_hoc_work_never_creates_a_partial_ledger(tmp_path: Path):
    """Post-hoc work never creates a misleading partial ledger."""
    with run_ledger(tmp_path, run="old", create=False) as led:
        assert led is None
        MeteredChatModel(FakeChat()).generate(
            ChatRequest(messages=[ChatMessage.user("x")], label="judge:static_object_v1:r00:s0"))
    assert not (tmp_path / "telemetry" / "cost.jsonl").exists()
    # but a run that already keeps one is appended to
    with run_ledger(tmp_path, run="old"):
        MeteredChatModel(FakeChat()).generate(ChatRequest(messages=[ChatMessage.user("x")], label="planner"))
    with run_ledger(tmp_path, run="old", create=False) as led:
        assert led is not None
        MeteredChatModel(FakeChat()).generate(
            ChatRequest(messages=[ChatMessage.user("x")], label="judge:static_object_v1:r00:s0"))
    assert len(load_ledger(tmp_path)) == 2


# ---------------------------------------------------- who meters the inside of a session
class SideEffectCliAgent(CliAgent):
    """A subscription CLI during whose session something else bills a model."""

    def __init__(self, chat, side_effect):
        super().__init__(chat)
        self.side_effect = side_effect

    def run(self, job: AgentJob) -> AgentResult:
        self.side_effect()
        return super().run(job)


def test_a_cli_session_is_recorded_even_when_a_tool_bills_a_model_inside_it(tmp_path: Path):
    """An unrelated inner model row never suppresses the CLI session."""
    def inner_model_call() -> None:
        MeteredChatModel(FakeChat()).generate(
            ChatRequest(messages=[ChatMessage.user("x")], label="texture_plan"))

    with run_ledger(tmp_path, run="r1"):
        MeteredAgent(SideEffectCliAgent(FakeChat(), inner_model_call)).run(
            AgentJob(workspace=str(tmp_path), prompt="p", label="baseline", round=0, kind="baseline"))
    rows = load_ledger(tmp_path)
    assert len(rows) == 2, [r.label for r in rows]
    session = next(r for r in rows if r.source == "session")
    assert session.backend == "gemini-cli" and session.input_tokens == 50_000
    tool = next(r for r in rows if r.source != "session")
    assert tool.stage is Stage.TEXTURE  # and it is NOT filed under the session's stage


# ------------------------------------------------------------------- nesting / parallelism
def test_parallel_runs_in_their_own_threads_keep_their_own_ledgers(tmp_path: Path):
    """`bench.run_bench` runs N prompts in N threads; their rows must not mix."""
    from concurrent.futures import ThreadPoolExecutor

    def one(name: str) -> None:
        with run_ledger(tmp_path / name, run=name):
            for _ in range(3):
                MeteredChatModel(FakeChat()).generate(
                    ChatRequest(messages=[ChatMessage.user("x")], label="baseline"))

    names = [f"p{i}" for i in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, names))
    for name in names:
        rows = load_ledger(tmp_path / name)
        assert len(rows) == 3 and {r.run for r in rows} == {name}


class KeyedChat(FakeChat):
    """A gemini-shaped response: ``raw`` names the key that answered and the round-trips."""

    def __init__(self, raw: dict):
        super().__init__()
        self.raw = raw

    def generate(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return ChatResponse(text="ok", usage=_usage(), raw=dict(self.raw))


def test_a_full_key_never_reaches_the_ledger(tmp_path: Path):
    with run_ledger(tmp_path, run="r1"):
        MeteredChatModel(KeyedChat({"key": "AIzaSy-a-whole-secret-key-9999"})).generate(
            ChatRequest(messages=[ChatMessage.user("x")], label="planner"))
    (row,) = load_ledger(tmp_path)
    assert row.key == "…9999" and row.attempts == 0
    assert "secret" not in (tmp_path / "telemetry" / "cost.jsonl").read_text()


def test_a_failed_call_records_its_attempts_but_no_key(tmp_path: Path):
    from codeverse3d.models.base import ModelError

    err = ModelError("503 high demand", retryable=True, status=503, attempts=7)
    with run_ledger(tmp_path, run="r1"), pytest.raises(ModelError):
        MeteredChatModel(FakeChat(err)).generate(ChatRequest(messages=[ChatMessage.user("x")], label="planner"))
    (row,) = load_ledger(tmp_path)
    assert row.outcome == "error" and row.attempts == 7 and row.key == ""


def test_a_billed_but_invalid_attempt_is_in_the_total_exactly_once(tmp_path: Path):
    """A charged invalid attempt is counted exactly once beside its winner."""
    from codeverse3d.cost.ledger import summarise
    from tests.models.test_gemini import make_model, text_response

    m, _log, _ = make_model([text_response("not json"), text_response('{"ok": true}')])
    with run_ledger(tmp_path, run="r1"):
        resp = MeteredChatModel(m).generate(
            ChatRequest(messages=[ChatMessage.user("x")], response_schema={"type": "object"},
                        label="planner"))
    assert resp.parsed == {"ok": True}
    rows = load_ledger(tmp_path)  # "attempt" forensics are filtered here; "extra" is money
    bad, = [r for r in rows if r.source == "extra"]
    logical, = [r for r in rows if r.source == "live"]
    assert logical.outcome == "ok" and logical.attempts == 2 and logical.call_id
    assert bad.discarded and bad.outcome == "discarded" and bad.input_tokens == 100
    assert bad.call_id == logical.call_id and bad.cost_usd > 0
    total = summarise(rows).total
    assert total.input_tokens == logical.input_tokens + bad.input_tokens
    assert summarise(load_ledger(tmp_path, include_attempts=True)).total.input_tokens \
        == total.input_tokens + 100, "include_attempts adds the winner's forensic row, once"
    win, = [r for r in load_ledger(tmp_path, include_attempts=True) if r.source == "attempt"]
    assert not win.discarded and win.attempt == 2


def test_a_late_hedge_loser_keeps_its_stage_role_and_round(tmp_path: Path):
    """The loser lands after generate() returned, in a thread with empty contextvars."""
    import threading

    from codeverse3d.cost.context import AttemptRecord, attempt_sink, call_context

    loser_usage = _usage(cost_usd=0.30)

    class Hedging:
        provider, model = "gemini", "gemini-3.7-flash"
        id = "gemini:gemini-3.7-flash"
        late: threading.Thread | None = None

        def generate(self, req):
            sink = attempt_sink()  # captured once, like gemini._attempt_hook

            def loser_lands():
                sink(AttemptRecord(attempt=2, key="k" * 20, outcome="ok", discarded=True,
                                   usage=loser_usage, error=""))

            self.late = threading.Thread(target=loser_lands)  # fresh thread = empty context
            return ChatResponse(text="winner", usage=_usage(cost_usd=0.01))

    inner = Hedging()
    with run_ledger(tmp_path, run="r1"):
        with call_context(stage="candidate", role="generator", round=0):
            MeteredChatModel(inner).generate(ChatRequest(messages=[ChatMessage.user("x")], label="baseline"))
        assert inner.late is not None
        inner.late.start()
        inner.late.join()
    loser, = [r for r in load_ledger(tmp_path) if r.source == "extra"]
    assert loser.discarded and loser.stage is Stage.CANDIDATE
    assert loser.role is Role.GENERATOR and loser.round == 0


def test_a_failed_calls_error_row_carries_what_was_billed(tmp_path: Path):
    """A final ModelError carries its billed usage into the error row."""
    from codeverse3d.models.base import ModelError

    err = ModelError("bad json after retries", retryable=True, attempts=6, usage=_usage())
    with run_ledger(tmp_path, run="r1"), pytest.raises(ModelError):
        MeteredChatModel(FakeChat(err)).generate(
            ChatRequest(messages=[ChatMessage.user("x")], label="planner"))
    (row,) = load_ledger(tmp_path)
    assert row.outcome == "error" and row.attempts == 6
    assert row.input_tokens == 12_000 and row.cost_usd > 0, "a billed failure is no longer a $0 row"
