"""Closing the accounting holes docs/COST.md §6 found, and the money stops of §5.

The audit measured $4.80 of real spend that never reached ``record.total_usage``
(and therefore never reached ``BudgetGuard``): rounds the budget cut after the
work was done, retried ``.a2`` agent sessions, post-hoc texture passes.  These
tests replay the *recorded* usage of three of those runs
(``data/offrecord_runs.json``, taken from the read-only bench runs) through the
production accounting objects and assert that every dollar now lands in the
guard, in one ledger row, and in a round bucket.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse.contracts.agent import AgentJob, AgentResult
from codeverse.contracts.common import Budget, Usage
from codeverse.cost.ledger import load_ledger
from codeverse.orchestrator.budget import BudgetExceeded, BudgetGuard, usage_delta
from codeverse.tracks.generation import GenerationTask, _SessionAcc

FIXTURES = json.loads((Path(__file__).parent / "data" / "offrecord_runs.json").read_text())["runs"]


def _usage(row: dict) -> Usage:
    return Usage(backend=row.get("backend") or "api-agent", model=row.get("model") or "gemini:gemini-3.7-flash",
                 cost_usd=row["cost_usd"], input_tokens=row["input_tokens"], output_tokens=row["output_tokens"],
                 cached_tokens=row.get("cached_tokens", 0))


class _RecordedAgent:
    """Replays the recorded sessions of one task, attempt by attempt."""

    kind = "api-agent"
    model = "gemini:gemini-3.7-flash"

    def __init__(self, sessions: list[dict]):
        self.sessions = list(sessions)

    def run(self, job: AgentJob) -> AgentResult:
        s = self.sessions.pop(0)
        return AgentResult(ok=True, exit_reason=s["exit_reason"] or "completed", usage=_usage(s))


def replay(name: str, ledger: Path | None, *, texture: bool = True) -> BudgetGuard:
    """Spend one recorded run's money through today's accounting path."""
    fx = FIXTURES[name]
    guard = BudgetGuard(Budget(max_usd=10_000, max_minutes=10_000), run=name, ledger=ledger)
    tasks: dict[tuple[str, int], list[dict]] = {}
    for s in fx["sessions"]:
        tasks.setdefault((s["job_label"] or s["label"], int(s["round"] or 0)), []).append(s)
    for (label, rnd), sessions in tasks.items():
        sessions.sort(key=lambda s: s["attempt"])
        task = GenerationTask(label=label, prompt="p", round=rnd,
                              kind="baseline" if label.startswith("baseline") else "refine")
        acc = _SessionAcc(task=task, budget=guard)
        agent = _RecordedAgent(sessions)
        for _ in sessions:  # attempt 1, then the .a2 retry — exactly as run_agent_task does
            acc.run(agent, AgentJob(workspace="/tmp", prompt="p", label=label, round=rnd))
    for j in fx["judges"]:  # steps.run_round
        guard.add(Usage(cost_usd=j["cost_usd"], input_tokens=j["input_tokens"], output_tokens=j["output_tokens"]),
                  stage="judge", role="judge", round_index=j["round"], label=j["rubric"])
    if texture:
        for t in fx["post_hoc_texture_passes"]:  # lifecycle._texture_pass
            guard.add(Usage(cost_usd=t["cost_usd"]), stage="texture", role="image", label="texture_pass")
    return guard


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_recorded_dollar_reaches_the_guard_and_the_ledger(name, tmp_path):
    fx = FIXTURES[name]
    ledger = tmp_path / "cost_ledger.jsonl"
    guard = replay(name, ledger)
    sessions = sum(s["cost_usd"] for s in fx["sessions"])
    judges = sum(j["cost_usd"] for j in fx["judges"])
    texture = sum(t["cost_usd"] for t in fx["post_hoc_texture_passes"])
    # nothing is lost between the sessions and the guard — retries included
    assert guard.spent.cost_usd == pytest.approx(sessions + judges + texture, abs=1e-6)
    # and the ledger the run ships is the same number, one row per call
    rows = load_ledger(ledger)
    assert len(rows) == len(fx["sessions"]) + len(fx["judges"]) + len(fx["post_hoc_texture_passes"])
    assert sum(r.cost_usd for r in rows) == pytest.approx(guard.spent.cost_usd, abs=1e-6)
    # a retried session is its OWN row, labelled .a2 — the reconstructor had to guess
    # this from a trajectory directory name, and double-counted when it guessed wrong
    retried = [s for s in fx["sessions"] if s["attempt"] > 1]
    assert sum(1 for r in rows if r.label.endswith(".a2")) == len(retried)


def test_the_round_the_budget_cut_is_still_reported():
    """tool_med_hand_drill spent $0.86 on r02 and then stopped: the round is not in
    ``record.rounds`` at all, so its dollars used to be attributable to nothing."""
    fx = FIXTURES["tool_med_hand_drill"]
    assert fx["rounds_in_record"] == [0, 1]
    cut = [s for s in fx["sessions"] if s["round"] == 2]
    assert cut, "fixture must contain the round the budget cut"
    guard = replay("tool_med_hand_drill", None)
    burned = sum(s["cost_usd"] for s in cut)
    assert guard.round_costs(2)["refine"] == pytest.approx(burned, abs=1e-6)
    assert burned > 0.8  # $0.86 that record.rounds never mentioned


def test_post_hoc_texture_passes_are_inside_the_total_now():
    """furn_hard_rolltop_desk ran four texture passes for $0.656; ``record.total_usage``
    said $1.2165.  Charged through the guard the run reports $1.87."""
    fx = FIXTURES["furn_hard_rolltop_desk"]
    with_tex = replay("furn_hard_rolltop_desk", None)
    without = replay("furn_hard_rolltop_desk", None, texture=False)
    off_record = with_tex.spent.cost_usd - without.spent.cost_usd
    assert off_record == pytest.approx(0.6562, abs=1e-3)
    assert without.spent.cost_usd == pytest.approx(fx["recorded_total_usd"], abs=0.02)
    assert with_tex.by_stage["texture"] == pytest.approx(0.6562, abs=1e-3)


# ----------------------------------------------------------------------------- guard mechanics
def test_spend_buckets_by_stage_and_round_and_only_charge_enforces():
    g = BudgetGuard(Budget(max_usd=1.0, max_minutes=60))
    g.charge(Usage(cost_usd=0.4), stage="baseline", round_index=0, label="baseline")
    g.add(Usage(cost_usd=0.05), stage="judge", role="judge", round_index=0)
    assert g.by_stage == {"baseline": pytest.approx(0.4), "judge": pytest.approx(0.05)}
    assert g.round_costs(0)["judge"] == pytest.approx(0.05) and g.round_costs(9) == {}
    g.add(Usage(cost_usd=0.9), stage="texture", role="image")  # over the ceiling, but never raises
    assert not g.ok() and g.spent.cost_usd == pytest.approx(1.35)
    with pytest.raises(BudgetExceeded):
        g.charge(Usage(cost_usd=0.01), stage="refine")
    assert g.spent.cost_usd == pytest.approx(1.36)  # the money is counted even when it raises
    assert list(g.stage_summary())[0] == "texture"  # biggest bucket first


def test_usage_delta_reports_what_a_round_burned():
    before = Usage(cost_usd=1.0, input_tokens=100, output_tokens=10)
    after = Usage(cost_usd=1.75, input_tokens=350, output_tokens=40)
    d = usage_delta(after, before)
    assert d.cost_usd == pytest.approx(0.75) and d.input_tokens == 250 and d.output_tokens == 30
    assert usage_delta(before, after).cost_usd == 0.0  # never negative


def test_the_ledger_is_optional_and_never_breaks_a_run(tmp_path):
    g = BudgetGuard(Budget(max_usd=1.0, max_minutes=60), ledger=tmp_path / "sub" / "dir" / "l.jsonl")
    g.charge(Usage(cost_usd=0.1), stage="plan", role="planner")
    assert len(load_ledger(tmp_path / "sub" / "dir" / "l.jsonl")) == 1
    quiet = BudgetGuard(Budget(max_usd=1.0, max_minutes=60))  # no ledger configured
    quiet.charge(Usage(cost_usd=0.1))
    assert quiet.spent.cost_usd == pytest.approx(0.1)


# ------------------------------------------------- subscription backends vs the guard
def test_a_subscription_backend_does_not_consume_the_spend_guard():
    """``max_usd`` guards money, and a flat-rate CLI costs none.

    Measured 2026-08-25, ``tsr_scn_temple_night`` (``codex:gpt-5.6-sol``,
    ``--profile quality``): two Blender hero sessions priced at OpenAI list rates put the
    run at $7.712 against the $4.40 soft cap in 6.8 minutes, so the asset judge was
    skipped for BOTH heroes and every later stage ran degraded — over a bill of $0.00.
    The run's own cost ledger said $0.00; only the guard disagreed.
    """
    from codeverse.contracts.common import Budget, Usage
    from codeverse.orchestrator.budget import BudgetGuard

    g = BudgetGuard(Budget(max_usd=8.0, max_minutes=60.0), soft_fraction=0.55)
    g.spend(Usage(backend="codex", model="gpt-5.6-sol", cost_usd=7.712), enforce=False)

    assert g.billed_usd == 0.0
    assert g.soft_exceeded() == ""  # the degradation that actually happened
    assert g.ok()
    # the notional price is NOT discarded — reports and $/complexity still want it
    assert g.spent.cost_usd == pytest.approx(7.712)
    assert g.summary()["notional_usd"] == pytest.approx(7.712)
    assert g.summary()["spent_usd"] == 0.0


def test_the_guard_still_enforces_backends_that_really_bill():
    """The exemption must not become a hole: API backends keep both ceilings, and an
    unclassified backend is enforced rather than exempted."""
    from codeverse.contracts.common import Budget, Usage
    from codeverse.orchestrator.budget import BudgetExceeded, BudgetGuard

    g = BudgetGuard(Budget(max_usd=8.0, max_minutes=60.0), soft_fraction=0.55)
    g.spend(Usage(backend="codex", cost_usd=99.0), enforce=False)  # free, ignored
    g.spend(Usage(backend="gemini", cost_usd=5.0), enforce=False)
    assert g.soft_exceeded()  # soft cap fires on the real $5
    assert g.ok()  # but the hard ceiling has not

    g.spend(Usage(backend="some-new-provider", cost_usd=4.0), enforce=False)
    assert g.billed_usd == pytest.approx(9.0), "an unknown backend must bill, not be exempt"
    with pytest.raises(BudgetExceeded):
        g.check()
