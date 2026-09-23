"""A scene refine target routes to the file that really holds the asset."""
from __future__ import annotations


def test_a_refine_target_routes_to_the_file_that_really_holds_the_asset():
    from types import SimpleNamespace

    from codeverse3d.contracts.common import Language, Track
    from codeverse3d.contracts.plan import ScenePlan
    from codeverse3d.languages import get_runtime
    from codeverse3d.tracks.planner import plan_example
    from codeverse3d.tracks.scene import SceneTrack

    plan = ScenePlan.model_validate(plan_example(Track.SCENE))  # Quay places Bollard and Crate (the hero)
    plan.assets.append(next(a for a in plan.assets if a.name == "Bollard").model_copy(update={"name": "Mooring"}))
    ctx = SimpleNamespace(plan=plan, runtime=get_runtime(Language.SCENE_THREEJS), extra={"asset_alias": {"Mooring": "Bollard"}})
    files_for = SceneTrack().refine_file_for_target(ctx)
    assert files_for("Bollard") == ["src/assets/bollard.js"]
    assert files_for("Mooring") == ["src/assets/bollard.js"], "the survivor's factory, not the shim"
    assert files_for("Crate") == ["src/zones/quay.js"], "a hero is fixed where it is placed"
    plan.zones = [z for z in plan.zones if z.name != "Quay"]
    assert files_for("Crate") == [], "placed nowhere: a whole-scene task"
