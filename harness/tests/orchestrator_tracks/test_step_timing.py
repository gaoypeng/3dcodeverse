"""A run's minutes are the sum of its steps' clocks minus what provider errors cost them
(owner, 2026-09-22) — THE number every reader reports."""

from __future__ import annotations

import time

from codeverse3d.contracts.agent import AgentJob, AgentResult
from codeverse3d.contracts.common import Language
from codeverse3d.contracts.run import RoundRecord, RunRecord, StepTime
from codeverse3d.cost.instrument import MeteredAgent
from codeverse3d.orchestrator import RunState
from codeverse3d.tracks.static_object import StaticObjectTrack
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeJudge,
    FakeRuntime,
    FakeServices,
    _planner,
)


class StormyAgent(FakeAgent):
    """A vendor CLI that spent part of every session retrying 503s."""

    def run(self, job: AgentJob) -> AgentResult:
        res = super().run(job)
        time.sleep(0.12)
        return res.model_copy(update={"provider_wait_s": 0.08})


def test_every_step_is_timed_and_the_minutes_leave_out_the_provider_errors(tmp_path, chair_plan, settings):
    agent = MeteredAgent(StormyAgent(lambda job, ws: {"src/model.py": f"import bpy  # r{job.round}\n"}))
    ws = Workspace(tmp_path / "runs" / "timed")
    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.5, 0.6)), agent=agent,
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.BLENDER))
    rec = track.run(make_spec(language=Language.BLENDER, max_rounds=1), ws)
    for r in rec.rounds:
        (gen,) = [s for s in r.steps if s.step == "generate"]
        assert gen.round == r.index and gen.wall_s >= 0.12 and abs(gen.lost_s - 0.08) < 0.005
    assert rec.steps is not None and all(s.round is None for s in rec.steps)
    total = sum(s.active_s for s in rec.steps) + sum(s.active_s for r in rec.rounds for s in r.steps)
    assert abs(rec.minutes - total / 60) < 1e-9
    assert rec.minutes < sum(r.duration_s for r in rec.rounds) / 60, "the 503 retries are not in the minutes"
    # the step log survives on disk: the journal carries each round's, run_state the run's
    assert RoundRecord.model_validate(ws.read_json(ws.root / "rounds" / "r00.json")).steps == rec.rounds[0].steps
    assert RunState.load(ws).steps == rec.steps


def test_a_record_from_before_step_timing_counts_its_own_clock():
    old = RunRecord.model_validate({"spec": make_spec().model_dump(mode="json"), "workspace": "w",
                                    "rounds": [{"index": 0, "kind": "baseline", "duration_s": 120.0}],
                                    "extra": {"budget": {"elapsed_min": 7.5}}})
    assert old.steps is None and old.minutes == 7.5 and old.rounds[0].minutes == 2.0
    assert RunRecord.model_validate({"spec": make_spec().model_dump(mode="json"), "workspace": "w"}).minutes is None
    # a run resumed across the change: new steps, and an old round that still counts its clock
    mixed = old.model_copy(update={"steps": [StepTime(step="plan", wall_s=90.0, lost_s=30.0)]})
    assert mixed.minutes == 1.0 + 2.0
