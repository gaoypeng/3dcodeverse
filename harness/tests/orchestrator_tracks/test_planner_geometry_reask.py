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


def test_every_moving_joint_gets_an_articulation_acceptance_item(tmp_ws):
    from codeverse.tracks.planner import articulation_acceptance

    good = _good()
    good["acceptance"] = [a for a in good["acceptance"] if a.get("how") != "articulation"]
    good["joints"].append({"name": "LidHinge", "type": "revolute", "parent": "Cabinet", "child": "Drawer",
                           "axis": [1, 0, 0], "pivot": [0, 0.21, 0.55], "lower": 0.0, "upper": 1.2, "rest": 0.0,
                           "motion": "the lid tilts up"})
    good["joints"][1]["child"] = "Lid"
    good["parts"].append({"name": "Lid", "role": "top lid", "description": "a flat lid", "attach_to": "Cabinet",
                          "bbox": {"center": [0, 0, 0.61], "extents": [0.4, 0.5, 0.02]}, "material": "oak"})
    plan_obj = ArticulatedPlan.model_validate(good)
    items = articulation_acceptance(plan_obj, plan_obj.acceptance)
    assert [a.id for a in items] == ["art_drawer", "art_lid"]
    assert all(a.how == "articulation" and a.priority == "should" for a in items)
    assert "moves over [0.00, 0.35] m" in items[0].text and "closed / stowed" in items[0].text
    assert "the lid tilts up" in items[1].text and "[0.00, 1.20] rad" in items[1].text
    # a planner item that already names the drawer covers its joint
    plan_obj.acceptance.append(type(items[0])(id="j9", text="Drawer slides out 0.35 m cleanly", how="articulation"))
    assert [a.id for a in articulation_acceptance(plan_obj, plan_obj.acceptance)] == ["art_lid"]
    # and the full planner path appends them exactly once
    model = FakeChatModel(lambda req: good)
    p = plan(_spec(), "fake:planner", ArticulatedPlan, tmp_ws, model=model, runtime=FakeRuntime(Language.URDF_BLENDER))
    ids = [a.id for a in p.acceptance]
    assert ids.count("art_drawer") == 1 and ids.count("art_lid") == 1


def test_a_crashing_geometry_check_never_costs_the_plan(tmp_ws, monkeypatch):
    import codeverse.tracks.plan_checks as pc

    def boom(plan_obj):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(pc, "plan_geometry_complaint", boom)
    model = FakeChatModel(lambda req: _bad())
    events = EventLog(tmp_ws.events_path)
    p = plan(_spec(), "fake:planner", ArticulatedPlan, tmp_ws, model=model, events=events,
             runtime=FakeRuntime(Language.URDF_BLENDER))
    assert isinstance(p, ArticulatedPlan) and len(model.requests) == 1
    kinds = [e["event"] for e in events.read()]
    assert "plan.geometry_error" in kinds and "plan.done" in kinds


def test_validation_reask_names_the_missing_parts_when_the_plan_is_thin(tmp_ws):
    thin = _good()
    thin["parts"] = thin["parts"][:1]  # only the cabinet; the joint still references the drawer
    answers = [thin, _good()]
    model = FakeChatModel(lambda req: answers.pop(0))
    p = plan(_spec(), "fake:planner", ArticulatedPlan, tmp_ws, model=model, runtime=FakeRuntime(Language.URDF_BLENDER))
    assert isinstance(p, ArticulatedPlan) and len(model.requests) == 2
    reask = model.requests[1].messages[-1].text
    assert "failed validation" in reask and "lists only 1 part(s)" in reask and "needs about" in reask
