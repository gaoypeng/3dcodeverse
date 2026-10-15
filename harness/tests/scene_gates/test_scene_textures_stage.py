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


@pytest.mark.parametrize("template", ["scene_env.j2", "scene_zone.j2"])
def test_the_pack_renders_and_no_recipe_block_competes_with_it(template: str):
    """The inlined cookbook chapters that used to sit above the pack are gone (D106): the pack
    is the only material block, and a stray ``recipes`` variable renders nothing."""
    out = _render(template, textures="TEXTURE_BLOCK_MARKER", recipes="RECIPE_MARKER")
    assert "TEXTURE_BLOCK_MARKER" in out and "RECIPE_MARKER" not in out and "# Recipes" not in out


@pytest.mark.parametrize("template", ["scene_env.j2", "scene_zone.j2"])
def test_a_prompt_with_the_pack_does_not_forbid_textures(template: str):
    """The rules said "no textures" and the pack below them said "use them"."""
    assert "no textures" not in _render(template, textures="TEXTURE_BLOCK_MARKER").lower()
    assert "no textures" in _render(template).lower()
