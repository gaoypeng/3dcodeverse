"""Sharing one plan between A/B arms (bench/pin_plan.py + the rule that permits it).

docs/EVAL.md §8.1: the A/A's worst pair planned 1 part in one arm and 10 in the other on
IDENTICAL settings.  Pinning removes that term; pinning the wrong switch deletes the
experiment.  Both halves are tested here.
"""

from __future__ import annotations

import json

import pytest

from bench.pin_plan import PinError, plan_artifacts, seed_plan


def _planned_run(root, *, parts, inputs_hash="abc123"):
    """A run directory in the state a planned harness run leaves behind."""
    run = root / "src_run"
    (run / "stages").mkdir(parents=True)
    plan = {"object_name": "Chair", "parts": [{"name": f"P{i}"} for i in range(parts)]}
    (run / "plan.json").write_text(json.dumps(plan))
    (run / "stages" / "plan.json").write_text(json.dumps(plan))
    (run / "run_state.json").write_text(json.dumps({
        "status": "generating",
        "stages": {"plan": {"name": "plan", "inputs_hash": inputs_hash,
                            "result_path": str(run / "stages" / "plan.json")}},
    }))
    return run


def test_the_target_resumes_from_the_source_plan(tmp_path):
    src = _planned_run(tmp_path, parts=10)
    dst = tmp_path / "dst_run"
    (dst / "stages").mkdir(parents=True)
    (dst / "run_state.json").write_text(json.dumps({"status": "created", "stages": {}}))

    got = seed_plan(src, dst)
    assert got == "abc123"

    dst_plan, dst_stage, dst_state = plan_artifacts(dst)
    assert json.loads(dst_plan.read_text())["parts"] == json.loads((src / "plan.json").read_text())["parts"]
    assert dst_stage.is_file(), "the stage cache result must exist or the stage is a MISS"
    entry = json.loads(dst_state.read_text())["stages"]["plan"]
    assert entry["inputs_hash"] == "abc123", "the hash is what makes it a cache hit"
    assert entry["result_path"] == str(dst_stage.resolve()), "must point at the TARGET's file"


def test_the_targets_own_state_survives(tmp_path):
    """Only the plan stage crosses over — status, rounds and other stages are the
    target's own, or seeding would import the source run's history."""
    src = _planned_run(tmp_path, parts=3)
    dst = tmp_path / "dst_run"
    (dst / "stages").mkdir(parents=True)
    (dst / "run_state.json").write_text(json.dumps(
        {"status": "created", "current_round": 0, "stages": {"skeleton": {"inputs_hash": "zzz"}}}))

    seed_plan(src, dst)
    state = json.loads((dst / "run_state.json").read_text())
    assert state["status"] == "created" and state["current_round"] == 0
    assert state["stages"]["skeleton"]["inputs_hash"] == "zzz", "the target's other stages were dropped"
    assert "plan" in state["stages"]


def test_an_unplanned_source_is_refused_by_name(tmp_path):
    (tmp_path / "src_run" / "stages").mkdir(parents=True)
    with pytest.raises(PinError, match="has not planned"):
        seed_plan(tmp_path / "src_run", tmp_path / "dst")


def test_a_source_without_a_cached_stage_is_refused(tmp_path):
    """plan.json on disk but no stage entry: the target would re-plan and the pair would
    silently stop being pinned — worse than not pinning, because the report would claim it."""
    src = _planned_run(tmp_path, parts=4)
    (src / "run_state.json").write_text(json.dumps({"status": "generating", "stages": {}}))
    dst = tmp_path / "dst_run"
    (dst / "stages").mkdir(parents=True)
    with pytest.raises(PinError, match="no cached 'plan' stage"):
        seed_plan(src, dst)


# --------------------------------------------------------------- the permission rule
def test_only_a_post_planning_switch_may_be_pinned():
    from codeverse.tracks.plan_features import pin_plan_blockers

    assert pin_plan_blockers({"CV3D_PLAN_FEATURES": "contacts"}) == []
    assert pin_plan_blockers({"CV3D_PLAN_FEATURES": "fit"}), "a plan-side switch must block"


def test_pinning_a_plan_side_switch_would_delete_the_experiment(tmp_path):
    """The failure this guards: both arms get one plan, so a change that only alters
    planning becomes a no-op — and the rig then reports 'no effect' with confidence."""
    from codeverse.tracks.plan_features import pin_plan_blockers

    src = _planned_run(tmp_path, parts=10)
    dst = tmp_path / "dst_run"
    (dst / "stages").mkdir(parents=True)
    (dst / "run_state.json").write_text(json.dumps({"status": "created", "stages": {}}))
    seed_plan(src, dst)
    # identical plans in both arms is exactly what pinning means...
    assert json.loads((dst / "plan.json").read_text()) == json.loads((src / "plan.json").read_text())
    # ...which is why the caller must refuse it for a switch that changes planning
    assert pin_plan_blockers({"CV3D_PLAN_FEATURES": "fit"}) == [
        "CV3D_PLAN_FEATURES=fit changes the plan itself"]
