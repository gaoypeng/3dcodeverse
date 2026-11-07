"""Metering: every ChatModel.generate becomes a ledger row."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.contracts.chat import ChatMessage, ChatRequest, ChatResponse
from codeverse3d.contracts.common import Usage
from codeverse3d.cost.instrument import (
    MeteredChatModel,
    run_ledger,
)
from codeverse3d.cost.ledger import load_ledger
from codeverse3d.cost.types import Role, Stage


def _usage(**kw: object) -> Usage:
    base = {"backend": "gemini", "model": "gemini-3.8-flash", "input_tokens": 12_000,
            "cached_tokens": 8_000, "output_tokens": 300, "latency_ms": 1234}
    base.update(kw)
    return Usage(**base)  # type: ignore[arg-type]


class FakeChat:
    provider = "gemini"
    model = "gemini-3.8-flash"

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.requests: list[ChatRequest] = []

    @property
    def id(self) -> str:
        return "gemini:gemini-3.8-flash"

    def generate(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return ChatResponse(text="ok", usage=_usage())


def test_a_metered_call_becomes_one_priced_row(tmp_path: Path):
    chat = FakeChat()
    with run_ledger(tmp_path, run="r1"):
        resp = MeteredChatModel(chat).generate(
            ChatRequest(messages=[ChatMessage.user("x")], label="judge:static_object:r01:s0"))
    assert resp.text == "ok"
    (row,) = load_ledger(tmp_path)
    assert row.run == "r1" and row.stage is Stage.JUDGE and row.role is Role.JUDGE and row.round == 1
    assert row.input_tokens == 12_000 and row.cached_tokens == 8_000 and row.cache_hit is True
    assert row.price_source == "exact" and row.price_input == 0.75 and row.price_checked
    assert row.cost_usd > 0


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


def test_a_billed_but_invalid_attempt_joins_its_winners_row(tmp_path: Path):
    """A charged invalid attempt is counted exactly once, inside its winner's one row."""
    from tests.models.test_gemini import make_model, text_response

    m, _log, _ = make_model([text_response("not json"), text_response('{"ok": true}')])
    with run_ledger(tmp_path, run="r1"):
        resp = MeteredChatModel(m).generate(
            ChatRequest(messages=[ChatMessage.user("x")], response_schema={"type": "object"},
                        label="planner"))
    assert resp.parsed == {"ok": True}
    (row,) = load_ledger(tmp_path)
    assert row.outcome == "ok" and row.attempts == 2 and row.source == "live"
    assert row.input_tokens == 200 and row.cost_usd > 0   # the bad reply's 100 + the winner's 100


def test_a_failed_call_is_recorded_re_raised_and_carries_what_was_billed(tmp_path: Path):
    """A failure is one error row: its outcome, its attempts, no key, and the usage it was billed."""
    from codeverse3d.models.base import ModelError

    cases = (
        (TimeoutError("boom"), dict(outcome="timeout", attempts=0, input_tokens=0)),
        (ModelError("503 high demand", retryable=True, status=503, attempts=7),
         dict(outcome="error", attempts=7, input_tokens=0)),
        (ModelError("bad json after retries", retryable=True, attempts=6, usage=_usage()),
         dict(outcome="error", attempts=6, input_tokens=12_000)),
    )
    for i, (err, want) in enumerate(cases):
        ws = tmp_path / str(i)
        with run_ledger(ws, run="r1"), pytest.raises(type(err)):
            MeteredChatModel(FakeChat(err)).generate(
                ChatRequest(messages=[ChatMessage.user("x")], label="planner"))
        (row,) = load_ledger(ws)
        assert row.stage is Stage.PLAN and row.key == ""
        assert (row.outcome, row.attempts, row.input_tokens) == tuple(want.values()), err
        assert (row.cost_usd > 0) is (want["input_tokens"] > 0), "a billed failure is no longer a $0 row"


