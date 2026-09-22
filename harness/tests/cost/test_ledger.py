"""The append-only ledger: writing, reading, pricing provenance, aggregation."""

from __future__ import annotations

from pathlib import Path

from codeverse3d.contracts.common import Usage
from codeverse3d.cost import CostLedger, load_ledger, record_call, summarise
from codeverse3d.cost.ledger import bound_ledger, existing_ledger_path, open_run_ledger
from codeverse3d.cost.types import Role, Stage


def _usage(**kw: object) -> Usage:
    base = {"backend": "gemini", "model": "gemini-3.7-flash", "input_tokens": 100_000,
            "cached_tokens": 80_000, "output_tokens": 1_000}
    base.update(kw)
    return Usage(**base)  # type: ignore[arg-type]


def test_record_call_prices_and_stamps_provenance(tmp_path: Path):
    row = record_call(_usage(), run="r1", stage="judge", ledger=tmp_path / "l.jsonl")
    # 20k uncached * 0.75 + 80k cached * 0.075 + 1k out * 3.75, per 1M
    assert abs(row.cost_usd - (0.015 + 0.006 + 0.00375)) < 1e-9
    assert row.role is Role.JUDGE and row.stage is Stage.JUDGE  # role inferred from stage
    assert row.provider == "gemini" and row.model == "gemini-3.7-flash"
    assert row.price_source == "exact" and row.price_approximate is False and row.price_checked
    assert row.cache_hit is True and row.n_calls == 1


def test_keeps_the_billed_cost_but_can_reprice(tmp_path: Path):
    billed = record_call(_usage(cost_usd=0.123), run="r", stage="baseline", ledger=tmp_path / "a.jsonl")
    assert billed.cost_usd == 0.123 and billed.recorded_usd == 0.123
    repriced = record_call(_usage(cost_usd=0.123), run="r", stage="baseline", reprice=True,
                           ledger=tmp_path / "a.jsonl")
    assert abs(repriced.cost_usd - 0.02475) < 1e-9 and repriced.recorded_usd == 0.123


def test_append_only_and_reload(tmp_path: Path):
    led = CostLedger(tmp_path / "l.jsonl")
    for i in range(3):
        record_call(_usage(), run="r", round=i, stage="refine", ledger=led)
    rows = load_ledger(tmp_path / "l.jsonl")
    assert len(rows) == 3 and [r.round for r in rows] == [0, 1, 2]
    record_call(_usage(), run="r", round=3, stage="refine", ledger=led)
    assert len(load_ledger(tmp_path / "l.jsonl")) == 4  # nothing rewritten


def test_bad_line_does_not_lose_the_file(tmp_path: Path):
    p = tmp_path / "l.jsonl"
    record_call(_usage(), run="r", stage="plan", ledger=p)
    with p.open("a") as fh:
        fh.write("{not json\n")
    record_call(_usage(), run="r", stage="judge", ledger=p)
    assert len(load_ledger(p)) == 2


def test_unknown_model_is_recorded_not_raised(tmp_path: Path):
    row = record_call(_usage(model="totally-made-up-9"), run="r", stage="plan", ledger=tmp_path / "l.jsonl")
    assert row.cost_usd == 0.0 and row.price_source == "unknown" and row.price_approximate is True


def test_provider_reported_cost_is_flagged(tmp_path: Path):
    """A subscription CLI bills us a cost our table cannot reproduce: keep the
    dollar, mark it unauditable."""
    row = record_call(_usage(backend="claude-code", model="claude-not-a-model", cost_usd=1.17),
                      run="r", stage="baseline", ledger=tmp_path / "l.jsonl")
    assert row.cost_usd == 1.17 and row.price_source == "provider-reported"


def test_summarise_dimensions(tmp_path: Path):
    p = tmp_path / "l.jsonl"
    record_call(_usage(), run="a", stage="baseline", ledger=p)
    record_call(_usage(model="gemini-3.1-pro-preview", cached_tokens=0), run="a", stage="judge", ledger=p)
    s = summarise(load_ledger(p))
    assert set(s.dimension("stage")) == {"baseline", "judge"}
    assert abs(s.total.cost_usd - sum(b.cost_usd for b in s.dimension("role").values())) < 1e-12
    assert s.dimension("stage")["baseline"].cached_fraction == 0.8
    assert s.total.usd_per_1k_tokens > 0


def test_a_bound_ledger_takes_the_calls_that_name_no_file(tmp_path: Path):
    with bound_ledger(tmp_path / "default.jsonl"):
        record_call(_usage(), run="r", stage="plan")
    assert len(load_ledger(tmp_path / "default.jsonl")) == 1
    record_call(_usage(), run="r", stage="plan", ledger=tmp_path / "after.jsonl")
    assert len(load_ledger(tmp_path / "default.jsonl")) == 1  # the binding is gone with the block


def test_a_run_has_exactly_one_ledger_file_and_the_root_name_is_its_symlink(tmp_path: Path):
    """``open_run_ledger`` writes only ``telemetry/cost.jsonl``; the root
    ``cost_ledger.jsonl`` others still open by name is a symlink to it, so the reader
    must not treat that name as a second, separate ledger."""
    led = open_run_ledger(tmp_path)
    assert existing_ledger_path(tmp_path) is None  # nothing written yet
    record_call(_usage(), run="r", stage="baseline", ledger=led)
    assert existing_ledger_path(tmp_path) == tmp_path / "telemetry" / "cost.jsonl"
    alias = tmp_path / "cost_ledger.jsonl"
    assert alias.is_symlink() and len(load_ledger(alias)) == 1
    assert len(load_ledger(tmp_path)) == 1  # the directory reads the one file, not two
