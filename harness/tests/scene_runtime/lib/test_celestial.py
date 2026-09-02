"""celestial.js: heat shimmer, stars, the Milky Way and the aurora.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_celestial_lib.py).  Their renderer-contract assertions are
dropped and their fog marker is retargeted — our `makeShaderMaterial` writes
`3dcv: no-fog`, the token runtime_js/lib/glsl_audit.mjs reads, and the old
`astra3d:` spelling is still accepted on read.  Their physical claims (the
magnitude distribution, the foot-bright curtain, a shimmer that displaces
rather than paints, one seed one sky) are kept as they stood.

THE PORT'S OWN LAWS, and the three regressions this file exists to stop —
each one was a frame rendered on our host, looked at, and measured:

1. A DISPLAY HAS TO CROSS THE SKY THE CAMERA IS POINTED AT.  The curtain
   azimuths were `n` independent draws, and at seed 11 the five of them left
   a hundred-degree hole that both showcase cameras looked straight through:
   the aurora was in the scene, cost its draw call, and did not appear in a
   single pixel.  Simulated over 180 seed/activity pairs, independent draws
   left a gap wider than one camera's own field 64% of the time and up to
   270 degrees wide.  One curtain per equal slice, jittered inside it, takes
   that to 37% and 139 degrees — so the floor asserted here is a bound the
   old code could not have met.

2. ADDED LIGHT MUST STOP SHORT OF WHITE.  ACES desaturates as it rolls off,
   so a curtain allowed to reach alpha 1 arrives WHITE rather than green:
   measured at 2.1% of the frame clipped past 0.97 with the hue gone.  The
   saturating curve now approaches `uPeak`, not 1.

3. THE NIGHT BUILD IS THE DEFAULT.  `ambient` defaulted to 0.85 — the
   daylight-safe blend — and a star field belongs to exactly one frame, a
   dark one.  At the default, `makeStars()` on a night scene put the whole
   sky inside a single 8-bit bucket (modal_frac 0.95, sky luminance flat at
   0.19): the catalog promised stars and the frame had none.  The bright
   build is still one argument away, and is still what a pale frame gets.
"""

from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "noise.js", "materials.js", "celestial.js")


def test_a_real_sky_is_mostly_faint_stars():
    """The count of stars brighter than magnitude m grows as 10^0.6m, so a
    sky is overwhelmingly faint with a handful of bright ones.  A field of
    evenly bright points reads as confetti, and it is what a uniform random
    brightness gives — so the claim is the DISTRIBUTION, not the count."""
    out = measure("""
import { makeStars } from './lib/celestial.js';
const g = makeStars({ count: 3000, seed: 5 });
let attr = null;
g.traverse((o) => {
  if (!o.isMesh || attr) return;
  // Per-star data rides aStar; .x is the brightness the size and the
  // alpha are both built from.
  const a = o.geometry.getAttribute('aStar');
  if (a) attr = a;
});
const v = [];
if (attr) for (let i = 0; i < attr.count; i++) v.push(attr.getX(i));
v.sort((a, b) => a - b);
const q = (p) => v[Math.min(v.length - 1, Math.floor(p * v.length))];
console.log(JSON.stringify({
  n: v.length,
  min: v[0], q25: q(0.25), median: q(0.5), q75: q(0.75), max: v[v.length - 1],
  mean: v.reduce((s, x) => s + x, 0) / (v.length || 1),
}));
""", _LIBS)
    assert out["n"] > 100, "no per-star brightness attribute found"
    span = out["max"] - out["min"]
    assert span > 0, "every star is the same brightness"
    # Skewed faint: the median sits well below the midpoint of the range.
    mid = out["min"] + span / 2
    assert out["median"] < mid, (
        f"the field is not skewed faint (median {out['median']:.4f} vs "
        f"midpoint {mid:.4f})")
    assert out["mean"] < mid


def test_nothing_in_the_sky_writes_depth_or_casts_a_shadow():
    """A star field at the far plane that writes depth clips whatever is
    drawn after it, and a firmament that casts a shadow puts the Milky Way
    on the ground."""
    out = measure("""
import { makeStars, makeAurora } from './lib/celestial.js';
const check = (g) => {
  const bad = [];
  g.traverse((o) => {
    if (!o.isMesh) return;
    for (const m of [].concat(o.material || [])) {
      if (!m) continue;
      if (m.depthWrite) bad.push(o.name + ':depthWrite');
      if (o.castShadow) bad.push(o.name + ':castShadow');
    }
  });
  return bad;
};
console.log(JSON.stringify({
  stars: check(makeStars({ count: 200, seed: 1 })),
  aurora: check(makeAurora({ seed: 2 })),
}));
""", _LIBS)
    assert not out["stars"], out["stars"]
    assert not out["aurora"], out["aurora"]


def test_the_shimmer_displaces_what_is_behind_it():
    """A shimmer you can SEE as a shape is wrong — it has to move what is
    behind it.  This pins that it displaces sample coordinates into a grab
    of the frame rather than painting a haze of its own."""
    out = measure("""
import { makeHeatShimmer } from './lib/celestial.js';
const g = makeHeatShimmer({ seed: 3 });
let fs = '';
let hasGrab = false;
g.traverse((o) => {
  if (o.isMesh && typeof o.onBeforeRender === 'function') hasGrab = true;
  for (const m of [].concat(o.material || [])) {
    if (m && m.fragmentShader) fs += m.fragmentShader;
  }
});
console.log(JSON.stringify({
  found: fs.length > 0,
  hasGrab,
  // A displaced sample: the coordinate is offset before it is read.
  displaces: /texture2D\\s*\\(|texture\\s*\\(/.test(fs)
      && /\\+\\s*(vec2|off|wob|push|sh\\w*)/i.test(fs),
  // And it must not simply add its own colour on top.
  notJustAdditive: !/gl_FragColor\\s*=\\s*vec4\\(\\s*uColor/.test(fs),
  // Resampling pixels that were already tone mapped and re-mapping them
  // is the one way this shader could change a colour instead of moving
  // it, so the card has to opt out.
  toneMapped: (() => { let t = null;
    g.traverse((o) => { for (const m of [].concat(o.material || []))
      if (m && m.fragmentShader) t = m.toneMapped; }); return t; })(),
}));
""", _LIBS)
    assert out["found"], "the shimmer builds no shader"
    assert out["displaces"], (
        "a shimmer must move what is behind it, not draw itself")
    assert out["notJustAdditive"]
    assert out["hasGrab"], "nothing grabs the frame the shimmer resamples"
    assert out["toneMapped"] is False, (
        "the resampled pixels are already tone mapped; mapping them twice "
        "changes a colour instead of moving it")


def test_the_aurora_is_brightest_at_its_foot():
    """A curtain is fed from below: it is dense at the bottom edge and
    dissolves upward.  Uniform brightness is a painted band.  The profile is
    baked per vertex, so this reads the DATA rather than the GLSL."""
    out = measure("""
import { makeAurora } from './lib/celestial.js';
const g = makeAurora({ seed: 4 });
let mesh = null;
g.traverse((o) => { if (o.isMesh && !mesh) mesh = o; });
const glow = mesh.geometry.getAttribute('aGlow');
const uv = mesh.geometry.getAttribute('uv');
// v is height up the curtain, 0 at the foot.  Bucket the baked glow by it.
const sum = [0, 0, 0, 0], cnt = [0, 0, 0, 0];
for (let i = 0; i < glow.count; i++) {
  const b = Math.min(3, Math.floor(uv.getY(i) * 4));
  sum[b] += glow.getX(i); cnt[b]++;
}
console.log(JSON.stringify({
  bands: sum.map((s, i) => (cnt[i] ? s / cnt[i] : 0)),
}));
""", _LIBS)
    b = out["bands"]
    assert all(c > 0 for c in b[:1]), "no baked glow profile on the curtain"
    assert b[0] > b[1] > b[2], (
        f"the aurora does not fade upward from its foot: {b}")
    assert b[3] < b[0] * 0.2, f"the top of the curtain never dissolves: {b}"


def test_a_display_crosses_the_sky_wherever_the_camera_looks():
    """Port law 1.  A curtain per equal slice of the compass, jittered
    inside it — so no seed can leave a hole wide enough to swallow a whole
    camera.  Asserted over the seeds and activities an agent will actually
    pass, on the built geometry rather than on the generator."""
    out = measure("""
import { makeAurora } from './lib/celestial.js';
const rows = [];
for (let seed = 1; seed <= 12; seed++) {
  for (const activity of [0.2, 0.5, 0.9]) {
    const g = makeAurora({ seed, activity });
    let mesh = null;
    g.traverse((o) => { if (o.isMesh && !mesh) mesh = o; });
    const p = mesh.geometry.getAttribute('position');
    const B = 180, bins = new Array(B).fill(0);
    for (let i = 0; i < p.count; i++) {
      let a = Math.atan2(p.getZ(i), p.getX(i));
      if (a < 0) a += Math.PI * 2;
      bins[Math.min(B - 1, Math.floor(a / (Math.PI * 2) * B))] = 1;
    }
    // Longest empty run, wrapping: the widest sky with no curtain in it.
    let best = 0, run = 0;
    for (let k = 0; k < B * 2; k++) {
      if (bins[k % B]) run = 0;
      else { run++; if (run > best) best = run; }
    }
    rows.push([seed, activity, Math.min(360, best * 2),
               bins.reduce((s, x) => s + x, 0) / B]);
  }
}
console.log(JSON.stringify({ rows }));
""", _LIBS)
    gaps = [r[2] for r in out["rows"]]
    covers = [r[3] for r in out["rows"]]
    worst = max(out["rows"], key=lambda r: r[2])
    # Independent draws reached 270 degrees and a median of 103; this bound
    # is one the old generator could not have met.
    assert worst[2] <= 150, (
        f"seed {worst[0]} at activity {worst[1]} leaves a {worst[2]} degree "
        "hole in the sky — a camera can look straight through the display")
    assert sorted(gaps)[len(gaps) // 2] <= 100, (
        f"median gap {sorted(gaps)[len(gaps) // 2]} degrees")
    assert min(covers) >= 0.25, (
        f"a display covers only {min(covers):.2f} of the compass")


def test_the_curtain_alpha_stops_short_of_white():
    """Port law 2.  The saturating curve approaches uPeak, not 1: an aurora
    that reaches full alpha comes back out of the tone map white, and white
    is the one colour an aurora is not."""
    out = measure("""
import { makeAurora } from './lib/celestial.js';
const read = (opts) => {
  const g = makeAurora(opts);
  let u = null;
  g.traverse((o) => {
    for (const m of [].concat(o.material || [])) {
      if (m && m.uniforms && m.uniforms.uPeak) u = m.uniforms.uPeak.value;
    }
  });
  return u;
};
console.log(JSON.stringify({ night: read({ seed: 1 }),
                             day: read({ seed: 1, ambient: 0.9 }) }));
""", _LIBS)
    assert out["night"] is not None, "the curtain declares no alpha ceiling"
    assert 0 < out["night"] <= 0.7, (
        f"the night curtain may reach alpha {out['night']}")
    assert out["day"] < out["night"], (
        "the daylight build must be the quieter of the two")


def test_the_night_build_is_the_default():
    """Port law 3.  Both sky fields default to the frame they belong in — a
    dark one — and both still switch to the blend that cannot clip when the
    caller says the sky behind is bright."""
    out = measure("""
import * as THREE from 'three';
import { makeStars, makeAurora } from './lib/celestial.js';
const blends = (g) => {
  const out = [];
  g.traverse((o) => {
    for (const m of [].concat(o.material || [])) {
      if (m && m.fragmentShader) out.push([m.name,
          m.blending === THREE.AdditiveBlending ? 'add' : 'normal']);
    }
  });
  return out.sort();
};
console.log(JSON.stringify({
  starsDefault: blends(makeStars({ count: 60, seed: 1 })),
  starsBright: blends(makeStars({ count: 60, seed: 1, ambient: 0.9 })),
  auroraDefault: blends(makeAurora({ seed: 1 })),
  auroraBright: blends(makeAurora({ seed: 1, ambient: 0.9 })),
}));
""", _LIBS)
    for key in ("starsDefault", "auroraDefault"):
        assert out[key], f"{key} built no sky material"
        assert all(mode == "add" for _, mode in out[key]), (
            f"{key} is not the added-light build: {out[key]}")
    for key in ("starsBright", "auroraBright"):
        assert all(mode == "normal" for _, mode in out[key]), (
            f"{key} still ADDS to a frame the caller called bright: "
            f"{out[key]}")


def test_the_sky_declares_its_own_fog_opt_out():
    """The firmament sits outside the weather.  Our GLSL audit reads a
    source marker, and the option that writes it is the one its own warning
    tells you to pass — so passing it has to produce the marker, or the
    advice does nothing.  `3dcv:` is this harness's spelling; the
    reference's `astra3d:` is still accepted on read."""
    out = measure("""
import { makeStars, makeAurora } from './lib/celestial.js';
const marks = [];
for (const g of [makeStars({ count: 50, seed: 1 }),
                 makeAurora({ seed: 1 })]) {
  g.traverse((o) => {
    for (const m of [].concat(o.material || [])) {
      if (m && m.fragmentShader) {
        marks.push([m.name, m.fog === false,
                    /(3dcv|astra3d):\\s*no-fog/.test(m.fragmentShader)]);
      }
    }
  });
}
console.log(JSON.stringify({ marks }));
""", _LIBS)
    assert out["marks"], "no sky materials found"
    for name, flag, marker in out["marks"]:
        assert flag, f"{name} does not opt out of fog"
        assert marker, f"{name} opts out but writes no marker for the checker"


def test_one_seed_lays_the_same_sky_twice():
    out = measure("""
import { makeStars } from './lib/celestial.js';
const shape = (seed) => {
  const g = makeStars({ count: 200, seed });
  const v = [];
  g.traverse((o) => {
    if (!o.isMesh) return;
    // position is zeroed for the billboard; the sky lives on aDir.
    const d = o.geometry.getAttribute('aDir');
    const b = o.geometry.getAttribute('aStar');
    let s = 0;
    if (d) for (let i = 0; i < d.count; i += 5) s += d.getX(i) + d.getZ(i);
    if (b) for (let i = 0; i < b.count; i += 5) s += b.getX(i);
    v.push(o.name, s.toFixed(5));
  });
  return v.join('|');
};
console.log(JSON.stringify({
  same: shape(6) === shape(6),
  differs: shape(6) !== shape(11),
}));
""", _LIBS)
    assert out["same"] and out["differs"]


def test_all_three_compile_on_the_headless_gpu():
    """rain.js shipped fog chunks that could not compile for months because
    nothing ever built them.  Every shader in this file — including the
    shimmer's, which only exists once a card is made — goes through a real
    GLSL compile here."""
    code, out = compile_scene("""
import * as THREE from 'three';
import { makeHeatShimmer, makeStars, makeAurora } from './lib/celestial.js';

export const BOUNDS = { min: [-20, 0, -20], max: [20, 20, 20] };
export function heightAt() { return 0; }

export async function createScene() {
  const scene = new THREE.Scene();
  // Both builds of both fields, and the shimmer: five programs.
  scene.add(makeHeatShimmer({ seed: 1 }));
  scene.add(makeStars({ count: 300, seed: 2 }));
  scene.add(makeStars({ count: 300, seed: 4, ambient: 0.9 }));
  scene.add(makeAurora({ seed: 3 }));
  scene.add(makeAurora({ seed: 5, ambient: 0.9 }));
  const cameras = [
    { name: 'sky', position: [0, 2, 12], lookAt: [0, 8, 0], fov: 55 },
  ];
  return { scene, cameras, update() {} };
}
""", _LIBS)
    assert code == 0, out
