"""Sharing one plan between A/B arms (bench/pin_plan.py), and the rule that permits it (EVAL.md §8.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.pin_plan import PinError, seed_plan


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


def test_the_targets_own_state_survives(tmp_path):
    """Only the plan stage crosses over, pointing at the TARGET's file; the rest of the state is the target's."""
    src = _planned_run(tmp_path, parts=3)
    dst = tmp_path / "dst_run"
    (dst / "stages").mkdir(parents=True)
    (dst / "run_state.json").write_text(json.dumps(
        {"status": "created", "current_round": 0, "stages": {"skeleton": {"inputs_hash": "zzz"}}}))

    seed_plan(src, dst)
    state = json.loads((dst / "run_state.json").read_text())
    assert state["status"] == "created" and state["current_round"] == 0
    assert state["stages"]["skeleton"]["inputs_hash"] == "zzz", "the target's other stages were dropped"
    assert state["stages"]["plan"]["inputs_hash"] == "abc123"
    assert state["stages"]["plan"]["result_path"] == str((dst / "stages" / "plan.json").resolve())


def test_an_unplanned_source_is_refused_by_name(tmp_path):
    (tmp_path / "src_run" / "stages").mkdir(parents=True)
    with pytest.raises(PinError, match="has not planned"):
        seed_plan(tmp_path / "src_run", tmp_path / "dst")


def test_a_source_without_a_cached_stage_is_refused(tmp_path):
    """plan.json but no stage entry: the target would silently re-plan while the report claims a pin."""
    src = _planned_run(tmp_path, parts=4)
    (src / "run_state.json").write_text(json.dumps({"status": "generating", "stages": {}}))
    dst = tmp_path / "dst_run"
    (dst / "stages").mkdir(parents=True)
    with pytest.raises(PinError, match="no cached 'plan' stage"):
        seed_plan(src, dst)


# --------------------------------------------------------------- the driver's use of it


def test_pin_pair_reuses_the_plan_when_the_pair_is_retried(tmp_path, monkeypatch):
    """--pin-plan plans ONCE, seeds every arm, and a retried pair neither re-plans nor gets another plan."""
    from bench import ab_plan
    from bench.run_bench import Battery

    battery = Battery.load(Path(ab_plan.__file__).resolve().parent / "prompts" / "compare_v1.yaml")
    item = battery.prompts[0]
    opts = ab_plan.AbOptions(variant_env={"C3D_SKILLS": "0"}, pin_plan=True)  # the skills-OFF arm
    n = 0

    def fake_plan_once(spec, ws_root):
        nonlocal n
        n += 1
        run = Path(ws_root)
        (run / "stages").mkdir(parents=True, exist_ok=True)
        body = json.dumps({"object_name": "Chair", "parts": [{"name": f"call{n}"}]})
        (run / "plan.json").write_text(body)
        (run / "stages" / "plan.json").write_text(body)
        (run / "run_state.json").write_text(json.dumps(
            {"stages": {"plan": {"name": "plan", "inputs_hash": "h1",
                                 "result_path": str(run / "stages" / "plan.json")}}}))
        return "h1"

    monkeypatch.setattr(ab_plan, "plan_once", fake_plan_once)
    assert ab_plan.pin_pair(battery, item, tmp_path, list(ab_plan.ARMS), opts) == "h1"
    ab_plan.pin_pair(battery, item, tmp_path, list(ab_plan.ARMS), opts)

    assert n == 1, "the second pass re-planned; the retry would not be comparable"
    for arm in ab_plan.ARMS:
        run = Path(ab_plan.cell_dir(tmp_path, arm, item.id, opts.generator)) / "run"
        assert json.loads((run / "plan.json").read_text())["parts"] == [{"name": "call1"}]
        entry = json.loads((run / "run_state.json").read_text())["stages"]["plan"]
        assert entry["inputs_hash"] == "h1" and entry["result_path"] == str((run / "stages" / "plan.json").resolve())
        assert (run / "spec.json").is_file(), "_run_harness skips writing it once the ws exists"


def test_a_plan_side_variant_env_is_refused_by_the_cli():
    """Pinning a plan-side switch deletes the thing under test: the CLI refuses."""
    from bench import ab_plan

    with pytest.raises(SystemExit):
        ab_plan.main(["--prompts", str(Path(ab_plan.__file__).resolve().parent / "prompts" / "compare_v1.yaml"),
                      "--out", "/tmp/never", "--pin-plan", "--no-preflight",
                      "--variant-env", "C3D_PLAN_BRIEF=1"])
