"""The merge map the asset stage USED is what the zones are told (2026-09-07).

`run_asset_stage` folds near-identical props under a cap that depends on the soft budget at
the time (`MAX_ASSETS` or `DEGRADED_MAX_ASSETS`); `prepare` used to recompute the map from
the plan with `MAX_ASSETS` alone, so a degraded run told the zones merged assets were
"NOT AVAILABLE" while working shims existed.  The stage writes the note it used, and
`prepare` reads it back.
"""
from __future__ import annotations

from codeverse3d.tracks.scene_assets import read_dedupe_note, write_dedupe_note


def test_the_note_round_trips_and_an_empty_map_is_still_a_note(tmp_ws):
    assert read_dedupe_note(tmp_ws) is None, "no stage has run"
    write_dedupe_note(tmp_ws, {"SteppingStone": "PondRock"})
    assert read_dedupe_note(tmp_ws) == {"SteppingStone": "PondRock"}
    write_dedupe_note(tmp_ws, {})
    assert read_dedupe_note(tmp_ws) == {}, "a stage that merged nothing says so (not None)"


def test_a_refine_target_routes_to_the_file_that_really_holds_the_asset():
    """Every asset target used to map to ``src/assets/<snake>.js``.  The skeleton writes
    that file only for a three.js asset: a merged asset's is the variant shim onto its
    survivor, and a blender hero ships as ``public/assets/<snake>.glb`` — so a refine
    session could "succeed" editing a file nothing imports."""
    from types import SimpleNamespace

    from codeverse3d.contracts.common import Language, Track
    from codeverse3d.contracts.plan import ScenePlan
    from codeverse3d.tracks.planner import plan_example
    from codeverse3d.tracks.prompting import file_for_target_factory

    plan = ScenePlan.model_validate(plan_example(Track.SCENE))  # Quay places Bollard and Crate (the hero)
    plan.assets.append(next(a for a in plan.assets if a.name == "Bollard").model_copy(update={"name": "Mooring"}))
    ctx = SimpleNamespace(plan=plan, language=Language.SCENE_THREEJS, runtime=SimpleNamespace(),
                          extra={"asset_alias": {"Mooring": "Bollard"}})
    files_for = file_for_target_factory(ctx)
    assert files_for("Bollard") == ["src/assets/bollard.js"]
    assert files_for("Mooring") == ["src/assets/bollard.js"], "the survivor's factory, not the shim"
    assert files_for("Crate") == ["src/zones/quay.js"], "a hero is fixed where it is placed"
    plan.zones = [z for z in plan.zones if z.name != "Quay"]
    assert file_for_target_factory(ctx)("Crate") == [], "placed nowhere: a whole-scene task"
