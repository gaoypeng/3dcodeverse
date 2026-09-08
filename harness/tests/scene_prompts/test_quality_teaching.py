"""What the scene prompts, cookbook and rubric must TEACH about picture quality.

Every item here maps to a defect the v1 bench judges actually wrote up (flat ground,
identical cones, hard world edge, diorama-on-a-plane, undressed, monochrome light,
invisible motion).  The tests are cheap guards that the teaching does not silently
disappear from a prompt during a refactor.
"""

from __future__ import annotations

import pytest

from codeverse.judges.rubrics import load_rubric
from codeverse.prompts import load_text

#: cookbook chapter titles selected by the scene prompt builder
QUALITY_CHAPTERS = [
    "Ground that reads real",
    "Horizon: the world must not end",
    "Vegetation that reads real",
    "Rocks, cliffs and boulders that read organic",
    "Set dressing",
    "Scene layering",
    "Atmosphere: time-of-day triads",
    "Motion you can SEE",
    "The exact contract the harness assembler enforces",
]


@pytest.fixture(scope="module")
def cookbook() -> str:
    return load_text("scene_threejs/cookbook.md")


@pytest.mark.parametrize("title", QUALITY_CHAPTERS)
def test_cookbook_quality_chapter_is_selectable(cookbook: str, title: str) -> None:
    from codeverse.prompts.sections import find_section, split_sections

    s = find_section(split_sections(cookbook), title)
    assert s is not None and s.title.startswith(title)


def test_cookbook_carries_the_numbers_the_recipes_depend_on(cookbook: str) -> None:
    for token in ("vertexColors", "setColorAt", "InstancedMesh", "shadow.mapSize",
                  "cameraMask", "buildHorizonRing", "TIME_OF_DAY", "hazeMix"):
        assert token in cookbook, token


def test_planner_asks_for_density_layering_and_subject_framing() -> None:
    text = load_text("tracks/plan_scene.j2")
    for token in ("DENSITY COUNTS", "DEPTH ROLE", "foreground frame", "silhouette ring",
                  "HERO motion", "fill hue", "SUBJECT"):
        assert token in text, token


def test_planner_is_told_that_must_acceptance_caps_the_run() -> None:
    """One unmet `must` caps the run at 0.60 (judges.caps.missing_must_acceptance).  A scene
    whose pictures score 0.81 must not lose 0.13 because the planner over-specified a detail
    and then marked it `must` — measured on cq_japanese_garden_v2 r00 (0.809 → 0.729 → 0.60)."""
    from codeverse.judges.rubrics import load_rubric

    caps = {c.id: c for c in load_rubric("scene_v1").caps}
    assert caps["missing_must_acceptance"].cap == 0.60
    text = load_text("tracks/plan_scene.j2")
    assert "priority: should" in text
    assert "caps the whole run" in text or "caps the\n   whole run" in text
    assert "at most 4-6 items `must`" in text


def test_zone_prompt_demands_variation_dressing_and_visible_motion() -> None:
    text = load_text("tracks/scene_zone.j2")
    for token in ("3 distinct silhouettes", "hue jitter", "density counts",
                  "foreground frame", "nothing_moves", "cameraMask"):
        assert token in text, token


def test_env_prompt_demands_blended_ground_horizon_and_colour_contrast() -> None:
    text = load_text("tracks/scene_env.j2")
    for token in ("flat_ground", "silhouette ring", "fog far", "60", "Shadow texel"):
        assert token in text, token


def test_refine_prompt_maps_judge_complaints_to_chapters() -> None:
    text = load_text("tracks/scene_refine.j2")
    for title in ("Ground that reads real", "Horizon: the world must not end",
                  "Vegetation that reads real", "Set dressing", "Scene layering",
                  "Atmosphere: time-of-day triads", "Motion you can SEE"):
        assert title in text, title


# --------------------------------------------------------------------------- rubric
COMPOSITION_DEFECTS = ("flat_ground", "monotonous_vegetation", "empty_midground",
                       "undressed_scene", "thin_atmosphere")


def test_scene_rubric_scores_the_composition_defects() -> None:
    r = load_rubric("scene_v1")
    ids = {d.id for d in r.defects}
    assert set(COMPOSITION_DEFECTS) <= ids
    for did in COMPOSITION_DEFECTS:
        d = r.defect(did)
        assert 0.0 < d.penalty <= 0.05, f"{did}: keep the new craft penalties small"
        assert d.cap is None, f"{did}: craft defects inform, they do not cap"


def test_scene_rubric_penalties_cannot_zero_a_good_scene() -> None:
    r = load_rubric("scene_v1")
    assert sum(d.penalty for d in r.defects) <= 0.62


def test_scene_rubric_has_the_diorama_anchor() -> None:
    r = load_rubric("scene_v1")
    comp = r.criterion("composition_and_camera")
    assert "0.5" in comp.anchors and "diorama" in comp.anchors["0.5"].lower()
    assert "foreground" in comp.anchors["1.0"].lower()


def test_extra_anchor_levels_reach_the_judge_prompt() -> None:
    from codeverse.judges.prompt_builder import _rubric_block

    block = _rubric_block(load_rubric("scene_v1"))
    assert "0.5: The scene reads as a diorama" in block
    # the four required levels are still rendered, richest first
    idx = [block.index(f"     {lvl}: ") for lvl in ("1.0", "0.7", "0.4", "0.1")]
    assert idx == sorted(idx)


# --------------------------------------------------------------------------- delivery
def test_the_quality_chapters_travel_INSIDE_the_zone_and_env_prompts() -> None:
    """Naming a chapter is not teaching it: on scenes_v1 not one of the 20 zone/env
    sessions fetched cookbook chapters on demand, so the chapters that decide the score are inlined
    into the brief itself (`tracks.prompting.cookbook_sections`)."""
    from codeverse.tracks.scene import ENV_RECIPES, ZONE_RECIPES

    cookbook = load_text("scene_threejs/cookbook.md")
    from codeverse.prompts.sections import find_section, split_sections

    secs = split_sections(cookbook)
    for names in (ENV_RECIPES, ZONE_RECIPES):
        for n in names:
            assert find_section(secs, n) is not None, n
    for tpl in ("tracks/scene_zone.j2", "tracks/scene_env.j2", "tracks/scene_refine.j2"):
        assert "{{ recipes }}" in load_text(tpl), tpl


def test_cookbook_sections_inlines_whole_chapters_and_clips_safely(tmp_path) -> None:
    from types import SimpleNamespace

    from codeverse.tracks.prompting import cookbook_sections
    from codeverse.tracks.scene import ZONE_RECIPES

    ctx = SimpleNamespace(cookbook_text=load_text("scene_threejs/cookbook.md"))
    text = cookbook_sections(ctx, ZONE_RECIPES)
    assert all(f"## {t.split(':')[0]}" in text or t.split(":")[0] in text for t in ZONE_RECIPES)
    for token in ("setColorAt", "cameraMask", "foreground frame", "±0.10–0.20 rad"):
        assert token in text, token
    assert len(text) > 12_000
    assert cookbook_sections(ctx, ZONE_RECIPES, max_chars=500).rstrip().endswith("cookbook.md]")
    assert cookbook_sections(SimpleNamespace(cookbook_text=""), ZONE_RECIPES) == ""
    assert cookbook_sections(ctx, ["zzz qqq"]) == ""                  # no match → nothing, never junk


def test_the_env_and_zone_briefs_hand_the_enclosure_to_env_for_an_interior() -> None:
    """2026-09-07: six interior runs were "not enclosed" because no brief said whose the walls
    are.  The env brief claims them (and the openings, and the light) only when the plan
    says interior; the zone brief forbids building them and puts fixtures flush to the bounds."""
    from codeverse.prompts import render

    base = dict(title="t", spec_prompt="p", setting="s", mood="m", bounds="b", environment="e", frame_doc="", contract="",
                zones_table="", cameras="", effects="", animation="", asset_api="", textures="", recipes="", constraints_text="",
                references="", skills="", tool_cards="", zone_name="Z", zone_description="d", zone_bbox="bb", zone_contents=[],
                zone_file="src/zones/z.js", neighbours=[], layout="")
    env_in = render("tracks/scene_env.j2", **base, interior=True)
    env_out = render("tracks/scene_env.j2", **base, interior=False)
    assert "the enclosure is yours" in env_in and "roomShell" in env_in and "openings" in env_in
    assert "enclosure is yours" not in env_out
    zone_in = render("tracks/scene_zone.j2", **base, interior=True)
    zone_out = render("tracks/scene_zone.j2", **base, interior=False)
    assert "never build walls or a roof" in zone_in and "flush against the inner face" in zone_in
    assert "never build walls" not in zone_out
