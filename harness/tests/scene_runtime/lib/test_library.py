"""The shipped effect library (``starter/src/lib``, D51/D74) as a whole.

Three claims cover every module: it imports under node and
exports every call effects_catalog.md advertises for it; its factories build;
and every program they make compiles on the real renderer, fogged and
unfogged.  The ten surface-patch libraries are compiled chain by chain in
test_patch_union.py; ``shader.js``'s own construction paths in test_shader.py.
Per-module behaviour tests exist only for modules generated scenes import, and
for named bugs.
"""
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

# One build per module, as a scene calls it: a JS expression over `L` (every
# module's namespace, by stem), THREE, `rand`, `heightAt`, `sunDir`, `scene`,
# `on(patch...)` (a mesh whose standard material wears those patches) and
# `inst(patch...)` (the same as a 4-copy InstancedMesh, flat-shaded — the
# only compile of a patch's USE_INSTANCING branch).  Every value is an array
# of Object3Ds to add; await is allowed.
BUILD: dict[str, str] = {
    "accumulation.js": "[on((m) => L.accumulation.patchSnow(m), (m) => L.accumulation.patchSand(m))]",
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
    "caustics.js": "[on((m) => L.caustics.patchCaustics(m, { level: 1, sunDir }))]",
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
    "figure.js": "(() => { const a = L.figure.figure({ rand }); L.figure.walk(a, 0.4);"
                 " const b = L.figure.figure({ rand, fem: 1 }); L.figure.sit(b, 0.45);"
                 " const c = L.figure.figure({ rand });"
                 " L.figure.carry(c, new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.1, 0.3), std()));"
                 " return [a, b, c]; })()",
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
    "instancing.js": "[L.instancing.instanceAsset(() => { const g = new THREE.Group();"
                     " g.add(new THREE.Mesh(new THREE.BoxGeometry(1, 2, 1), std())); return g; },"
                     " L.instancing.scatterGrid({ x: 0, z: 0, w: 20, d: 20 }, 5, rand, { tint: 0.2 }))]",
    "materials.js": "['plaster', 'brick', 'granite', 'cobble', 'weatheredWood', 'brushedSteel', 'fabric',"
                    " 'foliage', 'soil', 'skin', 'water', 'glass'].map((n, i) => {"
                    " const k = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1),"
                    " L.materials.tint(L.materials[n](), i / 12)); k.position.x = i * 1.2; return k; })",
    "merge.js": "[L.merge.mergeStatic([0, 1, 2].map((i) => { const k = new THREE.Mesh("
                "new THREE.BoxGeometry(1, 1, 1), std({ color: 0x806040 + i })); k.position.x = i * 2;"
                " return k; }))]",
    "neon.js": "(() => { const tube = L.neon.makeNeonTube({ radius: 0.05, seed: 5 });"
               # the spill chained under two other libraries' patches (D74 keeps windows.js for it)
               " const wall = on((m) => L.surface_wear.patchMicroBreakup(m),"
               " (m) => L.windows.patchWindowInteriors(m, { seed: 4 }),"
               " (m) => L.neon.patchNeonSpill(m, { sources: [{ position: [0, 1, 1], color: 0xff2e6a }] }));"
               " return [tube, L.neon.makeNeonTube({ ambient: 0.8, seed: 6 }), wall,"
               " L.neon.makeLightTrails({ path: [[-10, 0, 0], [10, 0, 0]], seed: 2 })]; })()",
    "noise.js": "(() => { const g = new THREE.PlaneGeometry(10, 10, 16, 16); g.rotateX(-Math.PI / 2);"
                " L.noise.displaceY(g, 1, 0.2, 3);"
                " return [new THREE.Mesh(g, std({ map: L.noise.noiseDataTexture(32,"
                " (x, y) => L.noise.fbm2(x * 8, y * 8)) }))]; })()",
    "place.js": "(() => { const hero = new THREE.Mesh(new THREE.BoxGeometry(2, 3, 2), std());"
                " hero.position.y = 1.5; L.place.seat(hero, []);"
                " L.place.establishingShot('wide', hero); L.place.alongPath([[0, 0, 0], [5, 0, 0],"
                " [5, 0, 5]], 6); const f = L.figure.figure({ rand });"
                " L.place.crowdOn(L.place.route([[0, 0, 0], [10, 0, 0], [10, 0, 10]]), [f]).tick(1);"
                " return [hero, f]; })()",
    "rain.js": "(() => { const car = new THREE.Mesh(new THREE.BoxGeometry(4, 1.4, 2),"
               " std({ color: 0x2a3542 })); L.rain.wetten(car);"
               " return [car, L.rain.makeRain({ seed: 5, count: 300 }),"
               " L.rain.makeSplashes({ seed: 11, count: 100, surfaces: [car] }),"
               " L.rain.makePuddle(4, 3, { seed: 3 })]; })()",
    "roadway.js": "[on((m) => L.roadway.patchRoadSurface(m), (m) => L.roadway.patchSeamBand(m),"
                  " (m) => L.roadway.patchTracks(m))]",
    "shader.js": "[]",
    "signage.js": "[await L.signage.makeText('OPEN', { size: 0.4, emissive: 0xff3a4e }),"
                  " await L.signage.makeText('MARKET', { size: 0.5 }),"
                  " await L.signage.makeText('EST', { size: 0.2, variation: 0 })]",
    "sky.js": "(L.sky.makeSky(scene, {}), [])",
    "smalllife.js": "[...['midge', 'butterfly', 'firefly'].map((kind) =>"
                    " L.smalllife.makeInsects({ kind, count: 20, extent: 3, height: 2, seed: 4 })),"
                    " L.smalllife.makeReeds({ extent: 4, density: 10, waterY: 0.4, heightAt,"
                    " shadows: true, seed: 2 })]",
    "strata.js": "[on((m) => L.strata.patchRockStrata(m), (m) => L.strata.patchErosionStreaks(m))]",
    "submerged.js": "[on((m) => L.submerged.patchUnderwater(m), (m) => L.submerged.patchThinIce(m)),"
                    " L.submerged.makeRainRings({ count: 8 })]",
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
    "wetground.js": "[L.wetground.makeMirrorFloor(12, 10, { puddleMask: true, seed: 3 }),"
                    " L.wetground.makeMirrorFloor(6, 6, { fresnel: 0, detail: 0, ripple: 0 })]",
    "windows.js": "(() => { const b = L.building.block({ w: 16, d: 12, h: 24, rand, lit: 0.4 });"
                  " L.windows.makeNightWindows(b, { seed: 3 });"
                  " return [b, inst((m) => L.windows.patchWindowInteriors(m, { seed: 5 }))]; })()",
    "woodland.js": "[on((m) => L.woodland.patchBark(m, { kind: 'oak' })),"
                   " L.woodland.makeImposters({ count: 30, extent: 20, heightAt })]",
    "cloudvolume.js": "[(() => { const c = L.cloudvolume.makeCloudVolume({ size: [60, 18, 36], quality: 'low',"
                      " seed: 3 }); c.position.y = 20; return c; })()]",
    "fire.js": "[L.fire.makeFire({ radius: 0.3, height: 0.9, quality: 'low', seed: 7 }),"
               " L.fire.makeCandle({ seed: 2 })]",
    "firefield.js": "[L.firefield.makeFireField({ emitters: [{ position: [0, 0, 0], radius: 0.3, height: 1,"
                    " strength: 1 }, { position: [0.8, 0, 0.2], radius: 0.2, height: 0.6, strength: 0.7 }],"
                    " quality: 'low', seed: 5 })]",
    "ice.js": "[L.ice.makeFracturedIce({ size: [4, 3], thickness: 0.2, seed: 3 })]",
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
    "stream.js": "[L.stream.makeStream({ points: [[0, 0.6, -8], [2, 0.3, 0], [0, 0, 8]], width: 2, depth: 0.3,"
                 " seed: 4, reflectionSize: 256, obstacles: [{ u: 0.5, lateral: 0.2, radius: 0.3 }] })]",
    "tree.js": "[L.tree.makeTree({ species: 'birch', height: 4, maxLeaves: 600, seed: 3 }),"
               " L.tree.makeShrub({ maxLeaves: 300, seed: 2 })]",
    "waterfall.js": "[L.waterfall.makeWaterfall({ width: 2, height: 3, seed: 4 })]",
}

# What the GPU compiles here, in groups so xdist spreads the browser boots.
# shader.js is compiled through its own construction paths (test_shader.py)
# and the ten patch libraries chain by chain (test_patch_union.py).
GROUPS: dict[str, tuple[str, ...]] = {
    "vegetation": ("canopy.js", "cloth.js", "flowers.js", "foliage_shade.js", "grass.js",
                   "smalllife.js", "woodland.js", "dapple.js"),
    "sky_and_air": ("atmosphere.js", "celestial.js", "clouds.js", "environment.js", "godrays.js",
                    "sky.js", "veils.js", "flock.js"),
    "water": ("rain.js", "water.js", "watermist.js", "wetground.js", "finish.js"),
    "volumes": ("cloudvolume.js", "fire.js", "firefield.js", "smoke.js"),
    "ground": ("lifecycle.js", "meadow.js", "paving.js", "rock.js", "sand.js", "tree.js"),
    "flowing_water": ("ice.js", "ocean.js", "stream.js", "waterfall.js"),
    "built": ("building.js", "figure.js", "instancing.js", "materials.js", "merge.js", "neon.js",
              "noise.js", "place.js", "signage.js", "terrain.js", "urban.js", "windows.js"),
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
    """A module added to the library without a build here, or a group entry
    for a module that no longer ships, fails by name."""
    assert set(BUILD) == set(MODULES)
    grouped = [m for g in GROUPS.values() for m in g]
    assert len(grouped) == len(set(grouped))
    assert set(grouped) | set(PATCH_UNION) | {"shader.js"} == set(MODULES)


def test_every_module_imports_and_exports_what_the_catalog_advertises():
    """Every module imports under node (a syntax error or a bad import is a
    scene that cannot boot), and every call a catalog row names is a real
    export of a module that row names — the catalog is the agent's only index,
    and a stale name is an import that cannot resolve."""
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
        # point at another row's (wetground's "re-renders like `makeOcean`")
        called = set(re.findall(r"`([a-z][A-Za-z0-9]*)\(", row))
        named = set(re.findall(r"`([a-z][A-Za-z0-9]*)`", row)) - _PROSE
        stale += [f"{c} ({', '.join(mods)})" for c in sorted((called - have) | (named - anywhere))]
    assert not stale, stale


@pytest.mark.parametrize("fog", [True, False], ids=["fogged", "unfogged"])
@pytest.mark.parametrize("group", sorted(GROUPS))
def test_every_program_compiles_on_the_real_renderer(group, fog, tmp_path):
    """The one witness for GLSL: every build above, in a lit scene whose sun
    casts (or no depth material compiles), fogged and then not — USE_FOG is a
    define, and rain.js shipped a fog branch that could not compile for months
    because no fogged scene ever built it."""
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
    # a bend tighter than the bank folded the ribbon into down-facing water
    ("stream.js", "makeStream({ points: [[0, 1, 0], [6, .9, 4], [0, .8, 8], [6, .7, 12]], width: 2 })",
     "bends tighter"),
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


def test_fire_field_depth_proxies_follow_the_live_occluders():
    """Named bug: a proxy for an occluder removed or hidden since the last
    capture stayed in the proxy scene for the field's lifetime."""
    code, out = compile_scene(r"""
import * as THREE from 'three';
import { makeFireField } from './lib/firefield.js';
export function createScene({ renderer }) {
  const root = new THREE.Group(), mat = new THREE.MeshStandardMaterial();
  const a = new THREE.Mesh(new THREE.BoxGeometry(), mat), b = new THREE.Mesh(new THREE.BoxGeometry(), mat);
  root.add(a, b);
  const field = makeFireField({ occluders: [root], quality: 'low' }), depth = field.userData.depthCapture;
  const camera = new THREE.PerspectiveCamera(50, 1, .1, 50);
  camera.position.set(0, 1, 4); camera.lookAt(0, .5, 0); camera.updateMatrixWorld(true);
  const view = new THREE.Vector4(0, 0, 64, 64), counts = [];
  depth.capture(renderer, camera, view); counts.push(depth.proxies);
  root.remove(b); depth.capture(renderer, camera, view); counts.push(depth.proxies);
  a.visible = false; depth.capture(renderer, camera, view); counts.push(depth.proxies);
  a.visible = true; depth.capture(renderer, camera, view); counts.push(depth.proxies);
  if (counts.join() !== '2,1,0,1') throw Error('proxy counts ' + counts.join());
  field.userData.dispose();
  return { scene: new THREE.Scene(), cameras: [{ name: 'probe', position: [0, 3, 5], lookAt: [0, 0, 0] }] };
}
""", ("firefield.js",), audit_module="src/scene.js")
    assert code == 0, out
