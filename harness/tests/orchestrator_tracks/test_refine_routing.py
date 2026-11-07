"""A scene refine target routes to the file that really holds the asset."""
from __future__ import annotations


def test_a_refine_target_routes_to_the_file_that_really_holds_the_asset():
    from codeverse3d.contracts.common import Language, Track
    from codeverse3d.contracts.plan import ScenePlan
    from codeverse3d.languages import get_runtime
    from codeverse3d.tracks.planner import plan_example

    plan = ScenePlan.model_validate(plan_example(Track.SCENE))  # Quay places Bollard and Crate (the hero)
    runtime = get_runtime(Language.SCENE_THREEJS)

    def files_for(target):
        return runtime.files_for(plan, target)

    assert files_for("Bollard") == ["src/assets/bollard.js"]
    assert files_for("Crate") == ["src/zones/quay.js"], "a hero is fixed where it is placed"
    plan.zones = [z for z in plan.zones if z.name != "Quay"]
    assert files_for("Crate") == [], "placed nowhere: a whole-scene task"
