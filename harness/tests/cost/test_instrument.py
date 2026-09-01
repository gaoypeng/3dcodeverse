"""Metering: every ChatModel.generate and every CodingAgent.run becomes a ledger row."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse.contracts.agent import AgentJob, AgentResult
from codeverse.contracts.chat import ChatMessage, ChatRequest, ChatResponse
from codeverse.contracts.common import Usage
from codeverse.cost.context import run_binding
from codeverse.cost.instrument import (
    MeteredAgent,
    MeteredChatModel,
    run_ledger,
)
from codeverse.cost.ledger import load_ledger, record_call
from codeverse.cost.types import Role, Stage


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
    meters_own_calls = True
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
    kind = "gemini-cli"
    meters_own_calls = False   # a vendor CLI bills as ONE session row

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


def test_a_cli_agent_we_cannot_see_inside_gets_one_session_row(tmp_path: Path):
    with run_ledger(tmp_path, run="r1"):
        MeteredAgent(CliAgent(FakeChat())).run(
            AgentJob(workspace=str(tmp_path), prompt="p", label="baseline", round=0, kind="baseline"))
    (row,) = load_ledger(tmp_path)
    assert row.source == "session" and row.backend == "gemini-cli" and row.n_calls == 4
    assert row.stage is Stage.BASELINE and row.round == 0 and row.input_tokens == 50_000


def test_a_session_row_is_filed_by_the_task_kind(tmp_path: Path):
    """``job.kind`` is a bare task kind (``zone``, ``compose``, ``rebuild``); until 2026-08-29
    only the label PREFIXES were known here, so every scene zone session was ``other``."""
    with run_ledger(tmp_path, run="r1"):
        for kind, label in (("zone", "zone_courtyard"), ("compose", "compose"), ("rebuild", "rebuild"),
                            ("asset", "asset_koi"), ("candidate", "baseline_c1")):
            MeteredAgent(CliAgent(FakeChat())).run(
                AgentJob(workspace=str(tmp_path), prompt="p", label=label, round=0, kind=kind))
    assert [r.stage for r in load_ledger(tmp_path)] == [Stage.ZONES, Stage.ASSEMBLE, Stage.REPAIR,
                                                        Stage.ASSETS, Stage.CANDIDATE]


def test_get_coding_agent_hands_out_a_metered_agent(monkeypatch: pytest.MonkeyPatch):
    from codeverse.agents import registry

    monkeypatch.setattr(registry, "_build_agent", lambda aid: CliAgent(FakeChat()))
    assert isinstance(registry.get_coding_agent("gemini-cli:m"), MeteredAgent)


def test_run_ledger_writes_telemetry_and_leaves_a_root_alias(tmp_path: Path):
    with run_ledger(tmp_path, run="r1"):
        MeteredChatModel(FakeChat()).generate(ChatRequest(messages=[ChatMessage.user("x")], label="planner"))
    assert (tmp_path / "telemetry" / "cost.jsonl").is_file()
    alias = tmp_path / "cost_ledger.jsonl"
    assert alias.is_symlink() and alias.is_file()  # resolves to the telemetry copy
    assert len(load_ledger(tmp_path)) == 1 == len(load_ledger(alias))


def test_accounting_never_breaks_a_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("ledger on fire")

    monkeypatch.setattr("codeverse.cost.instrument.record_call", boom)
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
    from codeverse.models import registry

    monkeypatch.setattr(registry, "_build_chat_model", lambda mid: FakeChat())
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
    assert not (tmp_path / "cost_ledger.jsonl").exists()
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


def test_an_in_process_session_is_never_counted_twice_even_from_another_thread(tmp_path: Path):
    """Worker-thread rows from a self-metered agent are not double counted."""
    from codeverse.proc import fan_out

    class ThreadedAgent(FakeAgent):
        def run(self, job: AgentJob) -> AgentResult:
            # fan_out, not a bare Thread: it is the one helper that copies the caller's
            # context, and since 2026-08-30 nothing else can find the run's ledger from
            # a worker thread (the process-global fallback leaked between parallel runs)
            (out,) = fan_out([job], lambda j: FakeAgent.run(self, j), label="agent")
            assert isinstance(out, AgentResult)
            return out

    with run_ledger(tmp_path, run="r1"):
        MeteredAgent(ThreadedAgent(MeteredChatModel(FakeChat()))).run(
            AgentJob(workspace=str(tmp_path), prompt="p", label="baseline", round=0, kind="baseline"))
    rows = load_ledger(tmp_path)
    assert len(rows) == 3 and not any(r.source == "session" for r in rows)
    assert sum(r.input_tokens for r in rows) == 36_000  # 3 turns, not 3 turns + a session


def test_a_backend_may_declare_that_it_meters_itself(tmp_path: Path):
    from codeverse.cost.instrument import meters_own_calls

    cli = CliAgent(FakeChat())
    assert meters_own_calls(cli) is False            # a vendor CLI bills as one session
    assert meters_own_calls(FakeAgent(FakeChat())) is True   # ...unless it declares otherwise
    cli.meters_own_calls = True  # type: ignore[attr-defined]
    assert meters_own_calls(cli) is True


# ------------------------------------------------------------------- nesting / parallelism
def test_run_ledgers_nest_and_restore_the_outer_one(tmp_path: Path):
    """Closing a nested ledger restores the outer context."""
    outer, inner = tmp_path / "cell", tmp_path / "cell" / "run"
    with run_ledger(outer, run="cell"):
        MeteredChatModel(FakeChat()).generate(ChatRequest(messages=[ChatMessage.user("x")], label="planner"))
        with run_ledger(inner, run="cell:run"):
            MeteredChatModel(FakeChat()).generate(
                ChatRequest(messages=[ChatMessage.user("x")], label="baseline"))
        MeteredChatModel(FakeChat()).generate(
            ChatRequest(messages=[ChatMessage.user("x")], label="judge:static_object_v1:r00:s0"))
    assert [r.stage for r in load_ledger(inner)] == [Stage.BASELINE]
    assert [r.stage for r in load_ledger(outer)] == [Stage.PLAN, Stage.JUDGE]
    assert {r.run for r in load_ledger(outer)} == {"cell"}


def test_parallel_runs_in_their_own_threads_keep_their_own_ledgers(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch):
    """`bench.run_bench` runs N prompts in N threads; their rows must not mix."""
    from concurrent.futures import ThreadPoolExecutor

    from codeverse.cost import ledger as ledger_mod

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
    # ...and neither binding leaked out of its worker.  Until 2026-08-30 the run name
    # and the default ledger were ALSO process globals that the first thread to exit
    # republished, so this plain main-thread call appended to a finished run's file
    # under that run's name instead of going to the per-process log.
    monkeypatch.setenv("CV3D_COST_LEDGER", str(tmp_path / "process.jsonl"))
    monkeypatch.setattr(ledger_mod, "_fallback", None)
    monkeypatch.setattr(ledger_mod, "_fallback_read", False)
    assert run_binding().run == ""
    record_call(Usage(cost_usd=0.5), label="baseline")
    assert [r.run for r in load_ledger(tmp_path / "process.jsonl")] == [""]
    for name in names:
        assert len(load_ledger(tmp_path / name)) == 3


# ------------------------------------------------------- key + attempts on the row (audit 2026-08-26 §4)
class KeyedChat(FakeChat):
    """A gemini-shaped response: ``raw`` names the key that answered and the round-trips."""

    def __init__(self, raw: dict):
        super().__init__()
        self.raw = raw

    def generate(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return ChatResponse(text="ok", usage=_usage(), raw=dict(self.raw))


def test_the_row_says_which_key_served_the_call_and_how_many_round_trips(tmp_path: Path):
    """Safe key identity and retry counts reach telemetry rows."""
    with run_ledger(tmp_path, run="r1"):
        MeteredChatModel(KeyedChat({"key": "…ab12", "attempts": 3, "hedged": 1})).generate(
            ChatRequest(messages=[ChatMessage.user("x")], label="planner"))
    (row,) = load_ledger(tmp_path)
    assert row.key == "…ab12" and row.attempts == 3


def test_a_full_key_never_reaches_the_ledger(tmp_path: Path):
    with run_ledger(tmp_path, run="r1"):
        MeteredChatModel(KeyedChat({"key": "AIzaSy-a-whole-secret-key-9999"})).generate(
            ChatRequest(messages=[ChatMessage.user("x")], label="planner"))
    (row,) = load_ledger(tmp_path)
    assert row.key == "…9999" and row.attempts == 0
    assert "secret" not in (tmp_path / "telemetry" / "cost.jsonl").read_text()


def test_a_failed_call_records_its_attempts_but_no_key(tmp_path: Path):
    from codeverse.models.base import ModelError

    err = ModelError("503 high demand", retryable=True, status=503, attempts=7)
    with run_ledger(tmp_path, run="r1"), pytest.raises(ModelError):
        MeteredChatModel(FakeChat(err)).generate(ChatRequest(messages=[ChatMessage.user("x")], label="planner"))
    (row,) = load_ledger(tmp_path)
    assert row.outcome == "error" and row.attempts == 7 and row.key == ""


def test_per_key_buckets_and_tries_per_call(tmp_path: Path):
    from types import SimpleNamespace

    from codeverse.cost.ledger import record_call, summarise
    from codeverse.cost.report import BUCKET_HEADERS, bucket_rows, keyed_buckets

    led = tmp_path / "cost.jsonl"
    rows = [
        record_call(_usage(), label="planner", ledger=led, key="…k1", attempts=1),
        record_call(_usage(), label="planner", ledger=led, key="…k1", attempts=3),
        record_call(_usage(), label="planner", ledger=led, key="…k2", attempts=1),
        record_call(_usage(), label="baseline", ledger=led, n_calls=4, source="session"),  # a CLI session: no key
    ]
    by = summarise(rows, dimensions=("key",)).dimension("key")
    assert by["…k1"].n_calls == 2 and by["…k1"].attempts_per_call == 2.0
    assert by["…k2"].attempts_per_call == 1.0
    assert by["(none)"].n_calls == 4 and by["(none)"].attempts_per_call == 0.0
    audit = SimpleNamespace(summary=summarise(rows, dimensions=("key",)))
    assert [b.key for b in keyed_buckets(audit)] == ["…k1", "…k2"], "most calls first, unkeyed rows left out"
    table = bucket_rows(keyed_buckets(audit), total=1.0)
    assert BUCKET_HEADERS[-1] == "tries/call" and table[0][-1] == "2.00" and table[1][-1] == "1.00"
# ------------------------------------------------------- per-attempt rows (audit 2026-08-27)
def test_a_billed_but_invalid_attempt_is_in_the_total_exactly_once(tmp_path: Path):
    """A charged invalid attempt is counted exactly once beside its winner."""
    from codeverse.cost.ledger import summarise
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


def test_a_hedge_losers_tokens_reach_the_ledger_when_it_lands(tmp_path: Path):
    """A late hedge loser's billed tokens reach the ledger total."""
    import threading
    import time as _time

    from codeverse.models.base import ModelError
    from codeverse.models.gemini import GeminiModel
    from codeverse.models.retry import KeyPool
    from tests.models.test_gemini import text_response

    release_k2 = threading.Event()

    class Client:
        def __init__(self, key: str):
            self.key, self.models = key, self

        def generate_content_stream(self, **kw):
            yield self.generate_content(**kw)

        def generate_content(self, *, model, contents, config):
            if self.key == "k1":
                raise ModelError("503 high demand", retryable=True, status=503)
            if self.key == "k2":
                assert release_k2.wait(5.0)
                return text_response("late loser")
            return text_response("winner")

    pool = KeyPool(["k1", "k2", "k3"], rpm_per_key=10_000)
    m = GeminiModel("gemini-3.7-flash", pool=pool, sleep=lambda s: None, client_factory=Client)
    with run_ledger(tmp_path, run="r1"):
        resp = MeteredChatModel(m).generate(
            ChatRequest(messages=[ChatMessage.user("x")], label="planner"))
    assert resp.text == "winner"
    release_k2.set()
    deadline = _time.monotonic() + 5.0
    loser = None
    while _time.monotonic() < deadline:
        rows = load_ledger(tmp_path)
        loser = next((r for r in rows if r.source == "extra" and r.key == "…k2"), None)
        if loser is not None:
            break
        _time.sleep(0.01)
    assert loser is not None, "the loser's row never arrived"
    assert loser.discarded and loser.input_tokens == 100, "the loser's paid tokens are on the ledger"
    logical, = [r for r in load_ledger(tmp_path) if r.source == "live"]
    # endswith, not ==: the 2-char fake key is shorter than the …last-4 redaction
    assert logical.key.endswith("k3") and logical.call_id == loser.call_id
    from codeverse.cost.ledger import summarise
    assert summarise(load_ledger(tmp_path)).total.cost_usd == pytest.approx(
        logical.cost_usd + loser.cost_usd), "the loser is in the total exactly once"


def test_zone_layout_rows_agree_with_the_guard(tmp_path: Path):
    """tracks/zone_layout.py labels its planner calls 'zone-layout' and charges the
    guard as stage='plan' — unclassified, the ledger filed the same dollars under
    other/other, so the two owners disagreed on every zone-layout cent (V10c)."""
    from codeverse.cost.types import role_for_stage, stage_for_label

    assert stage_for_label("zone-layout") is Stage.PLAN
    assert role_for_stage(stage_for_label("zone-layout")) is Role.PLANNER
    with run_ledger(tmp_path, run="r1"):
        record_call(_usage(cost_usd=0.02), label="zone-layout")
    row, = load_ledger(tmp_path)
    assert row.stage is Stage.PLAN and row.role is Role.PLANNER


def test_a_late_hedge_loser_keeps_its_stage_role_and_round(tmp_path: Path):
    """The loser lands AFTER generate() returned, from its own thread with empty
    contextvars: its 'extra' row used to fall back to what the label alone says
    (baseline/None) instead of the originating call's candidate/r0 attribution."""
    import threading

    from codeverse.cost.context import AttemptRecord, attempt_sink, call_context

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
    from codeverse.models.base import ModelError

    err = ModelError("bad json after retries", retryable=True, attempts=6, usage=_usage())
    with run_ledger(tmp_path, run="r1"), pytest.raises(ModelError):
        MeteredChatModel(FakeChat(err)).generate(
            ChatRequest(messages=[ChatMessage.user("x")], label="planner"))
    (row,) = load_ledger(tmp_path)
    assert row.outcome == "error" and row.attempts == 6
    assert row.input_tokens == 12_000 and row.cost_usd > 0, "a billed failure is no longer a $0 row"
