"""The append-only ledger: writing, reading, pricing provenance, aggregation."""

from __future__ import annotations

from pathlib import Path

from codeverse3d.contracts.common import Usage
from codeverse3d.cost import load_ledger, record_call
from codeverse3d.cost.ledger import existing_ledger_path, open_run_ledger


def _usage(**kw: object) -> Usage:
    base = {"backend": "gemini", "model": "gemini-3.7-flash", "input_tokens": 100_000,
            "cached_tokens": 80_000, "output_tokens": 1_000}
    base.update(kw)
    return Usage(**base)  # type: ignore[arg-type]


def test_keeps_the_billed_cost_but_can_reprice(tmp_path: Path):
    billed = record_call(_usage(cost_usd=0.123), run="r", stage="baseline", ledger=tmp_path / "a.jsonl")
    assert billed.cost_usd == 0.123 and billed.recorded_usd == 0.123
    repriced = record_call(_usage(cost_usd=0.123), run="r", stage="baseline", reprice=True,
                           ledger=tmp_path / "a.jsonl")
    assert abs(repriced.cost_usd - 0.02475) < 1e-9 and repriced.recorded_usd == 0.123


def test_bad_line_does_not_lose_the_file(tmp_path: Path):
    p = tmp_path / "l.jsonl"
    record_call(_usage(), run="r", stage="plan", ledger=p)
    with p.open("a") as fh:
        fh.write("{not json\n")
    record_call(_usage(), run="r", stage="judge", ledger=p)
    assert len(load_ledger(p)) == 2


def test_provider_reported_cost_is_flagged(tmp_path: Path):
    """A subscription CLI bills us a cost our table cannot reproduce: keep the
    dollar, mark it unauditable."""
    row = record_call(_usage(backend="claude-code", model="claude-not-a-model", cost_usd=1.17),
                      run="r", stage="baseline", ledger=tmp_path / "l.jsonl")
    assert row.cost_usd == 1.17 and row.price_source == "provider-reported"


def test_a_run_has_exactly_one_ledger_file(tmp_path: Path):
    """``open_run_ledger`` writes only ``telemetry/cost.jsonl``, and that is the only name read."""
    led = open_run_ledger(tmp_path)
    assert existing_ledger_path(tmp_path) is None  # nothing written yet
    record_call(_usage(), run="r", stage="baseline", ledger=led)
    assert existing_ledger_path(tmp_path) == tmp_path / "telemetry" / "cost.jsonl"
    assert sorted(p.name for p in tmp_path.rglob("*.jsonl")) == ["cost.jsonl"]
    assert len(load_ledger(tmp_path)) == 1
