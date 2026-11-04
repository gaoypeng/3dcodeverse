"""What the scene rubric scores and the prompts inline about picture quality (v1 bench defects)."""

from __future__ import annotations

import pytest

from codeverse3d.judges.rubrics import load_rubric

COMPOSITION_DEFECTS = ("flat_ground", "monotonous_vegetation", "empty_midground",
                       "undressed_scene", "thin_atmosphere")


def test_scene_rubric_craft_defects_inform_and_cannot_zero_a_scene() -> None:
    r = load_rubric("scene")
    assert set(COMPOSITION_DEFECTS) <= {d.id for d in r.defects}
    for did in COMPOSITION_DEFECTS:
        d = r.defect(did)
        assert 0.0 < d.penalty <= 0.05 and d.cap is None, did
    # the five craft items together take at most 0.20: they inform, they cannot sink a scene
    assert sum(r.defect(did).penalty for did in COMPOSITION_DEFECTS) == pytest.approx(0.18)
    # the whole checklist sums past 1.0: a scene with EVERY item present scores zero (exact figure: tests/judges/test_scene_judge.py)
    total = sum(d.penalty for d in r.defects)
    assert 1.0 <= total <= 1.10, total




def test_the_env_and_zone_briefs_hand_the_enclosure_to_env_for_an_interior() -> None:
    """Regression: six interior runs were "not enclosed"; env owns the walls only when the plan says interior."""
    from codeverse3d.prompts import render

    base = dict(title="t", spec_prompt="p", setting="s", mood="m", bounds="b", environment="e", frame_doc="", contract="",
                zones_table="", cameras="", effects="", animation="", asset_api="", textures="", constraints_text="",
                references="", skills="", tool_cards="", zone_name="Z", zone_description="d", zone_bbox="bb", zone_contents=[],
                zone_file="src/zones/z.js", neighbours=[], layout="", output_format="")
    env_in = render("tracks/scene_env.j2", **base, interior=True, glazed=False)
    env_out = render("tracks/scene_env.j2", **base, interior=False, glazed=False)
    assert "This is an INTERIOR" in env_in and "roomShell" in env_in and "openings" in env_in
    assert "INTERIOR" not in env_out
    zone_in = render("tracks/scene_zone.j2", **base, interior=True)
    zone_out = render("tracks/scene_zone.j2", **base, interior=False)
    assert "INTERIOR" in zone_in and "`env.js`'s" in zone_in
    assert "INTERIOR" not in zone_out




def test_the_bpy_env_brief_owns_the_enclosure_only_for_an_interior() -> None:
    from codeverse3d.prompts import render

    base = dict(title="t", spec_prompt="p", setting="s", mood="m", bounds="b", environment="e", frame_doc="", contract="",
                zones_table="", cameras="", effects="", animation="", asset_api="", textures="", constraints_text="",
                references="", skills="", tool_cards="", zone_name="Z", zone_description="d", zone_bbox="bb", zone_contents=[],
                zone_file="src/zones/z.py", neighbours=[], layout="", env_file="src/env.py", entry_file="src/scene.py",
                single_shot=False, output_format="OUT")
    assert "INTERIOR" in render("tracks/scene_env_bpy.j2", **base, interior=True, glazed=False)
    assert "GLASS HOUSE" in render("tracks/scene_env_bpy.j2", **base, interior=True, glazed=True)
    assert "INTERIOR" not in render("tracks/scene_env_bpy.j2", **base, interior=False, glazed=False)
