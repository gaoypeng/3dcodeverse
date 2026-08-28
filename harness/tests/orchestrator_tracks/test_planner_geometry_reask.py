"""The planner re-asks on a geometrically inconsistent articulated plan (tracks/plan_checks.py)."""

from __future__ import annotations

import copy

from codeverse.contracts.common import Language, Track
from codeverse.contracts.plan import ArticulatedPlan
from codeverse.proc import EventLog
from codeverse.tracks.planner import MAX_GEOMETRY_REASKS, plan, plan_example
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import FakeChatModel, FakeRuntime


def _good() -> dict:
    return copy.deepcopy(plan_example(Track.ARTICULATED_OBJECT))


def _bad() -> dict:
    d = _good()
    j = d["joints"][0]
    j.update(type="revolute", pivot=[0.9, -0.02, 0.45], lower=0.0, upper=1.0)  # a hinge 700 mm outside the cabinet
    return d


def _spec():
    return make_spec(track=Track.ARTICULATED_OBJECT, language=Language.URDF_BLENDER, prompt="a desk drawer unit")


def test_geometry_complaint_reasks_then_accepts(tmp_ws):
    answers = [_bad(), _good()]
    model = FakeChatModel(lambda req: answers.pop(0))
    events = EventLog(tmp_ws.events_path)
    p = plan(_spec(), "fake:planner", ArticulatedPlan, tmp_ws, model=model, events=events,
             runtime=FakeRuntime(Language.URDF_BLENDER))
    assert isinstance(p, ArticulatedPlan) and len(model.requests) == 2
    reask = model.requests[1].messages[-1].text
    assert "geometry contradicts itself" in reask and "DrawerSlide" in reask and "pivot" in reask
    kinds = [e["event"] for e in events.read()]
    assert "plan.geometry" in kinds and "plan.done" in kinds


def test_geometry_reasks_are_capped_and_the_plan_ships(tmp_ws):
    model = FakeChatModel(lambda req: _bad())
    p = plan(_spec(), "fake:planner", ArticulatedPlan, tmp_ws, model=model, runtime=FakeRuntime(Language.URDF_BLENDER))
    assert isinstance(p, ArticulatedPlan)
    assert len(model.requests) == 1 + MAX_GEOMETRY_REASKS == 3
    assert all("geometry contradicts" in r.messages[-1].text for r in model.requests[1:])


def test_static_plans_skip_the_geometry_check(tmp_ws):
    from codeverse.contracts.plan import StaticPlan
    from tests.orchestrator_tracks.test_generation_planner_repair import _valid_plan_dict

    model = FakeChatModel(lambda req: _valid_plan_dict())
    plan(make_spec(), "fake:planner", StaticPlan, tmp_ws, model=model, runtime=FakeRuntime(Language.THREEJS))
    assert len(model.requests) == 1


def test_switch_off_skips_the_geometry_reask(tmp_ws, monkeypatch):
    from codeverse.tracks.planner import PLAN_GEOMETRY_ENV

    monkeypatch.setenv(PLAN_GEOMETRY_ENV, "0")
    model = FakeChatModel(lambda req: _bad())
    p = plan(_spec(), "fake:planner", ArticulatedPlan, tmp_ws, model=model, runtime=FakeRuntime(Language.URDF_BLENDER))
    assert isinstance(p, ArticulatedPlan) and len(model.requests) == 1
