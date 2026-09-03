"""signage.js: real extruded lettering, loadable under BOTH runtimes.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_signage_lib.py).  Its four laws are kept as they stood — real
glyph geometry from the shipped helvetiker face, centred on X/Z with the
baseline at y = 0, a deterministic rebuild, and every documented option
alive — because the census still imports scene modules under plain node
and a broken load path still kills it.

THE PORT'S OWN LAWS, each one measured on this host (frames under
fx/out/signage/):

1. THE FONT COMES FROM WHEREVER `three` CAME FROM.  Both halves of the
   dual-runtime loader assumed a `node_modules` next to the app and both
   were dead here.  Under node a bare workspace / temp probe has no parent
   `node_modules`, so `createRequire().resolve('three/examples/fonts/…')`
   threw MODULE_NOT_FOUND — every census-side `makeText` died.  In the
   browser our host serves the workspace at `/` and three at
   `/__runtime/node_modules/`, so `../../node_modules/…` 404d twice per
   render (console errors, run not-ok) and the module only survived by
   falling through to its unpkg pin — i.e. it needed the internet.  Both
   now ask `import.meta.resolve`, which answers from the page's import map
   in Chrome and from the module resolver (our `resolve_three.mjs` hook
   included) under node.  Rendered: `fx/out/signage/before` carried two
   404 console errors, `after` carries none.

2. A LETTER IS NOT A CUTOUT.  A sign faces the viewer, so the sun almost
   never does: in the showcase base scene (sun az 215, both cameras at
   az ~37) every glyph face is lit by sky + environment only, and one flat
   albedo over the whole line rendered as a pasted-on shape.  The glyphs
   now carry a deterministic paint variance in a vertex colour — mottle,
   grime at the baseline, extrusion WALLS down to ~0.63 and the bevel rim
   as the brightest thing on the letter — read off |n.z|, so it needs no
   material groups and survives any bevel setting.  Measured over the
   headline's own pixels (close view): lum_std 0.0683 -> 0.0837, hue_std
   0.0768 -> 0.0891.

3. THE VERTEX COLOUR MAY ONLY DARKEN.  It is normalised to a ceiling of
   1.0 so `color` stays the true albedo — a rim that multiplied a 0.80
   linear paint by 1.14 would have quietly broken the 0.02-0.8 band the
   default was moved into (0xf2f2f2, 0.887 linear, -> 0xe7e3da, 0.799).

4. A SIGN THAT LIGHTS NOTHING IS A DECAL.  An emissive line now carries a
   practical whose candela is derived from its own radiance x inked area.
   Measured at night on the board just above a 0.42 m OPEN sign: red
   channel 0.174 -> 0.265, warmth (r-b) +0.023 -> +0.108.

5. LETTERING IS MOUNTED, NEVER SEATED.  `userData.placement = 'free'`, or
   the host's settle pass (runtime_js/lib/host_placement.mjs) drops a line
   of letters off its facade onto the terrain.  Asserted against the real
   settleScene, with an unmarked crate as the control.
"""

from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import HARNESS, compile_scene, measure

_HOST_PLACEMENT = (HARNESS / "runtime_js" / "lib" / "host_placement.mjs").as_uri()

_PROBE = """
import * as THREE from 'three';
import { makeText, loadHelvetiker } from './lib/signage.js';
import { settleScene } from '__PLACEMENT__';

// Default load path: no opts.font escape hatch, no NODE_PATH, and this
// probe dir has no node_modules of its own.
const a = await makeText('OPEN', { size: 1 });
const b = await makeText('OPEN', { size: 1 });
const wide = await makeText('OPEN WIDE', { size: 1 });
const bold = await makeText('OPEN', { size: 1, bold: true });
const neon = await makeText('X', { size: 1, emissive: 0xff2244 });
const dark = await makeText('X', { size: 1, emissive: 0xff2244, light: false });
const flat = await makeText('OPEN', { size: 1, bevel: false });
const plain = await makeText('OPEN', { size: 1, variation: 0 });
const bare = await makeText('OPEN', { size: 1, shadow: false });

a.geometry.computeBoundingBox();
const bb = a.geometry.boundingBox;

const pa = a.geometry.attributes.position.array;
const pb = b.geometry.attributes.position.array;
const ca = a.geometry.attributes.color.array;
const cb = b.geometry.attributes.color.array;
let identical = pa.length === pb.length && ca.length === cb.length;
for (let i = 0; identical && i < pa.length; i++) {
  if (pa[i] !== pb[i]) identical = false;
}
for (let i = 0; identical && i < ca.length; i++) {
  if (ca[i] !== cb[i]) identical = false;
}

// vertex colour, split by |n.z|: caps face front/back, walls are the
// extrusion sides, the bevel ring is everything between.
const col = a.geometry.attributes.color;
const nrm = a.geometry.attributes.normal;
let capSum = 0, capN = 0, wallSum = 0, wallN = 0;
let minC = 9, maxC = -9, hueSum = 0, hueMax = 0;
for (let i = 0; i < col.count; i++) {
  const r = col.array[i * 3], g = col.array[i * 3 + 1], bl = col.array[i * 3 + 2];
  const nz = Math.abs(nrm.array[i * 3 + 2]);
  const l = (r + g + bl) / 3;
  if (nz > 0.98) { capSum += l; capN++; } else if (nz < 0.05) { wallSum += l; wallN++; }
  minC = Math.min(minC, r, g, bl);
  maxC = Math.max(maxC, r, g, bl);
  hueSum += Math.abs(r - bl);
  hueMax = Math.max(hueMax, Math.abs(r - bl));
}

// the settle pass must skip lettering and seat an unmarked object
const scene = new THREE.Scene();
const grd = new THREE.Mesh(new THREE.PlaneGeometry(60, 60),
                           new THREE.MeshStandardMaterial());
grd.name = 'Ground';
grd.rotation.x = -Math.PI / 2;
scene.add(grd);
const sign = await makeText('HOTEL', { size: 0.6 });
sign.position.set(0, 3, 0);
scene.add(sign);
const crate = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1),
                             new THREE.MeshStandardMaterial());
crate.name = 'Crate';
crate.position.set(6, 3, 0);
scene.add(crate);
const settled = settleScene(scene, THREE, { groundY: 0 });

const f1 = await loadHelvetiker();
const f2 = await loadHelvetiker();

console.log(JSON.stringify({
  w: bb.max.x - bb.min.x,
  h: bb.max.y - bb.min.y,
  d: bb.max.z - bb.min.z,
  minY: bb.min.y,
  cx: 0.5 * (bb.min.x + bb.max.x),
  cz: 0.5 * (bb.min.z + bb.max.z),
  userW: a.userData.size.w,
  verts: a.geometry.attributes.position.count,
  flatVerts: flat.geometry.attributes.position.count,
  identical,
  wideW: wide.userData.size.w,
  boldW: bold.userData.size.w,
  emissiveHex: neon.material.emissive.getHex(),
  emissiveI: neon.material.emissiveIntensity,
  plainEmissive: a.material.emissive.getHex(),
  cached: f1 === f2,
  name: a.name,
  // --- the port's own numbers
  colorHex: a.material.color.getHex(),
  colorLinear: [a.material.color.r, a.material.color.g, a.material.color.b],
  vertexColors: a.material.vertexColors,
  hasColorAttr: !!a.geometry.attributes.color,
  plainHasColorAttr: !!plain.geometry.attributes.color,
  plainVertexColors: plain.material.vertexColors,
  capMean: capSum / capN, wallMean: wallSum / wallN, capN, wallN,
  minC, maxC, hueMean: hueSum / col.count, hueMax,
  cast: a.castShadow, recv: a.receiveShadow,
  bareCast: bare.castShadow, bareRecv: bare.receiveShadow,
  placement: a.userData.placement,
  lightCd: neon.userData.light ? neon.userData.light.intensity : null,
  lightHex: neon.userData.light ? neon.userData.light.color.getHex() : null,
  lightZ: neon.userData.light ? neon.userData.light.position.z : null,
  lightShadow: neon.userData.light ? neon.userData.light.castShadow : null,
  neonChildren: neon.children.length,
  plainHasLight: a.userData.light !== undefined,
  darkHasLight: dark.userData.light !== undefined,
  darkChildren: dark.children.length,
  settledCount: settled.count,
  signY: sign.position.y,
  crateY: crate.position.y,
}));
""".replace("__PLACEMENT__", _HOST_PLACEMENT)


# The scene our check_shaders.mjs boots: a camera (a scene with none is
# reported as not booted and never reaches the compile stage) and both
# material variants, because a vertex-coloured standard material and an
# emissive one are two different programs.
_SCENE = """
import * as THREE from 'three';
import { makeText } from './lib/signage.js';

export const BOUNDS = { min: [-8, 0, -8], max: [8, 8, 8] };
export function heightAt() { return 0; }

export async function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xc9d6e0, 0.02);
  scene.add(new THREE.HemisphereLight(0xbcd6ef, 0x4a4130, 0.9));
  const paint = await makeText('MARKET', { size: 0.8, bold: true });
  paint.position.set(0, 1.4, 0);
  scene.add(paint);
  const neon = await makeText('OPEN', { size: 0.4, emissive: 0xff3a4e });
  neon.position.set(0, 0.6, 0.3);
  scene.add(neon);
  const flatpaint = await makeText('EST 1897', { size: 0.2, variation: 0 });
  flatpaint.position.set(0, 0.2, 0.6);
  scene.add(flatpaint);
  return {
    scene,
    cameras: [{ name: 'a', position: [3, 2, 5], lookAt: [0, 1.2, 0], fov: 45 }],
    update() {},
  };
}
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    """One node launch of _PROBE, shared by every test that reads it."""
    return measure(_PROBE, ("signage.js",))


def test_default_font_path_yields_real_glyph_geometry(probe):
    """End to end under node, in a bare temp dir with no node_modules and
    no NODE_PATH — the case that threw MODULE_NOT_FOUND before the port.
    'OPEN' at size 1 must return substantial extruded geometry: wider than
    tall, cap height near size, real depth."""
    m = probe
    assert m["w"] > m["h"] > 0, m
    assert 0.5 < m["h"] <= 1.1, f"cap height off: {m['h']}"
    assert 0.1 < m["d"] < 0.3, f"extrusion depth off: {m['d']}"
    assert m["verts"] > 500, f"too few vertices for glyphs: {m['verts']}"


def test_mesh_is_centered_with_baseline_at_zero(probe):
    """Placement contract: centered on X and Z, baseline at y = 0, and
    userData.size carries the measured bbox — sign boards and asset
    seating are sized from these numbers."""
    m = probe
    assert abs(m["cx"]) < 1e-6 and abs(m["cz"]) < 1e-6, m
    assert abs(m["minY"]) < 0.05, f"baseline drifted: {m['minY']}"
    assert abs(m["userW"] - m["w"]) < 1e-6, "userData.size is stale"
    assert m["name"] == "SignText"


def test_same_input_rebuilds_identical_and_options_are_alive(probe):
    """Determinism (fix rounds re-render untouched assets identically),
    now including the baked paint variance, and each documented option
    must actually change the output."""
    m = probe
    assert m["identical"], "same text+opts rebuilt differently"
    assert m["wideW"] > m["userW"] * 1.5, "longer string not wider"
    assert abs(m["boldW"] - m["userW"]) > 0.01, "bold face is dead"
    assert m["verts"] > m["flatVerts"], "bevel:false changed nothing"
    assert m["emissiveHex"] == 0xFF2244 and m["emissiveI"] == 1.6, m
    assert m["plainEmissive"] == 0x000000, "default must not glow"
    assert m["cached"], "font loaded twice — cache broken"


def test_paint_variance_gives_the_face_hue_and_the_walls_form(probe):
    """Law 2.  Every glyph carries a vertex colour: the extrusion walls,
    which see far less sky than the face, sit well below the caps, and the
    warm/cool swing is real rather than a uniform grey ramp."""
    m = probe
    assert m["hasColorAttr"] and m["vertexColors"] is True, m
    assert m["capN"] > 100 and m["wallN"] > 100, m
    assert m["wallMean"] < 0.85 * m["capMean"], (
        f"walls not shaded: cap {m['capMean']:.3f} wall {m['wallMean']:.3f}")
    assert m["wallMean"] > 0.45 * m["capMean"], (
        f"walls crushed to soot: {m['wallMean']:.3f}")
    assert m["maxC"] - m["minC"] > 0.15, f"variance is flat: {m}"
    assert m["hueMean"] > 0.004 and m["hueMax"] > 0.02, (
        f"no warm/cool spread: mean {m['hueMean']:.4f} max {m['hueMax']:.4f}")


def test_the_vertex_colour_can_only_darken_the_stated_albedo(probe):
    """Law 3.  `color` is the ceiling, and the default paint sits inside
    the 0.02-0.8 linear band (0xf2f2f2 was 0.887 — a white slab under ACES
    at exposure 1.0 with no post chain)."""
    m = probe
    assert m["colorHex"] == 0xE7E3DA, hex(m["colorHex"])
    assert min(m["colorLinear"]) >= 0.02, m["colorLinear"]
    assert max(m["colorLinear"]) <= 0.8 + 1e-6, m["colorLinear"]
    assert m["maxC"] <= 1.0 + 1e-6, f"vertex colour brightens albedo: {m['maxC']}"


def test_variation_zero_is_a_clean_opt_out(probe):
    """`variation: 0` drops the attribute AND the material flag — a caller
    who wants flat studio lettering pays nothing for the variance."""
    m = probe
    assert m["plainHasColorAttr"] is False, "attribute written anyway"
    assert m["plainVertexColors"] is False, "material still reads colours"


def test_letters_cast_shadows_and_are_never_re_seated(probe):
    """Laws 4-5 of placement.  Shadows are on by default (the glyph shadow
    is what says "extruded" — see fx/out/signage/ground_before vs
    ground_after, where the same letters go from pasted-on to standing on
    the dirt) and opt-out-able; and the host's own settleScene must leave
    a mounted line alone while it seats an unmarked crate beside it."""
    m = probe
    assert m["cast"] and m["recv"], m
    assert not m["bareCast"] and not m["bareRecv"], "shadow:false ignored"
    assert m["placement"] == "free", m["placement"]
    assert m["signY"] == 3, f"settle dropped the lettering to {m['signY']}"
    assert m["crateY"] < 1.0, f"control crate was not seated: {m['crateY']}"
    assert m["settledCount"] == 1, m["settledCount"]


def test_an_emissive_sign_carries_its_own_practical(probe):
    """Law 4.  Emissive text lights what it is bolted to; plain text adds
    no light at all, and `light: false` is the opt-out for a facade
    carrying dozens of signs."""
    m = probe
    assert m["neonChildren"] == 1 and m["lightHex"] == 0xFF2244, m
    assert 0.05 < m["lightCd"] < 6.0, f"practical out of range: {m['lightCd']}"
    assert m["lightZ"] > 0, "practical sits behind the glyph faces"
    assert m["lightShadow"] is False, "a sign practical must not cast shadows"
    assert m["plainHasLight"] is False, "non-emissive text lit something"
    assert m["darkHasLight"] is False and m["darkChildren"] == 0, m


def test_every_signage_material_compiles_on_the_real_renderer():
    """Paint (vertex-coloured standard), neon (emissive + a point light)
    and the flat opt-out are three different programs; all three must
    build on the headless GPU."""
    code, out = compile_scene(_SCENE, ("signage.js",))
    assert code == 0, out
