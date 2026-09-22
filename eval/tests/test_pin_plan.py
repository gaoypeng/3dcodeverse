"""Sharing one plan between A/B arms (bench/pin_plan.py + the rule that permits it).

docs/EVAL.md §8.1: the A/A's worst pair planned 1 part in one arm and 10 in the other on
IDENTICAL settings.  Pinning removes that term; pinning the wrong switch deletes the
experiment.  Both halves are tested here.
"""

from __future__ import annotations

import json
from pathlib import Path

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


# --------------------------------------------------------------- the driver's use of it
def test_pin_pair_seeds_every_arm_from_one_plan(tmp_path, monkeypatch):
    """``--pin-plan`` must plan ONCE and hand the same plan to both arms.

    Planning per-arm is what the pinning exists to remove; planning per-arm *while
    reporting itself pinned* is worse than not pinning at all, so the count is asserted.
    """
    from bench import ab_plan
    from bench.run_bench import Battery

    battery = Battery.load(Path(ab_plan.__file__).resolve().parent / "prompts" / "compare_v1.yaml")
    item = battery.prompts[0]
    opts = ab_plan.AbOptions(variant_env={"C3D_SKILLS": "1"}, pin_plan=True)
    calls: list[Path] = []

    def fake_plan_once(spec, ws_root):
        calls.append(Path(ws_root))
        run = Path(ws_root)
        (run / "stages").mkdir(parents=True, exist_ok=True)
        body = json.dumps({"object_name": "Chair", "parts": [{"name": "Seat"}]})
        (run / "plan.json").write_text(body)
        (run / "stages" / "plan.json").write_text(body)
        (run / "run_state.json").write_text(json.dumps(
            {"stages": {"plan": {"name": "plan", "inputs_hash": "h1",
                                 "result_path": str(run / "stages" / "plan.json")}}}))
        return "h1"

    monkeypatch.setattr(ab_plan, "plan_once", fake_plan_once)
    got = ab_plan.pin_pair(battery, item, tmp_path, list(ab_plan.ARMS), opts)

    assert got == "h1"
    assert len(calls) == 1, f"planned {len(calls)} times, not once: {calls}"
    for arm in ab_plan.ARMS:
        run = Path(ab_plan.cell_dir(tmp_path, arm, item.id, opts.generator)) / "run"
        assert json.loads((run / "plan.json").read_text())["parts"] == [{"name": "Seat"}]
        entry = json.loads((run / "run_state.json").read_text())["stages"]["plan"]
        assert entry["inputs_hash"] == "h1"
        assert entry["result_path"] == str((run / "stages" / "plan.json").resolve())
        assert (run / "spec.json").is_file(), "_run_harness skips writing it once the ws exists"


def test_pin_pair_reuses_the_plan_when_the_pair_is_retried(tmp_path, monkeypatch):
    """A resumed / redone pair must not buy a second plan — and must not get a DIFFERENT
    one, which would make the two attempts incomparable."""
    from bench import ab_plan
    from bench.run_bench import Battery

    battery = Battery.load(Path(ab_plan.__file__).resolve().parent / "prompts" / "compare_v1.yaml")
    item = battery.prompts[0]
    opts = ab_plan.AbOptions(variant_env={"C3D_SKILLS": "1"}, pin_plan=True)
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
    ab_plan.pin_pair(battery, item, tmp_path, list(ab_plan.ARMS), opts)
    ab_plan.pin_pair(battery, item, tmp_path, list(ab_plan.ARMS), opts)

    assert n == 1, "the second pass re-planned; the retry would not be comparable"
    run = Path(ab_plan.cell_dir(tmp_path, "variant", item.id, opts.generator)) / "run"
    assert json.loads((run / "plan.json").read_text())["parts"] == [{"name": "call1"}]


def test_a_plan_side_variant_env_is_refused_by_the_cli():
    """Pinning a plan-side switch deletes the thing under test, and the rig would then
    report 'no effect' with confidence.  The refusal is the whole safety property."""
    from bench import ab_plan

    with pytest.raises(SystemExit):
        ab_plan.main(["--prompts", str(Path(ab_plan.__file__).resolve().parent / "prompts" / "compare_v1.yaml"),
                      "--out", "/tmp/never", "--pin-plan", "--no-preflight",
                      "--variant-env", "C3D_PLAN_BRIEF=1"])


# --------------------------------------------------------------------------- the key
# THE load-bearing invariant.  `plan_once` seeds a plan whose stage-cache key is computed
# in bench/pin_plan.py; the arm that consumes it computes its key in
# tracks/lifecycle.BaseTrack.run.  If those two expressions ever drift apart, the seeded
# plan is a cache MISS, both arms silently re-plan, and the rig reports "no effect" for a
# switch whose experiment it just deleted -- expensively, and with no error anywhere.
# Pinning that does not pin is worse than no pinning, because it looks like a result.

def _stage_call_inputs(path: Path, func: str) -> str:
    """The literal source of the ``inputs={...}`` argument of the ``plan`` stage call."""
    import ast

    tree = ast.parse(path.read_text())
    scope = next((n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == func), None)
    assert scope is not None, f"{path.name} has no {func}()"
    for node in ast.walk(scope):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "stage"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "plan"):
            for kw in node.keywords:
                if kw.arg == "inputs":
                    return ast.dump(kw.value)
    raise AssertionError(f"{path.name}::{func} has no runner.stage('plan', ..., inputs=...) call")


def test_the_seeded_plan_and_the_running_plan_share_one_stage_key():
    """`plan_once` and `BaseTrack.run` must key the plan stage by the same expression.

    The only legitimate difference is the receiver holding the track: `plan_once` keeps it
    in a local named `track`, `run` is a method and says `self`.  Everything else -- the
    keys, the helper called on the spec, the attribute path -- must match exactly.
    """

    here = Path(__file__).resolve().parents[1]           # eval/
    seeded = _stage_call_inputs(here / "bench" / "pin_plan.py", "plan_once")
    running = _stage_call_inputs(here.parent / "harness" / "codeverse3d" / "tracks" / "lifecycle.py", "run")

    def norm(dump: str) -> str:
        return dump.replace("Name(id='self',", "Name(id='track',")

    assert norm(seeded) == norm(running), (
        "the pinned plan and the arm that consumes it no longer compute the same "
        "stage-cache key.\n  pin_plan.plan_once: " + seeded
        + "\n  lifecycle.run:      " + running
        + "\nA seeded plan would be a cache MISS: both arms re-plan, neither is pinned, and "
          "the A/B reports 'no effect' for an experiment it silently deleted."
    )
    assert "plan_stage_inputs" in seeded, "the key stopped going through plan_stage_inputs"
