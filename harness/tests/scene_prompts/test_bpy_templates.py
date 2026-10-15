"""The bpy scene templates teach the hooks the Blender wrapper actually calls (the twin of
test_prompt_assembler_contract for scene_blender), and carry the library catalog where the three.js
templates do."""

from __future__ import annotations

from pathlib import Path

from codeverse3d.languages.wrappers import run_bpy_scene
from codeverse3d.prompts import PROMPTS_DIR, load_text, render

BASE = dict(title="t", spec_prompt="p", setting="s", mood="m", bounds="b", environment="e", frame_doc="", contract="",
            zones_table="", cameras="", effects="", animation="", asset_api="", textures="", constraints_text="",
            references="", skills="", tool_cards="", zone_name="Z", zone_description="d", zone_bbox="bb", zone_contents=[],
            zone_file="src/zones/z.py", neighbours=[], layout="", env_file="src/env.py", entry_file="src/scene.py",
            single_shot=False, output_format="OUT", effects_catalog="")


def test_the_bpy_templates_teach_the_hooks_the_wrapper_calls() -> None:
    """Guard the three call sites the prompts promise against the wrapper's source (if the wrapper
    changes its names, the prompts must)."""
    wrapper = Path(run_bpy_scene.__file__).read_text()
    assert 'getattr(mod, "build_env", None)' in wrapper and 'getattr(mod, "height_at", None)' in wrapper
    assert 'getattr(mod, f"build_{snake}", None)' in wrapper and 'getattr(mod, "build", None)' in wrapper
    env, zone, asset = (load_text(f"tracks/scene_{n}_bpy.j2") for n in ("env", "zone", "asset"))
    assert "build_env(ctx)" in env and "height_at(x, y)" in env and "buildEnv" not in env and "SUN_AZIMUTH_DEG" in env
    assert "def build(ctx)" in zone and "userData" not in zone and "ctx.frame(0.0)" in zone and "ctx.collection" in zone
    assert "variant=0" in asset and "ctx.collection" in asset and "ctx.frame(0.0)" in asset
    # the historical three.js hooks that nothing calls here must not leak into the bpy briefs
    for text in (env, zone, asset, load_text("tracks/scene_refine_bpy.j2")):
        assert "userData.update" not in text and "createScene" not in text and "THREE" not in text


def test_the_catalog_reaches_env_and_zone_but_not_the_asset_sheet_or_the_refine() -> None:
    """A bpy asset session gets the rows its sheet names (``library_rows``), never the whole catalog;
    a refine session edits one file with src/lib in the workspace and the skills routed, so it carries
    none either — exactly the three.js split (tests/scene_runtime/test_skeleton.py)."""
    tpl = PROMPTS_DIR / "tracks"
    for name in ("scene_env_bpy.j2", "scene_zone_bpy.j2"):
        assert "effects_catalog" in (tpl / name).read_text(), name
    assert "effects_catalog" not in (tpl / "scene_refine_bpy.j2").read_text()
    asset = (tpl / "scene_asset_bpy.j2").read_text()
    assert "effects_catalog" not in asset and "library_rows" in asset and "asset_contract" in asset


def test_the_bpy_env_and_zone_briefs_hand_the_enclosure_to_env_for_an_interior() -> None:
    """Six interior runs were "not enclosed": the env brief owns the shell only when the plan says interior."""
    env_in = render("tracks/scene_env_bpy.j2", **BASE, interior=True, glazed=False)
    env_glass = render("tracks/scene_env_bpy.j2", **BASE, interior=True, glazed=True)
    env_out = render("tracks/scene_env_bpy.j2", **BASE, interior=False, glazed=False)
    assert "This is an INTERIOR" in env_in and "room_shell" in env_in and "openings" in env_in
    assert "GLASS HOUSE" in env_glass and "glazed=True" in env_glass
    assert "INTERIOR" not in env_out and "GLASS HOUSE" not in env_out
    # the zone brief names env.py as the enclosure's owner only for an interior (the wording is the prompt's;
    # the ownership is the contract: the wrapper builds env first, zones link into their own collection)
    zone_in = render("tracks/scene_zone_bpy.j2", **BASE, interior=True)
    zone_out = render("tracks/scene_zone_bpy.j2", **BASE, interior=False)
    assert "INTERIOR" in zone_in and "`env.py`'s" in zone_in
    assert "INTERIOR" not in zone_out
