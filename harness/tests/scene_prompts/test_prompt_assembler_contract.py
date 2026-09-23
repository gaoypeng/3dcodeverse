"""The scene prompts teach the hooks the generated src/scene.js actually calls (the `nothing_moves` defect)."""

from __future__ import annotations

import re

import pytest

from codeverse3d.contracts.plan import CameraPlan
from codeverse3d.languages.scene_threejs import render_scene_js
from codeverse3d.prompts import load_text

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


def test_the_prompts_teach_the_hooks_the_assembler_calls(zone_prompt: str, env_prompt: str) -> None:
    assert "export function build(ctx)" in zone_prompt and "userData.update" in zone_prompt
    # the historical bug: `userData.tick`, a hook nothing calls, may appear only as a warning
    assert all("never called" in ln for ln in zone_prompt.splitlines() if "userData.tick" in ln)
    assert "buildEnv(ctx)" in env_prompt and "buildEnv(THREE, scene)" not in env_prompt
    assert "update(t, dt)" in env_prompt and "tickEnv" in env_prompt and "NEVER called" in env_prompt
    asset = load_text("tracks/scene_asset.j2")
    assert "group.userData.update" in asset and "`userData.tick` is never called" in asset


def test_env_prompt_sun_azimuth_matches_the_assembler_regex(env_prompt: str) -> None:
    from codeverse3d.languages.scene_threejs import _SUN_RE

    example = re.search(r"`?export const SUN_AZIMUTH_DEG[^`\n]*", env_prompt)
    assert example, "env prompt must show the export the assembler looks for"
    assert _SUN_RE.search("export const SUN_AZIMUTH_DEG = 235;")
