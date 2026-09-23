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
def test_the_pack_has_the_last_word_over_the_recipes(template: str):
    """The cookbook's ground chapter is long and specific; whichever comes last wins."""
    out = _render(template, textures="TEXTURE_BLOCK_MARKER", recipes="RECIPE_MARKER")
    assert out.index("RECIPE_MARKER") < out.index("TEXTURE_BLOCK_MARKER")
