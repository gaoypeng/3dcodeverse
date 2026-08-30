"""Cost replay, ledger attribution, and billed-versus-notional accounting."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from codeverse.contracts.agent import AgentJob, AgentResult
from codeverse.contracts.common import Budget, Usage
from codeverse.cost.instrument import MeteredAgent, run_ledger
from codeverse.cost.ledger import load_ledger, record_call
from codeverse.orchestrator import BudgetExceeded, BudgetGuard, usage_delta
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


def replay(name: str, root: Path | None, *, texture: bool = True) -> BudgetGuard:
    """Spend one recorded run's money through today's accounting path.

    The guard only buckets and enforces; the ledger rows come from the ONE writer —
    ``MeteredAgent`` for a session, ``record_call`` (what ``MeteredChatModel`` does
    per call) for the judge and texture money — inside ``run_ledger(root)``, as
    ``BaseTrack.run`` opens it.  ``root=None`` replays with no ledger at all."""
    fx = FIXTURES[name]
    guard = BudgetGuard(Budget(max_minutes=10_000))
    tasks: dict[tuple[str, int], list[dict]] = {}
    for s in fx["sessions"]:
        tasks.setdefault((s["job_label"] or s["label"], int(s["round"] or 0)), []).append(s)
    with run_ledger(root or Path("/nonexistent"), run=name, create=root is not None):
        for (label, rnd), sessions in tasks.items():
            sessions.sort(key=lambda s: s["attempt"])
            task = GenerationTask(label=label, prompt="p", round=rnd,
                                  kind="baseline" if label.startswith("baseline") else "refine")
            acc = _SessionAcc(task=task, budget=guard)
            agent = MeteredAgent(_RecordedAgent(sessions))
            for i in range(len(sessions)):  # attempt 1, then the .a2 retry — exactly as run_agent_task does
                job_label = label if i == 0 else f"{label}.a{i + 1}"
                acc.run(agent, AgentJob(workspace="/tmp", prompt="p", label=job_label, round=rnd))
        for j in fx["judges"]:  # steps.run_round: the judge's MeteredChatModel writes, the guard buckets
            u = Usage(cost_usd=j["cost_usd"], input_tokens=j["input_tokens"], output_tokens=j["output_tokens"])
            record_call(u, round=j["round"], stage="judge", role="judge", label=j["rubric"])
            guard.add(u, stage="judge")
        if texture:
            for t in fx["post_hoc_texture_passes"]:  # lifecycle._texture_pass
                u = Usage(cost_usd=t["cost_usd"])
                record_call(u, stage="texture", role="image", label="texture_pass")
                guard.add(u, stage="texture")
    return guard


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_recorded_dollar_reaches_the_guard_and_the_ledger(name, tmp_path):
    fx = FIXTURES[name]
    guard = replay(name, tmp_path)
    sessions = sum(s["cost_usd"] for s in fx["sessions"])
    judges = sum(j["cost_usd"] for j in fx["judges"])
    texture = sum(t["cost_usd"] for t in fx["post_hoc_texture_passes"])
    # nothing is lost between the sessions and the guard — retries included
    assert guard.spent.cost_usd == pytest.approx(sessions + judges + texture, abs=1e-6)
    # and the ledger the run ships is the same number, one row per call
    rows = load_ledger(tmp_path)  # telemetry/cost.jsonl, one writer
    assert len(rows) == len(fx["sessions"]) + len(fx["judges"]) + len(fx["post_hoc_texture_passes"])
    assert sum(r.cost_usd for r in rows) == pytest.approx(guard.spent.cost_usd, abs=1e-6)
    # a retried session is its OWN row, labelled .a2 — the reconstructor had to guess
    # this from a trajectory directory name, and double-counted when it guessed wrong
    retried = [s for s in fx["sessions"] if s["attempt"] > 1]
    assert sum(1 for r in rows if r.label.endswith(".a2")) == len(retried)


def test_the_round_the_budget_cut_is_still_reported(tmp_path):
    fx = FIXTURES["tool_med_hand_drill"]
    assert fx["rounds_in_record"] == [0, 1]
    cut = [s for s in fx["sessions"] if s["round"] == 2]
    assert cut, "fixture must contain the round the budget cut"
    guard = replay("tool_med_hand_drill", tmp_path)
    burned = sum(s["cost_usd"] for s in cut)
    # per-round money lives in the ledger (CallCost.round), the one place that keeps it;
    # the guard totals and enforces, and its own by_round map was retired 2026-08-30
    assert sum(r.cost_usd for r in load_ledger(tmp_path) if r.round == 2) == pytest.approx(burned, abs=1e-6)
    assert burned > 0.8  # $0.86 that record.rounds never mentioned
    assert guard.spent.cost_usd > burned  # and it is inside the run total, not beside it


def test_post_hoc_texture_passes_are_inside_the_total_now():
    fx = FIXTURES["furn_hard_rolltop_desk"]
    with_tex = replay("furn_hard_rolltop_desk", None)
    without = replay("furn_hard_rolltop_desk", None, texture=False)
    off_record = with_tex.spent.cost_usd - without.spent.cost_usd
    assert off_record == pytest.approx(0.6562, abs=1e-3)
    assert without.spent.cost_usd == pytest.approx(fx["recorded_total_usd"], abs=0.02)
    assert with_tex.by_stage["texture"] == pytest.approx(0.6562, abs=1e-3)


# ----------------------------------------------------------------------------- guard mechanics
def test_spend_buckets_by_stage_and_only_charge_enforces():
    g0 = BudgetGuard(Budget(max_minutes=60))
    g0.charge(Usage(cost_usd=0.4), stage="baseline")
    g0.add(Usage(cost_usd=0.05), stage="judge")
    assert g0.by_stage == {"baseline": pytest.approx(0.4), "judge": pytest.approx(0.05)}
    assert list(g0.stage_summary())[0] == "baseline"  # biggest bucket first

    g = BudgetGuard(Budget(max_minutes=1.0), start_time=time.time() - 600)  # already past
    g.add(Usage(cost_usd=0.9), stage="texture")  # over the ceiling, never raises
    assert not g.ok() and g.spent.cost_usd == pytest.approx(0.9)
    with pytest.raises(BudgetExceeded):
        g.charge(Usage(cost_usd=0.01), stage="refine")
    assert g.spent.cost_usd == pytest.approx(0.91)  # the money is counted even when it raises


def test_usage_delta_reports_what_a_round_burned():
    before = Usage(cost_usd=1.0, input_tokens=100, output_tokens=10)
    after = Usage(cost_usd=1.75, input_tokens=350, output_tokens=40)
    d = usage_delta(after, before)
    assert d.cost_usd == pytest.approx(0.75) and d.input_tokens == 250 and d.output_tokens == 30
    assert usage_delta(before, after).cost_usd == 0.0  # never negative


def test_the_guard_never_writes_a_ledger_row_itself(tmp_path, monkeypatch):
    """One writer: a charge outside any run ledger lands nowhere, not in a second file."""
    monkeypatch.setenv("CV3D_COST_LEDGER", str(tmp_path / "process.jsonl"))
    from codeverse.cost import ledger as ledger_mod

    monkeypatch.setattr(ledger_mod, "_fallback_read", False)
    quiet = BudgetGuard(Budget(max_minutes=60))
    quiet.charge(Usage(cost_usd=0.1), stage="plan")
    assert quiet.spent.cost_usd == pytest.approx(0.1)
    assert not (tmp_path / "process.jsonl").exists() and not any(tmp_path.glob("**/*.jsonl"))


def test_backend_billing_and_time_guard():
    g = BudgetGuard(Budget(max_minutes=60.0), soft_fraction=0.55)
    g.add(Usage(backend="codex", model="gpt-5.6-sol", cost_usd=7.712))
    assert g.billed_usd == 0.0
    assert g.soft_exceeded() == "" and g.ok()
    assert g.spent.cost_usd == pytest.approx(7.712)
    assert g.summary()["notional_usd"] == pytest.approx(7.712)
    assert g.summary()["spent_usd"] == 0.0

    g.add(Usage(backend="gemini", cost_usd=5.0))
    assert g.billed_usd == pytest.approx(5.0)
    assert g.ok() and not g.soft_exceeded()

    g.add(Usage(backend="some-new-provider", cost_usd=4.0))
    assert g.billed_usd == pytest.approx(9.0)
    g._active_s = 3600.0  # noqa: SLF001
    with pytest.raises(BudgetExceeded):
        g.check()
