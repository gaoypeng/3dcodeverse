"""What the scene rubric scores and the prompts inline about picture quality (v1 bench defects)."""

from __future__ import annotations

import pytest

from codeverse3d.judges.rubrics import load_rubric
from codeverse3d.prompts import load_text

COMPOSITION_DEFECTS = ("flat_ground", "monotonous_vegetation", "empty_midground",
                       "undressed_scene", "thin_atmosphere")


def test_scene_rubric_craft_defects_inform_and_cannot_zero_a_scene() -> None:
    r = load_rubric("scene_v2")
    assert set(COMPOSITION_DEFECTS) <= {d.id for d in r.defects}
    for did in COMPOSITION_DEFECTS:
        d = r.defect(did)
        assert 0.0 < d.penalty <= 0.05 and d.cap is None, did
    # the five craft items together take at most 0.20: they inform, they cannot sink a scene
    assert sum(r.defect(did).penalty for did in COMPOSITION_DEFECTS) == pytest.approx(0.18)
    # scene_v2 (2026-10-14): the whole checklist sums past 1.0 (was 0.60 under v1's 0.62 bound).  The
    # weight that moved is the placement / detail items — floating 0.10, sunken 0.08, interpenetration
    # 0.08, wrong_scale 0.06, missing_planned_element 0.06, hero_misplaced 0.06, hard-edged particles
    # 0.04 — the same weights the object rubric gives floating_part / interpenetration, and each of
    # the four measured ones also caps (0.60-0.75) so the penalty only matters under the cap.  A
    # scene with EVERY item present is a wrong, unlit, floating, clipping world: zero is its score.
    # The exact figure is tests/judges/test_scene_judge.py's (the judge lane adds items such as
    # diorama_look, 2026-10-15); this test keeps the invariant.
    total = sum(d.penalty for d in r.defects)
    assert 1.0 <= total <= 1.10, total
    assert sum(d.penalty for d in load_rubric("scene_v1").defects) <= 0.62  # the frozen replay rubric


def test_no_scene_stage_inlines_cookbook_recipes() -> None:
    """Owner, 2026-10-15: the same four ENV and six ZONE cookbook chapters used to be inlined into
    every env / zone / refine session whatever the brief ("copy these numbers and snippets"), and
    the 60 iteration-0 scenes executed them verbatim.  The track has no recipe tables, no recipe
    inliner and passes no ``recipes`` variable; the scene templates render without one."""
    import inspect

    from codeverse3d.tracks import prompting, scene

    for gone in ("ENV_RECIPES", "ZONE_RECIPES", "ZONE_RECIPES_MAX_CHARS", "refine_recipes"):
        assert not hasattr(scene, gone), gone
    assert not hasattr(prompting, "cookbook_sections")
    assert "recipes=" not in inspect.getsource(scene)


def test_the_env_and_zone_briefs_hand_the_enclosure_to_env_for_an_interior() -> None:
    """Six interior runs were "not enclosed": the env brief owns the walls only when the plan says interior."""
    from codeverse3d.prompts import render

    base = dict(title="t", spec_prompt="p", setting="s", mood="m", bounds="b", environment="e", frame_doc="", contract="",
                zones_table="", cameras="", effects="", animation="", asset_api="", textures="", constraints_text="",
                references="", skills="", tool_cards="", zone_name="Z", zone_description="d", zone_bbox="bb", zone_contents=[],
                zone_file="src/zones/z.js", neighbours=[], layout="")
    env_in = render("tracks/scene_env.j2", **base, interior=True, glazed=False)
    env_out = render("tracks/scene_env.j2", **base, interior=False, glazed=False)
    assert "This is an INTERIOR" in env_in and "roomShell" in env_in and "openings" in env_in
    assert "INTERIOR" not in env_out
    # the zone brief names env.js as the enclosure's owner only for an interior (the wording is the prompt's;
    # the ownership is the contract: createScene builds env first, zones return their own group)
    zone_in = render("tracks/scene_zone.j2", **base, interior=True)
    zone_out = render("tracks/scene_zone.j2", **base, interior=False)
    assert "INTERIOR" in zone_in and "`env.js`'s" in zone_in
    assert "INTERIOR" not in zone_out


def test_the_practical_light_numbers_live_in_the_lib_and_no_prompt_restates_them() -> None:
    """Audit N41: one scene session read PointLight 0.5–2 (system prompt, skill) and 6–14 with
    10–16 lights (same skill), emissive 1.5–4 / ≤ 3 / 2–6, and a night rig of "moon ≤ 0.2, no
    HemisphereLight" that sunRig never builds.  `PRACTICAL` in environment.js owns the numbers
    (the cookbook that once restated them is gone, 2026-10-15); every text points there."""
    import re

    from codeverse3d.languages.scene_threejs import lib_files
    from codeverse3d.skills import skills_dir

    env = next(p for p in lib_files() if p.name == "environment.js").read_text(encoding="utf-8")
    body = re.search(r"export const PRACTICAL = Object\.freeze\(\{([^}]*)\}\)", env)
    assert body, "environment.js no longer exports PRACTICAL"
    practical = {k: float(v) for k, v in re.findall(r"(\w+): ([\d.]+)", body.group(1))}
    assert {"emissiveIntensity", "intensity", "distance", "decay"} <= set(practical)
    others = {"system": load_text("scene_threejs/system.md"), "contract": load_text("scene_threejs/contract.md"),
              "lighting": (skills_dir() / "c3d-scene-lighting" / "SKILL.md").read_text(encoding="utf-8"),
              "materials": (skills_dir() / "c3d-scene-materials" / "SKILL.md").read_text(encoding="utf-8")}
    restated = re.compile(r"PointLight\W{0,4}\(?\*{0,2}\d|`intensity` \d|emissive\w*`?\W{0,4}\*{0,2}\d|<= 0\.2|"
                          r"HemisphereLight` \d")
    assert not {k: restated.findall(v) for k, v in others.items() if restated.search(v)}
