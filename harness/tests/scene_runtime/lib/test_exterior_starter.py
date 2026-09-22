"""The starter's outdoor world ships from the library (D71, 2026-09-08).

Measured over the day's exterior runs: with a flat one-colour plane and a bare dome as the
default, every env session that under-delivered (or died in the 503 storm) shipped "flat
untextured ground", "no aerial perspective", "hard world edge" — the three most frequent
defects of the tally over 43 judged rounds.  The starter now builds its ground from
`terrain.ground()` (level inside CONTENT_RADIUS, rolling beyond), its sky / ridge / fog
from `worldShell()` and the middle distance from `makeOutskirts()`.
"""
from __future__ import annotations

from pathlib import Path

from _probe import measure

from codeverse3d.contracts.common import Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.languages.scene_threejs import STARTER_DIR, _env_for_plan
from codeverse3d.tracks.planner import plan_example

_LIBS = ("environment.js", "terrain.js", "noise.js", "materials.js", "sky.js", "shader.js")


def _probe_script() -> str:
    env = (STARTER_DIR / "env.js").read_text()
    body = env.replace("import * as THREE from 'three';", "").replace("export function", "function").replace("export const", "const")
    return f"""
import * as THREE from 'three';
{body}
const scene = new THREE.Scene();
const out = buildEnv({{ scene, THREE }});
const names = [];
scene.traverse((o) => {{ if (o.name) names.push(o.name); }});
const h = (x, z) => heightAt(x, z);
console.log(JSON.stringify({{
  names: names.filter((n) => ['Environment', 'Ground', 'WorldShell', 'SkyGradient', 'HorizonRidge', 'Outskirts', 'SunRigSun', 'SunRigFill'].includes(n)),
  fogExp2: scene.fog && scene.fog.isFogExp2 === true,
  bgIsFog: scene.background.getHex() === scene.fog.color.getHex(),
  insideMax: Math.max(...[[0, 0], [20, 10], [-30, 25], [40, -15]].map(([x, z]) => Math.abs(h(x, z)))),
  outsideSpread: Math.max(...[[70, 0], [0, -75], [60, 60]].map(([x, z]) => h(x, z))) - Math.min(...[[70, 0], [0, -75], [60, 60]].map(([x, z]) => h(x, z))),
  groundTextured: !!(out.ground.material.map),
  envMap: !!scene.environment,
}}));
"""


def test_the_starter_world_is_the_librarys_ground_shell_and_outskirts():
    got = measure(_probe_script(), _LIBS)
    for n in ("Ground", "WorldShell", "SkyGradient", "HorizonRidge", "Outskirts", "SunRigSun", "SunRigFill"):
        assert n in got["names"], (n, got["names"])
    assert got["fogExp2"] and got["bgIsFog"] and got["groundTextured"] and got["envMap"], got
    assert got["insideMax"] < 0.05, got            # level where the plan's cameras were written for y ≈ 0
    assert got["outsideSpread"] > 0.5, got         # and rolling beyond the content radius


def test_the_skeleton_fills_the_content_radius_from_the_plan_bounds():
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    env = _env_for_plan(plan)
    span = max(plan.bounds.extents[0], plan.bounds.extents[2], 40.0)
    assert f"export const CONTENT_RADIUS = {int(max(20, span / 2))};" in env
    assert "FOG_NEAR" not in env and "SKY_RADIUS" not in env
    assert Path(STARTER_DIR / "env.js").read_text().count("export const CONTENT_RADIUS = 45;") == 1


def test_the_ground_height_is_finite_for_a_signed_zero_column():
    """terrain.ground(): a coordinate of -1.5e-14 wrapped to exactly N and read past the lattice
    row — 15 NaN vertices in the outskirts ring, a NaN bounding box in the render console."""
    got = measure("""
import * as THREE from 'three';
import { mulberry32 } from './lib/noise.js';
import { ground } from './lib/terrain.js';
const G = ground({ size: 160, segments: 16, rand: mulberry32(7), relief: 3, scale: 60 });
console.log(JSON.stringify({ tiny: G.height(-1.5e-14, -80), zero: G.height(0, -80), far: G.height(-1.5e-14, -84.73), neg: G.height(-160.00000001, 12) }));
""", ("terrain.js", "noise.js", "materials.js", "shader.js"))
    assert all(isinstance(v, (int, float)) and v == v for v in got.values()), got
    assert abs(got["tiny"] - got["zero"]) < 1e-6
