"""The scene texture pack as the env and zone prompts see it (the wiring is pinned by the prompt manifest)."""

from __future__ import annotations

import pytest

from codeverse3d.config import Settings


def test_the_switch_is_off_by_default():
    """D58: the pack costs an image-model call per run, so it is off unless switched on."""
    assert Settings.model_fields["scene_textures"].default is False


def _render(name: str, **extra):
    from codeverse3d.prompts import render

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


GROUND_MANIFEST = {
    "raked_zen_gravel": {"file": "raked_zen_gravel.png", "family": "stone", "role": "ground",
                         "tile_size_m": 1.0, "roughness": 0.95},
    "dark_slate_flagstone": {"file": "dark_slate_flagstone.png", "family": "stone", "role": "path",
                             "tile_size_m": 1.5, "roughness": 0.8},
    "aged_cedar_planks": {"file": "aged_cedar_planks.png", "family": "wood", "role": "prop",
                          "tile_size_m": 0.8, "roughness": 0.75},
}


def test_the_pack_says_outright_that_the_ground_is_what_it_is_for():
    """A passive offer loses to the cookbook's ground chapter: the pack names its ground maps outright."""
    from codeverse3d.texturing.plan import texture_pack_prompt

    text = texture_pack_prompt(GROUND_MANIFEST)
    assert "The ground is what this pack is for" in text
    # it names the ground/path maps, and only those
    assert "`raked_zen_gravel`" in text and "`dark_slate_flagstone`" in text
    tail = text.split("The ground is what this pack is for")[1]
    assert "`aged_cedar_planks`" not in tail, "a prop texture is not a ground map"
    props = texture_pack_prompt({k: v for k, v in GROUND_MANIFEST.items() if v["role"] == "prop"})
    assert "aged_cedar_planks.png" in props and "The ground is what this pack is for" not in props


@pytest.mark.parametrize("template", ["scene_env.j2", "scene_zone.j2"])
def test_the_pack_has_the_last_word_over_the_recipes(template: str):
    """The cookbook's ground chapter is long and specific; whichever comes last wins."""
    out = _render(template, textures="TEXTURE_BLOCK_MARKER", recipes="RECIPE_MARKER")
    assert out.index("RECIPE_MARKER") < out.index("TEXTURE_BLOCK_MARKER")
