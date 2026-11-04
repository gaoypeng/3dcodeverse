"""The shipped effect library (``starter/src/lib``, D51/D74) as a whole."""
from __future__ import annotations

import json
import re

import pytest

from codeverse3d.languages.scene_threejs import lib_files
from codeverse3d.prompts import PROMPTS_DIR
from tests.scene_runtime.lib._probe import compile_scene, measure
from tests.scene_runtime.lib.test_patch_union import LIBRARIES as PATCH_UNION

pytestmark = pytest.mark.node

MODULES = sorted(p.name for p in lib_files())

# backticked words in catalog rows that name an option or a hook, not a call
_PROSE = {"update", "tick", "ambient", "sunDir", "shadows", "true", "false", "color"}

# One build per module, as a scene calls it: a JS expression over `L` (module namespaces by stem), THREE,
# `rand`, `heightAt`, `sunDir`, `scene`, `on(patch...)` (a mesh wearing those patches) and `inst(patch...)`
# (a 4-copy InstancedMesh: the only compile of a patch's USE_INSTANCING branch); each value is an array of Object3Ds.
BUILD: dict[str, str] = {
    "aging.js": "[on((m) => L.aging.patchDripStains(m), (m) => L.aging.patchRust(m),"
                " (m) => L.aging.patchDust(m))]",
    "atmosphere.js": "[L.atmosphere.makeHeightFog({ scene, extent: 30, top: 4, heightAt, seed: 4 }),"
                     " inst((m) => L.atmosphere.patchAerialPerspective(m, { scene }))]",
    "building.js": "(() => { const b = L.building.block({ w: 10, d: 8, h: 12, rand, lit: 0.4 });"
                   " L.building.roofClutter(b, { rand });"
                   " return [b, L.building.cottage({ rand, lit: true }), L.building.tower({ rand }),"
                   " L.building.casement({ lit: true }),"
                   " L.building.cityFabric({ rand, width: 60, depth: 60 }).group]; })()",
    "canopy.js": "[L.canopy.makeCanopy({ crowns: [{ position: [0, 4, 0], radius: 2, height: 3 },"
                 " { position: [6, 1, 0], size: [4, 1.5, 1] }], shadows: true })]",
    "celestial.js": "[L.celestial.makeHeatShimmer({ seed: 1 }), L.celestial.makeStars({ count: 200, seed: 2 }),"
                    " L.celestial.makeStars({ count: 200, seed: 4, ambient: 0.9 }),"
                    " L.celestial.makeAurora({ seed: 3 }), L.celestial.makeAurora({ seed: 5, ambient: 0.9 })]",
    "cloth.js": "[L.cloth.makeFlag({ seed: 3 }), L.cloth.makeBanner({ seed: 5 }),"
                " L.cloth.makeWheatField({ extent: 6, density: 20, seed: 7, shadows: true })]",
    "clouds.js": "[L.clouds.makeClouds({ seed: 11, preset: 'day', sunDir }),"
                 " L.clouds.makeCirrus({ seed: 23, sunDir })]",
    "damp.js": "[on((m) => L.damp.patchMoss(m), (m) => L.damp.patchMoisture(m),"
               " (m) => L.damp.patchCrackedMud(m))]",
    "dapple.js": "[inst((m) => L.dapple.patchCanopyShade(m, { density: 0.6 }),"
                 " (m) => L.dapple.patchDappledLight(m, { height: 7, sunDir }))]",
    "environment.js": "(() => { const rig = L.environment.sunRig({ azimuth: 140, elevation: 30 });"
                      " return [L.environment.worldShell({}).group, rig.sun, rig.fill, rig.sunDisc,"
                      " L.environment.makeOutskirts({ inner: 30, heightAt, seed: 2 }),"
                      " L.environment.roomShell({ center: [0, 1.5, 30], extents: [6, 3, 5],"
                      " openings: [{ wall: 'south', width: 1.2, height: 2 }] })]; })()",
    "finish.js": "[on((m) => L.finish.patchTranslucency(m, { thickness: 0.01 }),"
                 " (m) => L.finish.patchIridescence(m, { seed: 4 }))]",
    "flock.js": "[L.flock.makeFlock({ count: 60, seed: 4 }),"
                " L.flock.makeFlock({ count: 30, kind: 'fish', extent: 8, height: 3, seed: 5 })]",
    "flowers.js": "[L.flowers.makeFlowers({ extent: 4, heightAt }),"
                  " L.flowers.makeFalling({ extent: 4, height: 3 })]",
    "foliage_shade.js": "[inst((m) => L.foliage_shade.patchLeafSSS(m, { sunDir }),"
                        " (m) => L.foliage_shade.patchWind(m, { strength: 0.3, height: 5 })),"
                        " on((m) => L.foliage_shade.patchWind(m, { height: 5 }),"
                        " (m) => L.foliage_shade.patchRootContact(m, { band: 0.6 }))]",
    "godrays.js": "[L.godrays.makeGodRays({ count: 4, height: 5, seed: 4 }),"
                  " L.godrays.makeGodRays({ count: 3, height: 5, ambient: 0.05, seed: 9 })]",
    "grass.js": "[L.grass.makeGrass({ extent: 6, density: 40, heightAt, shadows: true })]",
    "materials.js": "['plaster', 'brick', 'granite', 'cobble', 'weatheredWood', 'brushedSteel', 'fabric',"
                    " 'foliage', 'soil', 'skin', 'water', 'glass'].map((n, i) => {"
                    " const k = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1),"
                    " L.materials.tint(L.materials[n](), i / 12)); k.position.x = i * 1.2; return k; })",
    "merge.js": "[L.merge.mergeStatic([0, 1, 2].map((i) => { const k = new THREE.Mesh("
                "new THREE.BoxGeometry(1, 1, 1), std({ color: 0x806040 + i })); k.position.x = i * 2;"
                " return k; }))]",
    "neon.js": "(() => { const tube = L.neon.makeNeonTube({ radius: 0.05, seed: 5 });"
               " const wall = on((m) => L.surface_wear.patchMicroBreakup(m),"
               " (m) => L.neon.patchNeonSpill(m, { sources: [{ position: [0, 1, 1], color: 0xff2e6a }] }));"
               " return [tube, L.neon.makeNeonTube({ ambient: 0.8, seed: 6 }), wall,"
               " L.neon.makeLightTrails({ path: [[-10, 0, 0], [10, 0, 0]], seed: 2 })]; })()",
    "noise.js": "(() => { const g = new THREE.PlaneGeometry(10, 10, 16, 16); g.rotateX(-Math.PI / 2);"
                " L.noise.displaceY(g, 1, 0.2, 3);"
                " return [new THREE.Mesh(g, std({ map: L.noise.noiseDataTexture(32,"
                " (x, y) => L.noise.fbm2(x * 8, y * 8)) }))]; })()",
    "roadway.js": "[on((m) => L.roadway.patchRoadSurface(m), (m) => L.roadway.patchSeamBand(m),"
                  " (m) => L.roadway.patchTracks(m))]",
    "shader.js": "[]",
    "sky.js": "(L.sky.makeSky(scene, {}), [])",
    "strata.js": "[on((m) => L.strata.patchRockStrata(m), (m) => L.strata.patchErosionStreaks(m))]",
    "surface_wear.js": "[on((m) => L.surface_wear.patchMicroBreakup(m), (m) => L.surface_wear.patchEdgeWear(m))]",
    "terrain.js": "[L.terrain.ground({ size: 40, segments: 24, rand, relief: 4 }).mesh,"
                  " L.terrain.cliff({ length: 20, height: 8, rand }).mesh]",
    "terrain_shade.js": "[on((m) => L.terrain_shade.patchTriplanar(m),"
                        " (m) => L.terrain_shade.patchSlopeSplat(m, { snowLine: 3 }))]",
    "urban.js": "[inst((m) => L.urban.patchCurtainWall(m, {})),"
                " on((m) => L.urban.patchCurtainWall(m, { curve: 4.5, coat: 1, blinds: 0.4 })),"
                " L.urban.makePowerLines({ seed: 5 }), L.urban.makeBillboard({ lit: true, seed: 6 })]",
    "veils.js": "[L.veils.makeRainVeil({ extent: 40, height: 20 }),"
                " L.veils.makeSnowfall({ extent: 20, height: 10, seed: 4 }),"
                " L.veils.makeMotes({ extent: 4, height: 3, seed: 6 })]",
    "water.js": "[L.water.makeOcean(18, 14, { sunDir })]",
    "watermist.js": "[L.watermist.makeWaterMist({ extent: 8, height: 2, heightAt }),"
                    " L.watermist.makeSpray({ origin: [0, 0, 0], seed: 3 })]",
    "waterside.js": "[on((m) => L.waterside.patchShoreWet(m), (m) => L.waterside.patchShoreFoam(m),"
                    " (m) => L.waterside.patchShallowWater(m))]",
    "woodland.js": "[on((m) => L.woodland.patchBark(m, { kind: 'oak' })),"
                   " L.woodland.makeImposters({ count: 30, extent: 20, heightAt })]",
    "cloudvolume.js": "[(() => { const c = L.cloudvolume.makeCloudVolume({ size: [60, 18, 36], quality: 'low',"
                      " seed: 3 }); c.position.y = 20; return c; })()]",
    "fire.js": "[L.fire.makeFire({ radius: 0.3, height: 0.9, quality: 'low', seed: 7 }),"
               " L.fire.makeCandle({ seed: 2 })]",
    "lifecycle.js": "(() => { const g = new THREE.Group(); g.add(new THREE.Mesh(new THREE.BoxGeometry(), std()));"
                    " L.lifecycle.attachDisposal(g, L.lifecycle.snapshotResources(g)); return [g]; })()",
    "meadow.js": "[L.meadow.makeMeadow({ size: [2, 2], density: 60, heightAt, seed: 4 })]",
    "ocean.js": "[L.ocean.makeOceanSurface({ width: 20, depth: 20, segments: 32, reflectionSize: 64, seed: 2 })]",
    "paving.js": "[L.paving.makePaving({ size: [2, 2], stoneSize: 0.24, seed: 3 }),"
                 " L.paving.makePaving({ size: [2, 2], pattern: 'setts', seed: 4 })]",
    "rock.js": "[L.rock.makeRock({ type: 'granite', size: [1, 0.8, 1], seed: 4 }),"
               " L.rock.makeRockField({ count: 6, radius: 3, seed: 5 })]",
    "sand.js": "[L.sand.makeSandTerrain({ size: [20, 20], seed: 3 })]",
    "smoke.js": "[L.smoke.makeSmoke({ quality: 'low', seed: 2 }), L.smoke.makeSteam({ quality: 'low' })]",
    "tree.js": "[L.tree.makeTree({ species: 'birch', height: 4, maxLeaves: 600, seed: 3 }),"
               " L.tree.makeShrub({ maxLeaves: 300, seed: 2 })]",
}

# What the GPU compiles here, in groups so xdist spreads the browser boots.
# shader.js is compiled through its own construction paths (test_shader.py)
# and the seven patch libraries chain by chain (test_patch_union.py).
GROUPS: dict[str, tuple[str, ...]] = {
    "vegetation": ("canopy.js", "cloth.js", "flowers.js", "foliage_shade.js", "grass.js",
                   "woodland.js", "dapple.js"),
    "sky_and_air": ("atmosphere.js", "celestial.js", "clouds.js", "environment.js", "godrays.js",
                    "sky.js", "veils.js", "flock.js"),
    "water": ("water.js", "watermist.js", "finish.js"),
    "volumes": ("cloudvolume.js", "fire.js", "smoke.js"),
    "ground": ("lifecycle.js", "meadow.js", "paving.js", "rock.js", "sand.js", "tree.js"),
    "flowing_water": ("ocean.js",),
    "built": ("building.js", "materials.js", "merge.js", "neon.js",
              "noise.js", "terrain.js", "urban.js"),
}

_PRELUDE = "\n".join(f"import * as L_{m[:-3]} from './lib/{m}';" for m in MODULES) + """
import * as THREE from 'three';
const L = { """ + ", ".join(f"{m[:-3]}: L_{m[:-3]}" for m in MODULES) + """ };
const rand = L.noise.mulberry32(7);
const heightAt = (x, z) => 0.3 * Math.sin(x * 0.1) * Math.cos(z * 0.1);
const sunDir = new THREE.Vector3(-0.6, 0.62, -0.5).normalize();
const std = (o = {}) => new THREE.MeshStandardMaterial(Object.assign({ color: 0x8b8478, roughness: 0.7 }, o));
const on = (...patches) => {
  const m = std();
  for (const p of patches) p(m);
  return new THREE.Mesh(new THREE.IcosahedronGeometry(0.6, 2), m);
};
const inst = (...patches) => {
  const m = std({ flatShading: true });
  for (const p of patches) p(m);
  const k = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(0.6, 2), m, 4);
  for (let i = 0; i < 4; i++) k.setMatrixAt(i, new THREE.Matrix4().makeTranslation(0, i * 1.3, 0));
  return k;
};
"""


def _builds(modules: tuple[str, ...]) -> str:
    """``const BUILT = {module: [Object3D...]}`` for these modules."""
    return "const BUILT = {};\n" + "\n".join(
        f"BUILT[{json.dumps(m)}] = await ({BUILD[m]});" for m in modules)


def test_every_module_is_built_and_compiled_somewhere():
    """A module added to the library without a build here, or a group entry for a module that no longer ships, fails by name."""
    assert set(BUILD) == set(MODULES)
    grouped = [m for g in GROUPS.values() for m in g]
    assert len(grouped) == len(set(grouped))
    assert set(grouped) | set(PATCH_UNION) | {"shader.js"} == set(MODULES)


def test_every_module_imports_and_exports_what_the_catalog_advertises():
    """Every module imports under node (a syntax error or a bad import is a scene that cannot boot), and every call a catalog row names is a real export o..."""
    out = measure("""
const names = """ + json.dumps(MODULES) + """;
const exports = {}, errors = {};
for (const n of names) {
  try { exports[n] = Object.keys(await import('./lib/' + n)); } catch (e) { errors[n] = String(e.stack || e); }
}
console.log(JSON.stringify({ exports, errors }));
""", tuple(MODULES))
    assert not out["errors"], out["errors"]
    exported = out["exports"]
    catalog = (PROMPTS_DIR / "scene_threejs" / "effects_catalog.md").read_text(encoding="utf-8")
    anywhere = {name for names in exported.values() for name in names}
    stale = []
    for row in catalog.splitlines():
        mods = re.findall(r"`lib/([a-z_]+\.js)`", row)
        if not row.startswith("| ") or not mods:
            continue
        have = {name for m in mods for name in exported[m]}
        # `name(` is a call into the row's own modules; a bare `name` may
        # point at another row's (meadow's "read by grass.js's `windOf`")
        called = set(re.findall(r"`([a-z][A-Za-z0-9]*)\(", row))
        named = set(re.findall(r"`([a-z][A-Za-z0-9]*)`", row)) - _PROSE
        stale += [f"{c} ({', '.join(mods)})" for c in sorted((called - have) | (named - anywhere))]
    # the scene planner's SHIPPED NATURAL EFFECTS list: every `module.js` (`makeX`, ...) pair is real, else
    # the planner routes an element to a zone that cannot build it (or, listed short, to a hand-built asset)
    plan = (PROMPTS_DIR / "tracks" / "plan_scene.j2").read_text(encoding="utf-8")
    shipped = re.findall(r"`([a-z_]+\.js)`\s*\(([^)]*)\)", plan)
    assert len(shipped) >= 9, shipped
    stale += [f"plan_scene.j2: {n} ({m})" for m, calls in shipped
              for n in re.findall(r"`(make[A-Za-z]+)`", calls) if n not in exported.get(m, ())]
    assert not stale, stale


@pytest.mark.parametrize("fog", [True, False], ids=["fogged", "unfogged"])
@pytest.mark.parametrize("group", sorted(GROUPS))
def test_every_program_compiles_on_the_real_renderer(group, fog, tmp_path):
    """The one witness for GLSL."""
    modules = GROUPS[group]
    scene = _PRELUDE + """
export const BOUNDS = { min: [-40, 0, -40], max: [40, 30, 40] };
export { heightAt };
export async function createScene({ renderer }) {
  const scene = new THREE.Scene();
  """ + ("scene.fog = new THREE.FogExp2(0xcfd8e6, 0.004);" if fog else "") + """
  scene.add(new THREE.HemisphereLight(0xbcd6ef, 0x4a4130, 0.9));
  const sun = new THREE.DirectionalLight(0xfff0d6, 2.5);
  sun.position.set(5, 8, 7);
  sun.castShadow = true;
  scene.add(sun);
  """ + _builds(modules) + """
  let i = 0;
  for (const objs of Object.values(BUILT)) {
    for (const o of objs) {
      if (!o.parent) { o.position.x += (i++ % 6) * 3 - 8; scene.add(o); }
    }
  }
  return {
    scene,
    cameras: [{ name: 'a', position: [0, 10, 30], lookAt: [0, 2, 0], fov: 60 }],
    update(t) {
      L.shader.tickShaders(scene, t);
      scene.traverse((o) => { if (o.userData.update) o.userData.update(t, 0.016);
                              else if (o.userData.tick) o.userData.tick(t, 0.016); });
    },
  };
}
"""
    report_path = tmp_path / "report.json"
    code, out = compile_scene(scene, tuple(MODULES), report=report_path)
    assert report_path.is_file(), out  # the preflight itself died: `out` says why
    report = json.loads(report_path.read_text(encoding="utf-8"))
    problems = [f"{e.get('file')}:{e.get('line')}: {e.get('message')}" for e in report["errors"]]
    # the star field opts out of fog on purpose: a star is at infinity
    problems += [f"{w.get('file')}: {w.get('message')}" for w in report["warnings"]
                 if "'StarField'" not in w.get("message", "")]
    assert code == 0 and report["ok"] and not problems, "\n".join(problems) or out
    compiled = report["compile"]
    assert compiled["gpu"], "this claim is only worth a real GPU"
    assert compiled["programs"] >= len(modules), compiled


# Named bugs where a factory once built silently broken output from input it
# should refuse: (module, call, a phrase the RangeError must carry).
REFUSALS = [
    # a NaN wind direction normalised to NaN and poisoned every dune vertex
    ("sand.js", "makeSandTerrain({ windDirection: [NaN, 1] })", "windDirection"),
]


@pytest.mark.parametrize(("module", "call", "phrase"), REFUSALS, ids=[r[0] for r in REFUSALS])
def test_factories_refuse_input_they_would_silently_break(module, call, phrase):
    out = measure(f"""
import * as M from './lib/{module}';
const {{ {call.split('(')[0]} }} = M;
let error = null;
try {{ {call}; }} catch (e) {{ error = e instanceof RangeError ? e.message : 'not a RangeError: ' + e; }}
console.log(JSON.stringify({{ error }}));
""", tuple(MODULES))
    assert out["error"] and phrase in out["error"], out


# One option rule (audit N33): null takes the default, an invalid one throws a RangeError naming it, a seed is
# any finite number, the sun is `sunDir` everywhere (a derived seed built in one effect and threw in its neighbour).
_OPTIONS = """
import * as THREE from 'three';
import { makeFire } from './lib/fire.js';
import { makeTree } from './lib/tree.js';
import { makeMeadow } from './lib/meadow.js';
import { makeOceanSurface } from './lib/ocean.js';
import { makeSmoke } from './lib/smoke.js';
import { makeClouds } from './lib/clouds.js';
import { makeCloudVolume } from './lib/cloudvolume.js';
import { makeSandTerrain } from './lib/sand.js';
import { makeRock } from './lib/rock.js';
import { makePaving } from './lib/paving.js';
import { readVec3 } from './lib/shader.js';
const calls = {
  fire: (o) => makeFire(o),
  tree: (o) => makeTree({ leafDensity: .05, ...o }), meadow: (o) => makeMeadow({ size: [1, 1], density: 20, ...o }),
  ocean: (o) => makeOceanSurface({ width: 10, depth: 10, segments: 16, ...o }),
  smoke: (o) => makeSmoke({ quality: 'low', ...o }), clouds: (o) => makeClouds({ count: 2, ...o }),
  cloudvolume: (o) => makeCloudVolume({ quality: 'low', ...o }),
  sand: (o) => makeSandTerrain({ size: [10, 10], segments: 16, ...o }),
  rock: (o) => makeRock(o), paving: (o) => makePaving({ size: [2, 2], ...o }),
};
const seeds = { null: null, big: 2 ** 40, fraction: 3.7, text: '7' };
const run = (f) => { try { f(); return 'ok'; } catch (e) { return e instanceof RangeError ? e.message : 'not a RangeError: ' + e; } };
const out = { seed: {} };
for (const [name, call] of Object.entries(calls))
  out.seed[name] = Object.fromEntries(Object.entries(seeds).map(([k, seed]) => [k, run(() => call({ seed }))]));
const sunOf = (g) => { let v; g.traverse((o) => { const u = o.material?.uniforms; v ??= (u?.seaSun ?? u?.uSunWorld)?.value; }); return v?.toArray(); };
out.sun = { ocean: sunOf(calls.ocean({ sunDir: [0, 1, 0] })), cloudvolume: sunOf(calls.cloudvolume({ sunDir: [0, 1, 0] })) };
out.nullDefaults = run(() => calls.meadow({ height: null }));
out.fireClamps = run(() => makeFire({ radius: 1e6 }));
out.badNames = [run(() => calls.meadow({ height: 'tall' })), run(() => calls.smoke({ spread: 5 })), run(() => calls.cloudvolume({ coverage: 2 }))];
out.point = readVec3([1, 2]).toArray().concat(readVec3(null, 4, 5, 6).toArray(), readVec3({ x: 1 }).toArray());
console.log(JSON.stringify(out));
"""


def test_every_factory_reads_its_options_by_one_rule():
    out = measure(_OPTIONS, tuple(MODULES))
    for name, row in out["seed"].items():
        assert row["null"] == row["big"] == row["fraction"] == "ok", (name, row)
        assert "seed" in row["text"], (name, row)
    assert out["sun"] == {"ocean": [0, 1, 0], "cloudvolume": [0, 1, 0]}, out["sun"]
    assert out["nullDefaults"] == out["fireClamps"] == "ok", out
    for phrase, message in zip(("height", "spread", "coverage"), out["badNames"], strict=True):
        assert phrase in message, out["badNames"]
    assert out["point"] == [1, 2, 0, 4, 5, 6, 1, 0, 0]
