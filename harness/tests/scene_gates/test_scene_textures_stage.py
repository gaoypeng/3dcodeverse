"""The scene texture pack, wired into the loop.

`texturing.plan.scene_texture_pack` generates a tileable pack into `public/textures/`,
and `texture_pack_prompt` — whose own docstring says "Prompt snippet for zone/env
generation" — describes it with the exact loading idiom.  Nothing in `tracks/scene.py`
called either: `Spec.options.texture` does nothing on this track, and the generator was
never told a pack could exist.

Measured on bench/out/scene_baseline (2026-09-05): of 24 judge issues over five scored
cells, **four say the GROUND is a flat untextured colour**, in near-identical words
("single flat brown color", "single flat color with no cobblestone texture", "flat,
untextured blueish plane with no material blending").  It is the most consistent defect
in the battery, and it is a harness gap rather than a limit of three.js.

OFF by default: it costs an image-model call per run and what that buys is unmeasured.
"""

from __future__ import annotations

import pytest

from codeverse.config import SCENE_TEXTURES_ENV, get_settings, scene_textures_enabled
from codeverse.tracks.plan_features import LIVE_SWITCHES


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    monkeypatch.delenv(SCENE_TEXTURES_ENV, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_the_switch_is_off_by_default_and_readable_at_call_time(monkeypatch):
    assert scene_textures_enabled() is False
    monkeypatch.setenv(SCENE_TEXTURES_ENV, "1")
    assert scene_textures_enabled() is True
    monkeypatch.setenv(SCENE_TEXTURES_ENV, "false")
    assert scene_textures_enabled() is False


def test_the_switch_is_registered_as_live():
    """An A/B arm that differs only by a switch no code reads is byte-identical to its
    control; `plan_features` refuses such an arm, and only if the name is declared."""
    assert LIVE_SWITCHES[SCENE_TEXTURES_ENV] == "codeverse/config.py"


def _render(name: str, **extra):
    from codeverse.prompts import render

    base = dict(
        title="t", setting="a yard", mood="calm", bounds="0 0 0 / 10 5 10", environment="sunny",
        zones_table="- Yard: d", cameras="- overview", effects="(none)", animation="(none)",
        asset_api="(no assets)", output_format="write the files", prompt="p", track="scene",
        language="scene_threejs", tool_cards="", single_shot=False, recipes="", constraints="",
        must_have="", dimensions="", zone_name="Yard", zone_description="d", zone_bbox="b",
        zone_contents=[], zone_file="src/zones/yard.js", neighbours=[], layout="",
    )
    base.update(extra)
    return render(f"tracks/{name}", **base)


MANIFEST = {
    "cobblestone": {"file": "cobblestone.png", "family": "stone", "role": "ground",
                    "tile_size_m": 2.0, "roughness": 0.85},
}


@pytest.mark.parametrize("template", ["scene_env.j2", "scene_zone.j2"])
def test_the_pack_reaches_both_prompts(template: str):
    from codeverse.texturing.plan import texture_pack_prompt

    snippet = texture_pack_prompt(MANIFEST)
    with_pack = _render(template, textures=snippet)
    assert "/public/textures/cobblestone.png" in with_pack
    assert "loaders.texture.load" in with_pack, "the loading idiom has to travel with the list"


@pytest.mark.parametrize("template", ["scene_env.j2", "scene_zone.j2"])
def test_no_pack_adds_nothing_to_either_prompt(template: str):
    from codeverse.texturing.plan import texture_pack_prompt

    assert texture_pack_prompt({}) == ""
    empty = _render(template, textures="")
    assert "textures" not in empty.lower().split("## output")[0] or "/public/textures/" not in empty


def test_the_prompt_context_carries_the_manifest_the_stage_produced(monkeypatch):
    """`_ctx` is the ONE place env and zone prompts get their context, so the manifest has
    to arrive there or the stage generates textures nothing is told about — which is
    exactly the state the loop was in before this."""
    from types import SimpleNamespace

    import codeverse.tracks.scene as S
    from codeverse.contracts.plan import BBox, CameraPlan, ScenePlan, ZonePlan

    seen: dict = {}
    monkeypatch.setattr(S, "base_prompt_context", lambda ctx, **kw: seen.update(kw) or kw)

    bb = BBox(center=(0, 0, 0), extents=(10, 5, 10))
    plan = ScenePlan(title="t", summary="s", setting="yard", mood="calm", bounds=bb, environment="sunny",
                     zones=[ZonePlan(name="Yard", description="d", bbox=bb, contents=[])], assets=[],
                     cameras=[CameraPlan(name="overview", position=(1, 1, 1), look_at=(0, 0, 0), fov=50, purpose="p")],
                     animation=[], effects=[])

    ctx = SimpleNamespace(plan=plan, extra={"textures": MANIFEST})
    S.SceneTrack()._ctx(ctx)  # type: ignore[arg-type]
    assert "/public/textures/cobblestone.png" in seen["textures"]

    ctx_none = SimpleNamespace(plan=plan, extra={})
    seen.clear()
    S.SceneTrack()._ctx(ctx_none)  # type: ignore[arg-type]
    assert seen["textures"] == "", "no pack, no prompt block"


GROUND_MANIFEST = {
    "raked_zen_gravel": {"file": "raked_zen_gravel.png", "family": "stone", "role": "ground",
                         "tile_size_m": 1.0, "roughness": 0.95},
    "dark_slate_flagstone": {"file": "dark_slate_flagstone.png", "family": "stone", "role": "path",
                             "tile_size_m": 1.5, "roughness": 0.8},
    "aged_cedar_planks": {"file": "aged_cedar_planks.png", "family": "wood", "role": "prop",
                          "tile_size_m": 0.8, "roughness": 0.75},
}


def test_the_pack_says_outright_that_the_ground_is_what_it_is_for():
    """Measured 2026-09-05: the first two cells of the texture arm generated 8 and 9
    textures, carried the block verbatim in their env prompt, and used ZERO of them across
    `env.js` and six zone modules.  Three things told the model to write a procedural
    colour blend — the plan's own ground sentence, the cookbook chapter "Ground that reads
    real (blend, paths, edges — never one flat colour)", and that chapter's position after
    the block — against one passive list.  A passive offer loses."""
    from codeverse.texturing.plan import texture_pack_prompt

    text = texture_pack_prompt(GROUND_MANIFEST)
    assert "The ground is what this pack is for" in text
    # it names the ground/path maps, and only those
    assert "`raked_zen_gravel`" in text and "`dark_slate_flagstone`" in text
    tail = text.split("The ground is what this pack is for")[1]
    assert "`aged_cedar_planks`" not in tail, "a prop texture is not a ground map"


def test_a_pack_with_no_ground_map_makes_no_ground_claim():
    from codeverse.texturing.plan import texture_pack_prompt

    props = {k: v for k, v in GROUND_MANIFEST.items() if v["role"] == "prop"}
    text = texture_pack_prompt(props)
    assert "aged_cedar_planks.png" in text
    assert "The ground is what this pack is for" not in text


@pytest.mark.parametrize("template", ["scene_env.j2", "scene_zone.j2"])
def test_the_pack_has_the_last_word_over_the_recipes(template: str):
    """The cookbook's ground chapter is long and specific; whichever comes last wins."""
    out = _render(template, textures="TEXTURE_BLOCK_MARKER", recipes="RECIPE_MARKER")
    assert out.index("RECIPE_MARKER") < out.index("TEXTURE_BLOCK_MARKER")
