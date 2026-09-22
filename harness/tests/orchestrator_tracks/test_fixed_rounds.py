"""FIXED rounds (owner, 2026-09-22): a run is the baseline plus ``max_rounds`` refine rounds,
each built on the round before it, and only the clock or a hard failure ends it early."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.common import Language
from codeverse3d.contracts.run import RunStatus
from codeverse3d.proc import EventLog
from codeverse3d.tracks.static_object import StaticObjectTrack
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import fake_clock, make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeJudge,
    FakeRuntime,
    FakeServices,
    _planner,
)


def _track(chair_plan, settings, scores, *, agent=None) -> StaticObjectTrack:
    agent = agent or FakeAgent(lambda job, ws: {"src/model.py": f"import bpy  # {job.label} r{job.round}\n"})
    return StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=scores), agent=agent,
                             planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                             runtime=FakeRuntime(Language.BLENDER))


@pytest.mark.parametrize("scores", [
    (0.55, 0.85, 0.95),   # passes at r01 — once a "pass" stop
    (0.60, 0.60, 0.60),   # flat — once a plateau / diminishing-returns stop
    (0.70, 0.40, 0.30),   # regresses twice — once a rewrite, then a "regression" stop
], ids=["passing", "flat", "regressing"])
@pytest.mark.parametrize("max_rounds", [0, 3])
def test_a_run_is_the_baseline_plus_max_rounds_whatever_the_judge_says(tmp_path, chair_plan, settings, scores,
                                                                       max_rounds):
    ws = Workspace(tmp_path / "runs" / "fixed")
    rec = _track(chair_plan, settings, scores).run(make_spec(language=Language.BLENDER, max_rounds=max_rounds), ws)
    assert len(rec.rounds) == max_rounds + 1
    assert [r.kind for r in rec.rounds] == ["baseline"] + ["refine"] * max_rounds
    assert rec.status is RunStatus.MAX_ROUNDS and rec.extra["stop_reason"] == "max_rounds"
    assert all(r.judgment is not None for r in rec.rounds), "the judge still scores every round"
    stops = [e for e in EventLog(ws.events_path).read() if e["event"] == "stop"]
    assert [e["reason"] for e in stops] == ["max_rounds"]


def test_only_the_clock_stops_a_run_early(tmp_path, chair_plan, settings):
    agent = FakeAgent(lambda job, ws: {"src/model.py": f"import bpy  # r{job.round}\n"}, minutes=4.0)
    ws = Workspace(tmp_path / "runs" / "clock")
    with fake_clock():
        rec = _track(chair_plan, settings, (0.5, 0.6, 0.7, 0.8), agent=agent).run(
            make_spec(language=Language.BLENDER, max_rounds=4, max_minutes=10.0), ws)
    assert rec.status is RunStatus.BUDGET and 1 <= len(rec.rounds) < 5


def test_every_refine_starts_from_the_previous_rounds_commit(tmp_path, chair_plan, settings):
    """0.70 → 0.30 is a regression the loop once answered by restoring r00 before r02
    (D73 refine-from-best).  Now r02 starts from r01's code, and nothing is restored."""
    seen: dict[int, str] = {}

    def writer(job, ws):
        model = ws.src / "model.py"
        seen[job.round] = model.read_text() if model.is_file() else ""
        return {"src/model.py": f"import bpy  # written in r{job.round}\n"}

    ws = Workspace(tmp_path / "runs" / "prev")
    rec = _track(chair_plan, settings, (0.70, 0.30, 0.50), agent=FakeAgent(writer)).run(
        make_spec(language=Language.BLENDER, max_rounds=2), ws)
    assert seen[1] == "import bpy  # written in r0\n"
    assert seen[2] == "import bpy  # written in r1\n", "r02 must refine r01, not the higher-scored r00"
    assert rec.rounds[2].instructions, "the tasks come from r01's verdict and gates"
    events = [e["event"] for e in EventLog(ws.events_path).read()]
    assert "round.refine_from_best" not in events and "strategy.switch" not in events
