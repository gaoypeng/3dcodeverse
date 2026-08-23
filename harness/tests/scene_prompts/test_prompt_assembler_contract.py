"""The scene track's prompts must describe the interface the harness ACTUALLY calls.

The scene track never lets the model write `src/scene.js`: `languages.scene_threejs.assemble`
generates it and is the only caller of `src/env.js` and `src/zones/*.js`.  When a prompt
teaches a different signature the model either wastes tokens on defensive shims or —
worse — writes an animation hook nothing calls (the `nothing_moves` defect that cost three
of the four v1 bench scenes a grade).  These tests pin the prompts to the generated file.
"""

from __future__ import annotations

import re

import pytest

from codeverse.contracts.plan import CameraPlan
from codeverse.languages.scene_threejs.assemble import render_scene_js
from codeverse.prompts import load_text

CAMS = [CameraPlan(name="Establishing", position=(1.0, 2.0, 3.0), look_at=(0.0, 1.0, 0.0), fov=44.0)]


@pytest.fixture(scope="module")
def generated() -> str:
    return render_scene_js(["pond_basin"], CAMS, [], env_ok=True)


@pytest.fixture(scope="module")
def zone_prompt() -> str:
    return load_text("tracks/scene_zone.j2")


@pytest.fixture(scope="module")
def env_prompt() -> str:
    return load_text("tracks/scene_env.j2")


def test_generated_scene_js_is_what_we_think_it_is(generated: str) -> None:
    """Guard the four call sites the prompts promise (if this changes, the prompts must)."""
    assert "import { build as buildPondBasin } from './zones/pond_basin.js';" in generated
    assert "const env = (await buildEnv(ctx)) || {};" in generated
    assert "if (env.update) env.update(t, dt);" in generated
    assert "for (const z of zones) if (z.userData.update) z.userData.update(t, dt);" in generated
    assert "tickEnv" not in generated and "userData.tick" not in generated


def test_zone_prompt_teaches_the_export_and_hook_the_assembler_calls(zone_prompt: str) -> None:
    assert "export function build(ctx)" in zone_prompt
    assert "userData.update" in zone_prompt
    # the historical bug: the prompt asked for `userData.tick`, a hook nothing calls.
    # It may only appear now as an explicit warning.
    for line in zone_prompt.splitlines():
        if "userData.tick" in line:
            assert "never called" in line, f"zone prompt still advertises userData.tick: {line!r}"


def test_env_prompt_teaches_ctx_signature_and_update(env_prompt: str) -> None:
    assert "buildEnv(ctx)" in env_prompt
    assert "buildEnv(THREE, scene)" not in env_prompt
    assert "update(t, dt)" in env_prompt
    assert "tickEnv" in env_prompt and "NEVER called" in env_prompt
    # the assembler greps this out of env.js to place the overview cameras
    assert "SUN_AZIMUTH_DEG" in env_prompt


def test_env_prompt_sun_azimuth_matches_the_assembler_regex(env_prompt: str) -> None:
    from codeverse.languages.scene_threejs.assemble import _SUN_RE

    example = re.search(r"`?export const SUN_AZIMUTH_DEG[^`\n]*", env_prompt)
    assert example, "env prompt must show the export the assembler looks for"
    assert _SUN_RE.search("export const SUN_AZIMUTH_DEG = 235;")


def test_asset_prompt_uses_the_hook_zones_can_forward() -> None:
    text = load_text("tracks/scene_asset.j2")
    assert "group.userData.update" in text
    assert "`userData.tick` is never called" in text
