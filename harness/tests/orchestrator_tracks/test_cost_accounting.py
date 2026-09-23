"""Recorded runs replayed through today's accounting: every billed dollar is one ledger row."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.contracts.agent import AgentJob, AgentResult
from codeverse3d.contracts.common import Budget, Usage
from codeverse3d.cost.instrument import MeteredAgent, run_ledger
from codeverse3d.cost.ledger import load_ledger, record_call
from codeverse3d.orchestrator import BudgetGuard
from codeverse3d.tracks.generation import GenerationTask, _SessionAcc

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


def replay(name: str, root: Path) -> float:
    """Spend one recorded run's money through today's accounting path and return the ledger's
    total: the ONE writer — ``MeteredAgent`` for a session, ``record_call`` (what
    ``MeteredChatModel`` does per call) for the judge and texture money — inside
    ``run_ledger(root)``, as ``BaseTrack.run`` opens it."""
    fx = FIXTURES[name]
    guard = BudgetGuard(Budget(max_minutes=10_000))
    tasks: dict[tuple[str, int], list[dict]] = {}
    for s in fx["sessions"]:
        tasks.setdefault((s["job_label"] or s["label"], int(s["round"] or 0)), []).append(s)
    with run_ledger(root, run=name):
        for (label, rnd), sessions in tasks.items():
            sessions.sort(key=lambda s: s["attempt"])
            task = GenerationTask(label=label, prompt="p", round=rnd,
                                  kind="baseline" if label.startswith("baseline") else "refine")
            acc = _SessionAcc(task=task, budget=guard)
            agent = MeteredAgent(_RecordedAgent(sessions))
            for i in range(len(sessions)):  # attempt 1, then the .a2 retry — exactly as run_agent_task does
                job_label = label if i == 0 else f"{label}.a{i + 1}"
                acc.run(agent, AgentJob(workspace="/tmp", prompt="p", label=job_label, round=rnd))
        for j in fx["judges"]:  # the judge's MeteredChatModel writes one row per verdict call
            u = Usage(cost_usd=j["cost_usd"], input_tokens=j["input_tokens"], output_tokens=j["output_tokens"])
            record_call(u, round=j["round"], stage="judge", role="judge", label=j["rubric"])
        for t in fx["post_hoc_texture_passes"]:  # a texture pass joins the run's ledger
            record_call(Usage(cost_usd=t["cost_usd"]), stage="texture", role="image", label="texture_pass")
    return sum(r.cost_usd for r in load_ledger(root))


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_recorded_dollar_reaches_the_ledger_once(name, tmp_path):
    fx = FIXTURES[name]
    total = replay(name, tmp_path)
    sessions = sum(s["cost_usd"] for s in fx["sessions"])
    judges = sum(j["cost_usd"] for j in fx["judges"])
    texture = sum(t["cost_usd"] for t in fx["post_hoc_texture_passes"])
    # nothing is lost between the sessions and the ledger — retries included — and nothing twice
    assert total == pytest.approx(sessions + judges + texture, abs=1e-6)
    rows = load_ledger(tmp_path)  # telemetry/cost.jsonl, one writer, one row per call
    assert len(rows) == len(fx["sessions"]) + len(fx["judges"]) + len(fx["post_hoc_texture_passes"])
    # a retried session is its OWN row, labelled .a2
    retried = [s for s in fx["sessions"] if s["attempt"] > 1]
    assert sum(1 for r in rows if r.label.endswith(".a2")) == len(retried)


def test_the_round_the_budget_cut_is_still_in_the_total(tmp_path):
    fx = FIXTURES["tool_med_hand_drill"]
    assert fx["rounds_in_record"] == [0, 1]
    cut = [s for s in fx["sessions"] if s["round"] == 2]
    assert cut, "fixture must contain the round the budget cut"
    total = replay("tool_med_hand_drill", tmp_path)
    burned = sum(s["cost_usd"] for s in cut)
    assert sum(r.cost_usd for r in load_ledger(tmp_path) if r.round == 2) == pytest.approx(burned, abs=1e-6)
    assert burned > 0.8 and total > burned  # $0.86 that record.rounds never mentioned, inside the total
