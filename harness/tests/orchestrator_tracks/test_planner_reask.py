"""Articulated planner: the articulation acceptance items and the validation re-ask's wording."""

from __future__ import annotations

import copy

from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ArticulatedPlan
from codeverse3d.tracks.planner import plan, plan_example
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import FakeChatModel


def _good() -> dict:
    return copy.deepcopy(plan_example(Track.ARTICULATED_OBJECT))


def _spec():
    return make_spec(track=Track.ARTICULATED_OBJECT, language=Language.URDF_BLENDER, prompt="a desk drawer unit")


def test_every_moving_joint_gets_an_articulation_acceptance_item(tmp_ws):
    from codeverse3d.tracks.planner import articulation_acceptance

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
    p = plan(_spec(), "fake:planner", ArticulatedPlan, tmp_ws, model=model)
    ids = [a.id for a in p.acceptance]
    assert ids.count("art_drawer") == 1 and ids.count("art_lid") == 1


def test_the_validation_reask_names_a_thin_plans_missing_parts_and_a_schema_echo(tmp_ws):
    thin = _good()
    thin["parts"] = thin["parts"][:1]        # only the cabinet ...
    thin["joints"] = []                      # ... and no joint references the dropped link
    thin["root_link"] = "NoSuchLink"         # a plain validation failure to trigger the re-ask
    echo = _good()
    echo["joints"][0]["name"] = "string"
    echo["joints"][0]["axis"] = [0, 0, 0]
    answers = [thin, _good(), echo, _good()]
    model = FakeChatModel(lambda req: answers.pop(0))
    for n, needles, absent in ((2, ("failed validation", "lists only 1 part(s)", "needs about"), None),
                               (4, ("echoes the schema", "joints[0].name"), "zero axis")):
        p = plan(_spec(), "fake:planner", ArticulatedPlan, tmp_ws, model=model)
        assert isinstance(p, ArticulatedPlan) and len(model.requests) == n
        reask = model.requests[n - 1].messages[-1].text
        assert all(x in reask for x in needles), reask
        assert absent is None or absent not in reask
