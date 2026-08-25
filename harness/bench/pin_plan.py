"""Share ONE plan between the two arms of an A/B.

WHY (docs/EVAL.md §8.1).  An 8-prompt A/A of this rig — arms identical by construction —
measured paired sd 0.202 and needs ~408 paired prompts to resolve +0.02.  Its worst pair,
on identical settings, planned **1 part** in one arm and **10** in the other: 3 vs 13 built
parts, 4,796 vs 61,340 triangles, 0.750 vs 0.600.  The spread is the PLANNER's, so no
amount of judge sampling touches it.

For a switch that acts AFTER planning, both arms can be seeded with the same ``plan.json``
and the paired difference stops carrying that spread.  It costs one planner call per pair
instead of two — cheaper, not more expensive, unlike averaging k generations.

Only valid when the switch cannot change planning: sharing a plan across a plan-side switch
would silently delete the thing under test and the rig would then report "no effect" with
confidence.  ``codeverse.tracks.plan_features.pin_plan_blockers`` decides that, and callers
must consult it — this module refuses to guess.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

__all__ = ["PinError", "plan_artifacts", "seed_plan"]

#: what a planned run leaves behind that a later run resumes from
PLAN_JSON = "plan.json"
STAGE_RESULT = Path("stages") / "plan.json"
RUN_STATE = "run_state.json"
STAGE_NAME = "plan"


class PinError(RuntimeError):
    """The source run has no reusable plan, or the target cannot take one."""


def plan_artifacts(run: Path) -> tuple[Path, Path, Path]:
    """``(plan.json, stages/plan.json, run_state.json)`` of a run that has planned."""
    return Path(run) / PLAN_JSON, Path(run) / STAGE_RESULT, Path(run) / RUN_STATE


def seed_plan(src_run: Path, dst_run: Path) -> str:
    """Copy ``src_run``'s planning result into ``dst_run`` so it resumes from that plan.

    Returns the ``inputs_hash`` that was carried across, which the caller should assert is
    the same for both arms — it is derived from the spec, so a mismatch means the two arms
    were not planning the same thing and pinning would be comparing different questions.

    The stage cache is keyed by ``(inputs_hash, result file exists)``
    (``orchestrator/runner.StageRunner.stage``), so writing all three artifacts makes the
    plan stage a cache HIT and the run continues from generation without a planner call.
    """
    src_plan, src_stage, src_state = plan_artifacts(src_run)
    for p in (src_plan, src_stage, src_state):
        if not p.is_file():
            raise PinError(f"{src_run} has not planned: {p.name} missing")

    state = json.loads(src_state.read_text())
    entry = (state.get("stages") or {}).get(STAGE_NAME)
    if not entry or not entry.get("inputs_hash"):
        raise PinError(f"{src_run}/run_state.json has no cached '{STAGE_NAME}' stage")

    dst_plan, dst_stage, dst_state = plan_artifacts(dst_run)
    dst_stage.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_plan, dst_plan)
    shutil.copyfile(src_stage, dst_stage)

    # merge into the TARGET's state: it owns its own status, rounds and result paths, and
    # only the plan stage crosses over (with this workspace's path, not the source's)
    target = json.loads(dst_state.read_text()) if dst_state.is_file() else {}
    stages = dict(target.get("stages") or {})
    stages[STAGE_NAME] = {**entry, "result_path": str(dst_stage.resolve())}
    target["stages"] = stages
    dst_state.write_text(json.dumps(target, indent=1))
    return str(entry["inputs_hash"])
