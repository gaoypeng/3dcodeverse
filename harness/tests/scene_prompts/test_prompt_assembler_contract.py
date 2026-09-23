"""The scene prompts teach the hooks the generated src/scene.js actually calls (the `nothing_moves` defect)."""

from __future__ import annotations

from codeverse3d.contracts.plan import CameraPlan
from codeverse3d.languages.scene_threejs import render_scene_js
from codeverse3d.prompts import load_text


def test_the_prompts_teach_the_hooks_the_assembler_calls() -> None:
    """Guard the four call sites the prompts promise (if the assembler changes, the prompts must)."""
    cams = [CameraPlan(name="Establishing", position=(1.0, 2.0, 3.0), look_at=(0.0, 1.0, 0.0), fov=44.0)]
    generated = render_scene_js(["pond_basin"], cams, [], env_ok=True)
    assert "import { build as buildPondBasin } from './zones/pond_basin.js';" in generated
    assert "const env = (await buildEnv(ctx)) || {};" in generated
    assert "if (env.update) env.update(t, dt);" in generated
    assert "for (const z of zones) if (z.userData.update) z.userData.update(t, dt);" in generated
    assert "tickEnv" not in generated and "userData.tick" not in generated

    zone_prompt, env_prompt = load_text("tracks/scene_zone.j2"), load_text("tracks/scene_env.j2")
    assert "export function build(ctx)" in zone_prompt and "userData.update" in zone_prompt
    # the historical bug: `userData.tick`, a hook nothing calls, may appear only as a warning
    assert all("never called" in ln for ln in zone_prompt.splitlines() if "userData.tick" in ln)
    assert "buildEnv(ctx)" in env_prompt and "buildEnv(THREE, scene)" not in env_prompt
    assert "update(t, dt)" in env_prompt and "tickEnv" in env_prompt and "NEVER called" in env_prompt
    asset = load_text("tracks/scene_asset.j2")
    assert "group.userData.update" in asset and "`userData.tick` is never called" in asset
