"""A degenerate plan is re-sampled from the original prompt, not edited in context.

Measured 2026-09-02 over 200 plan calls on two trees: 3-4 % of articulated plan calls end
in `PlanningError`, always the same way — one top-level part, joints naming links the plan
never lists — and both in-context re-asks come back with that same answer."""

from __future__ import annotations

import copy
import json

import pytest

from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ArticulatedPlan
from codeverse3d.proc import EventLog
from codeverse3d.tracks.planner import (
    MAX_PLAN_RESTARTS,
    PLAN_RESTART_ENV,
    PlanningError,
    missing_link_names,
    plan,
    plan_example,
)
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import FakeChatModel, FakeRuntime


def _good() -> dict:
    return copy.deepcopy(plan_example(Track.ARTICULATED_OBJECT))


def _degenerate() -> dict:
    """One part; the joint still moves a link the plan never lists (the measured shape)."""
    d = _good()
    d["parts"] = [p for p in d["parts"] if p["name"] == "Cabinet"]
    return d


def _spec():
    return make_spec(track=Track.ARTICULATED_OBJECT, language=Language.URDF_BLENDER, prompt="a desk drawer unit")


def _run(tmp_ws, answers, events=None):
    model = FakeChatModel(lambda req: answers.pop(0))
    p = plan(_spec(), "fake:planner", ArticulatedPlan, tmp_ws, model=model, events=events,
             runtime=FakeRuntime(Language.URDF_BLENDER))
    return p, model


def test_missing_link_names_reports_what_the_joints_reference_and_parts_lack():
    assert missing_link_names(_degenerate()) == ["Drawer"]
    assert missing_link_names(_good()) == []
    assert missing_link_names({"parts": [], "joints": []}) == []
    assert missing_link_names("not a dict") == []
    nested = _good()
    nested["parts"] = [nested["parts"][0]]
    nested["parts"][0]["children"] = [{"name": "Drawer"}]
    assert missing_link_names(nested) == []  # a sub-part IS a name the plan defines


def test_a_degenerate_plan_is_resampled_without_its_own_answer_in_context(tmp_ws):
    events = EventLog(tmp_ws.events_path)
    p, model = _run(tmp_ws, [_degenerate(), _good()], events=events)
    assert isinstance(p, ArticulatedPlan) and len(model.requests) == 2
    first, second = model.requests
    assert len(second.messages) == len(first.messages) == 1  # no echo, no accumulated turns
    text = second.messages[-1].text
    assert text.startswith(first.messages[-1].text)  # the original request is kept verbatim
    assert "listed only 1 top-level part(s)" in text and "Drawer" in text
    assert "must contain EVERY link a joint names" in text
    ev = [e for e in events.read() if e["event"] == "plan.restart"]
    assert len(ev) == 1 and ev[0]["n_parts"] == 1 and ev[0]["missing"] == ["Drawer"]
    assert [e for e in events.read() if e["event"] == "plan.done"][0]["restarts"] == 1


def test_an_ordinary_validation_error_still_edits_in_context(tmp_ws):
    """Nothing changes for the failures a re-ask does fix: the model sees its own answer."""
    bad = _good()
    bad["joints"][0]["axis"] = [0, 0, 0]  # a real slip, not a degenerate plan
    p, model = _run(tmp_ws, [bad, _good()])
    assert isinstance(p, ArticulatedPlan) and len(model.requests) == 2
    assert len(model.requests[1].messages) == 3  # user + echoed answer + complaint
    assert "failed validation" in model.requests[1].messages[-1].text


def test_the_kill_switch_restores_the_in_context_reask(tmp_ws, monkeypatch):
    monkeypatch.setenv(PLAN_RESTART_ENV, "0")
    p, model = _run(tmp_ws, [_degenerate(), _good()])
    assert isinstance(p, ArticulatedPlan) and len(model.requests[1].messages) == 3


def test_a_restart_costs_an_attempt_not_one_of_the_two_reask_slots(tmp_ws):
    """Every answer degenerate: restart once, then still get BOTH in-context re-asks.

    The restart used to increment the same counter as a re-ask, so a run that restarted
    had one re-ask left instead of two — visible in the recorded battery as deaths with
    ``restarts=1`` at the validation cap (bench/data/plan_stage/restart_on.jsonl)."""
    model = FakeChatModel(lambda req: _degenerate())
    with pytest.raises(PlanningError):
        plan(_spec(), "fake:planner", ArticulatedPlan, tmp_ws, model=model,
             runtime=FakeRuntime(Language.URDF_BLENDER))
    assert MAX_PLAN_RESTARTS == 1
    assert len(model.requests) == 4  # first + restart + two re-asks
    # 1 = the request alone (a restart replaces the conversation), then one echo+complaint pair each
    assert [len(r.messages) for r in model.requests] == [1, 1, 3, 5]


def test_a_full_plan_with_one_dangling_link_is_edited_in_context_not_resampled(tmp_ws):
    """The restart's trigger is the MEASURED class: collapsed AND dangling.  A plan with
    every part in place and one misspelled link is what the re-ask is good at, and
    re-sampling it throws away the parts that were right."""
    typo = _good()
    for i in range(4):  # a full-sized plan, well over the degeneracy floor
        typo["parts"].append({"name": f"Filler{i}", "role": "trim", "description": "a rail",
                              "bbox": {"center": [0, 0, 0.1 * i], "extents": [0.02, 0.4, 0.02]},
                              "material": "oak", "attach_to": "Cabinet"})
    typo["joints"][0]["child"] = "Drawerr"
    p, model = _run(tmp_ws, [typo, _good()])
    assert isinstance(p, ArticulatedPlan) and len(model.requests) == 2
    assert len(model.requests[1].messages) == 3  # user + echoed answer + complaint, no restart
    assert "failed validation" in model.requests[1].messages[-1].text


def test_a_rejected_plan_is_written_where_a_dead_run_can_be_read(tmp_ws):
    """A run that dies at the plan stage used to leave nothing but the pydantic message,
    which truncates the offending value — so "what did the model actually write?", the
    question every plan-stage failure class starts from, had no answer at all."""
    from codeverse3d.tracks.planner import INVALID_PLAN_DIR

    model = FakeChatModel(lambda req: _degenerate())
    with pytest.raises(PlanningError):
        plan(_spec(), "fake:planner", ArticulatedPlan, tmp_ws, model=model,
             runtime=FakeRuntime(Language.URDF_BLENDER))

    written = sorted((tmp_ws.root / INVALID_PLAN_DIR).glob("attempt*.json"))
    assert len(written) == 4, [p.name for p in written]      # one per rejected answer
    first = json.loads(written[0].read_text())
    assert [p["name"] for p in first["parts"]] == ["Cabinet"]  # the answer, not the error text
    assert first["joints"][0]["child"] == "Drawer"             # the link it named and never listed
