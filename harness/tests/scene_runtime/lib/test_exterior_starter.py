"""The starter's env.js is the EXAMPLE scene's environment (the plan-less skeleton the live tests
build): a level plate named Ground, the library's sun rig and a fog — no landform, shell or
outskirts (the library landscape it used to ship was kept byte for byte by 29 of 30 iteration-0
scenes).  A PLAN skeleton never ships it: ``_env_for_plan`` writes an interface stub with the
plan's data, a plate, and no light, sky or atmosphere — it fails the gates until the author writes
the brief's (a passing placeholder was kept for the same reason the landscape was; 2026-10-15)."""
from __future__ import annotations

from pathlib import Path

from _probe import measure

from codeverse3d.contracts.common import Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.languages.scene_threejs import STARTER_DIR, _env_for_plan
from codeverse3d.tracks.planner import plan_example

_LIBS = ("environment.js", "terrain.js", "noise.js", "materials.js", "sky.js", "shader.js")


def _probe_script(env: str | None = None) -> str:
    env = (STARTER_DIR / "env.js").read_text() if env is None else env
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
  names,
  fog: !!scene.fog,
  bgIsFog: !!scene.fog && scene.background.getHex() === scene.fog.color.getHex(),
  fogFar: scene.fog ? scene.fog.far : 0,
  lights: names.filter((n) => /Sun|Fill|Light/.test(n)).length,
  groundSize: out.ground.geometry.parameters.width,
  heights: [[0, 0], [20, 10], [-30, 25], [70, 0], [0, -75]].map(([x, z]) => h(x, z)),
  envMap: !!scene.environment,
}}));
"""


def test_the_example_env_passes_the_env_gates():
    got = measure(_probe_script(), _LIBS)
    for n in ("Environment", "Ground", "SunRigSun", "SunRigFill"):
        assert n in got["names"], (n, got["names"])
    for n in ("WorldShell", "SkyGradient", "HorizonRidge", "Outskirts"):     # no landscape decided for the author
        assert n not in got["names"], (n, got["names"])
    assert got["fog"] and got["bgIsFog"] and got["envMap"], got              # no_fog / no_background / lit PBR
    assert all(isinstance(v, (int, float)) and v == v for v in got["heights"]), got
    assert got["fogFar"] >= 2 * (got["groundSize"] / 3), got                 # fog_short on the plan the plate was sized for


def test_the_plan_stub_ships_a_plate_and_the_plans_light_and_nothing_that_passes():
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    env = _env_for_plan(plan)
    span = max(plan.bounds.extents[0], plan.bounds.extents[2])
    assert f"export const GROUND_SIZE = {int(span * 3)};" in env
    assert "export const SUN_AZIMUTH_DEG = 250;" in env and "export const SUN_ELEVATION_DEG = 8;" in env   # the plan's, not a constant
    code = "\n".join(ln for ln in env.splitlines() if not ln.lstrip().startswith("//"))
    for word in ("CONTENT_RADIUS", "worldShell(", "makeOutskirts(", "flat:", "TUNE these", "sunRig(", "MOOD", "scene.fog =",
                 "scene.background =", "scene.environment =", "Light("):
        assert word not in code, word
    assert "export function heightAt(x, z)" in env and "export function buildEnv(ctx)" in env and "ground.name = 'Ground'" in env
    # a plan that states no sun: the rig's own guess, flagged as such, never a decided light
    blank = _env_for_plan(plan.model_copy(update={"environment": "a cave; no sky"}))
    assert "export const SUN_AZIMUTH_DEG = 45;" in blank and "rig's guess" in blank and "SUN_ELEVATION_DEG = null" in blank
    got = measure(_probe_script(_env_for_plan(plan)), _LIBS)
    assert "Ground" in got["names"] and not got["fog"] and not got["envMap"] and not got["lights"], got   # fails no_fog / dark_frame as shipped
    starter = Path(STARTER_DIR / "env.js").read_text()
    assert "export function heightAt(x, z)" in starter and "export function buildEnv(ctx)" in starter
    assert "export const SUN_AZIMUTH_DEG" in starter


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
