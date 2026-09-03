"""A river is not a plane with a scrolling texture.

Scroll a UV on a curved ribbon and the whole sheet slides one way, so
round a bend the water stops following its own course — the one defect
that reads instantly as "moving texture" instead of "river".  ``river.js``
carries the centreline tangent per vertex and advects its noise along
THAT, so the geometry half of these tests holds the claim (the flow field
is tangent to the course, and it turns where the course turns) rather
than any pixel.

Ported 2026-09-01 from the scene_multifile_graphics reference test.  The
colour half is OURS: the surface is unlit, so every constant in it is a
decision, and the reference's were three daylight constants baked into a
factory.  Measured on this harness's night rig with those defaults, the
channel came back at luminance 0.40/0.54 over a frame whose mean was
0.21/0.31 — a strip of white plastic lying in the dark.  It now reads the
scene's own key light, hemisphere fill and fog at the first render, so
the tests below pin THAT as hard as they pin the flow field.
"""

from __future__ import annotations

from tests.scene_runtime.lib._probe import compile_scene, measure  # noqa: F401

_LIBS = ("shader.js", "river.js")

# One S-bend, so a flow field that did not turn cannot pass by accident.
_POINTS = "[[-14,0,-12],[-5,0,-6],[2,0,2],[-1,0,10],[4,0,16]]"

# Rows are arc-length uniform, so a row's centre IS the centreline
# there; this hands every probe the same frame to measure against.
_FRAME = """
const geom = (g) => g.getObjectByName('Channel').geometry;
function frame(g) {
  const a = geom(g).attributes;
  const P = a.position.array, UV = a.uv.array;
  let cols = 0;
  while (cols < a.position.count && UV[cols * 2 + 1] === 0) cols++;
  const rows = a.position.count / cols - 1;
  const centre = [];
  for (let j = 0; j <= rows; j++) {
    let x = 0, y = 0, z = 0;
    for (let i = 0; i < cols; i++) {
      const k = (j * cols + i) * 3;
      x += P[k]; y += P[k + 1]; z += P[k + 2];
    }
    centre.push([x / cols, y / cols, z / cols]);
  }
  return { a, cols, rows, centre };
}
"""

# A scene the way `sunRig` builds one: a key directional light, a
# hemisphere fill and fog.  `adoptSceneLight` runs off `onBeforeRender`,
# which a probe can call directly — no GPU needed to prove what it read.
_LIT_SCENE = """
function litScene(THREE, opts) {
  const o = opts || {};
  const scene = new THREE.Scene();
  const key = new THREE.DirectionalLight(o.sunHex || 0xfff0d8,
      o.sunI === undefined ? 5.4 : o.sunI);
  // Direction is position -> target, so a light at +y+x shines DOWN and
  // the adopted uSun must point back up at it.
  key.position.set(30, 40, -10);
  scene.add(key);
  const hemi = new THREE.HemisphereLight(o.skyHex || 0x9db8e8, 0x8a7f6a,
      o.fillI === undefined ? 1.4 : o.fillI);
  scene.add(hemi);
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.0035);
  return scene;
}
const adopt = (g, scene) => {
  const m = g.getObjectByName('Channel');
  m.onBeforeRender(null, scene);
  return m.material.uniforms;
};
const hex = (c) => [+c.r.toFixed(4), +c.g.toFixed(4), +c.b.toFixed(4)];
"""


def test_the_ribbon_follows_the_given_points():
    """The course is the contract: every waypoint the caller gave must
    have channel under it, and the ribbon rests on the water surface
    rather than sinking into it or floating over it."""
    out = measure(_FRAME + """
import { makeRiver } from './lib/river.js';
const pts = __POINTS__;
const g = makeRiver({ points: pts, width: 6, depth: 1.2, seed: 3 });
const { a, centre } = frame(g);
const miss = pts.map((p) => Math.min(...centre.map(
    (c) => Math.hypot(c[0] - p[0], c[2] - p[2]))));
let run = 0;
for (let j = 1; j < centre.length; j++) {
  run += Math.hypot(centre[j][0] - centre[j - 1][0],
                    centre[j][2] - centre[j - 1][2]);
}
const P = a.position.array;
let minY = 1e9, maxY = -1e9;
for (let i = 1; i < P.length; i += 3) {
  minY = Math.min(minY, P[i]); maxY = Math.max(maxY, P[i]);
}
let poly = 0;
for (let i = 1; i < pts.length; i++) {
  poly += Math.hypot(pts[i][0] - pts[i - 1][0],
                     pts[i][2] - pts[i - 1][2]);
}
console.log(JSON.stringify({
  worstMiss: +Math.max(...miss).toFixed(3),
  run: +run.toFixed(2), poly: +poly.toFixed(2),
  straight: +Math.hypot(pts[4][0] - pts[0][0],
                        pts[4][2] - pts[0][2]).toFixed(2),
  minY: +minY.toFixed(3), maxY: +maxY.toFixed(3),
  name: g.name, hasTick: typeof g.userData.tick === 'function',
}));
""".replace("__POINTS__", _POINTS), _LIBS)
    # Sampled every ~0.55 m, so the nearest row centre to a waypoint is
    # inside half that; a ribbon that cut the corner would miss by
    # metres.
    assert out["worstMiss"] < 0.35, out
    # It runs the polyline's own length — neither shortcutting the
    # corners nor wandering off between them.
    assert out["poly"] <= out["run"] <= out["poly"] * 1.05, out
    assert out["run"] > out["straight"] * 1.1, out
    # Rests ON the surface: a hair above the water plane it joins, not
    # a slab with thickness.
    assert 0 < out["minY"] <= 0.02 and out["maxY"] - out["minY"] < 0.01
    assert out["name"] == "River" and out["hasTick"]


def test_the_flow_direction_is_tangent_to_the_centreline():
    """The whole claim. `aFlow` is the world flow velocity, and its
    direction must be the course's own tangent at that vertex — that is
    what makes the advected pattern go round a bend instead of sliding
    sideways as one sheet. A constant direction would pass a straight
    reach and fail here, so the turn is measured too."""
    out = measure(_FRAME + """
import { makeRiver } from './lib/river.js';
const g = makeRiver({ points: __POINTS__, width: 6, flow: 1.5, seed: 3 });
const { a, cols, rows, centre } = frame(g);
const F = a.aFlow.array;
const ang = (v) => Math.atan2(v[1], v[0]) * 180 / Math.PI;
let worst = 1, slow = 1e9, fast = -1e9;
const flowH = [], courseH = [];
for (let j = 1; j < rows; j++) {
  // Central difference: second-order, so a real bend does not read as
  // a tangency error the way a forward chord would.
  const d = [centre[j + 1][0] - centre[j - 1][0],
             centre[j + 1][2] - centre[j - 1][2]];
  const dl = Math.hypot(d[0], d[1]);
  for (let i = 0; i < cols; i++) {
    const k = (j * cols + i) * 3;
    const f = [F[k], F[k + 2]];
    const fl = Math.hypot(f[0], f[1]);
    worst = Math.min(worst, (f[0] * d[0] + f[1] * d[1]) / (fl * dl));
    slow = Math.min(slow, fl); fast = Math.max(fast, fl);
    if (i === 0) { flowH.push(ang(f)); courseH.push(ang(d)); }
  }
}
// How far the heading swings over the whole course — an S returns to
// where it started, so end-to-end would say a river never turns.
const swing = (h) => Math.max(...h) - Math.min(...h);
console.log(JSON.stringify({
  worstDot: +worst.toFixed(5),
  flowSwing: +swing(flowH).toFixed(1),
  courseSwing: +swing(courseH).toFixed(1),
  slow: +slow.toFixed(3), fast: +fast.toFixed(3),
  y: Math.max(...[...F].filter((_, i) => i % 3 === 1).map(Math.abs)),
}));
""".replace("__POINTS__", _POINTS), _LIBS)
    assert out["worstDot"] > 0.999, out
    # It TURNS, and it turns by exactly what the course turns by.
    assert out["flowSwing"] > 40, out
    assert abs(out["flowSwing"] - out["courseSwing"]) < 3, out
    # Magnitude is the surface speed, horizontal: the flow map advects
    # in metres per second, so a wrong length is a wrong current.
    assert out["slow"] > 1.2 and out["fast"] < 1.9, out
    assert out["y"] == 0, out


def test_it_runs_thin_over_the_bank_and_over_a_rock():
    """Depth is what tells the eye where the channel is, and only the
    CPU can find it: GLSL_UTIL's value noise is not the field the
    terrain was built from, so `heightAt` is measured here and handed
    over as an attribute or the pale shallows never line up with the
    ground the water actually runs over."""
    out = measure(_FRAME + """
import { makeRiver } from './lib/river.js';
// Flat bed at -1.6 with one boulder standing 1.3 m proud of it.
const heightAt = (x, z) =>
    Math.max(0, 1.3 - Math.hypot(x, z) * 1.2) - 1.6;
const g = makeRiver({ points: [[-12, 0, 0], [12, 0, 0]], width: 6,
                      depth: 1.4, flow: 1.0, heightAt, seed: 2 });
const { a, cols, rows, centre } = frame(g);
const C = a.aChan.array;
const at = (j, i, c) => C[(j * cols + i) * 4 + c];
let bank = 0, mid = 0, rockDeep = 9, rockBrk = 0, calmBrk = 0;
for (let j = 0; j <= rows; j++) {
  bank = Math.max(bank, at(j, 0, 1), at(j, cols - 1, 1));
  const c = at(j, (cols - 1) / 2, 1);
  const onRock = Math.abs(centre[j][0]) < 0.6;
  if (onRock) {
    rockDeep = Math.min(rockDeep, c);
    rockBrk = Math.max(rockBrk, at(j, (cols - 1) / 2, 2));
  } else {
    mid = Math.max(mid, c);
    // Upstream only: the wake downstream of the rock keeps foaming.
    if (centre[j][0] < -3) calmBrk = Math.max(calmBrk, at(j, 7, 2));
  }
}
console.log(JSON.stringify({
  bank: +bank.toFixed(3), mid: +mid.toFixed(3),
  rockDeep: +rockDeep.toFixed(3), rockBrk: +rockBrk.toFixed(3),
  calmBrk: +calmBrk.toFixed(3),
}));
""", _LIBS)
    assert out["bank"] == 0 and 1.3 < out["mid"] <= 1.4, out
    # The water over the boulder is thin, and it breaks there.
    assert out["rockDeep"] < 0.25, out
    assert out["rockBrk"] > 0.5, out
    # ...and nowhere else: foam everywhere is foam nowhere.
    assert out["calmBrk"] < 0.05, out


def test_a_narrows_runs_faster_and_breaks():
    """Flux, not decoration: the same water through a smaller gap has
    to run faster, and that is what makes a narrows both race and go
    white — one number driving both, so they cannot disagree."""
    out = measure(_FRAME + """
import { makeRiver } from './lib/river.js';
const pinch = (u) => 6 * (1 - 0.45 * Math.exp(-Math.pow(
    (u - 0.5) / 0.10, 2)));
const g = makeRiver({ points: [[-12, 0, 0], [12, 0, 0]], width: pinch,
                      depth: 1.2, flow: 1.0, seed: 6 });
const { a, cols, rows, centre } = frame(g);
const F = a.aFlow.array, C = a.aChan.array;
let gap = 1e9, wide = 0, gapV = 0, wideV = 9, gapBrk = 0, wideBrk = 0;
for (let j = 0; j <= rows; j++) {
  const k = (j * cols) * 3;
  const v = Math.hypot(F[k], F[k + 2]);
  const w = Math.hypot(
      a.position.array[k] - a.position.array[k + (cols - 1) * 3],
      a.position.array[k + 2] - a.position.array[k + (cols - 1) * 3 + 2]);
  const brk = C[(j * cols + (cols - 1) / 2) * 4 + 2];
  if (Math.abs(centre[j][0]) < 1.0) {
    gap = Math.min(gap, w); gapV = Math.max(gapV, v);
    gapBrk = Math.max(gapBrk, brk);
  } else if (centre[j][0] < -6) {
    wide = Math.max(wide, w); wideV = Math.min(wideV, v);
    wideBrk = Math.max(wideBrk, brk);
  }
}
console.log(JSON.stringify({
  gap: +gap.toFixed(2), wide: +wide.toFixed(2),
  gapV: +gapV.toFixed(3), wideV: +wideV.toFixed(3),
  gapBrk: +gapBrk.toFixed(3), wideBrk: +wideBrk.toFixed(3),
}));
""", _LIBS)
    assert out["gap"] < out["wide"] * 0.7, out
    assert out["gapV"] > out["wideV"] * 1.4, out
    assert out["gapBrk"] > 0.5 and out["wideBrk"] < 0.05, out


def test_two_rivers_with_one_seed_are_identical():
    """Determinism is the contract for every shipped factory: a re-run
    that re-wanders the banks is a re-run nobody can compare against."""
    out = measure("""
import { makeRiver } from './lib/river.js';
const dump = (s) => {
  const g = makeRiver({ points: [[-8, 0, 0], [0, 0, 3], [8, 0, 0]],
                        width: 5, seed: s });
  const a = g.getObjectByName('Channel').geometry.attributes;
  return JSON.stringify([...a.position.array, ...a.aChan.array]);
};
const a = dump(5), b = dump(5), c = dump(9);
console.log(JSON.stringify({ same: a === b, differs: a !== c }));
""", _LIBS)
    assert out["same"] and out["differs"]


def test_tick_advances_the_material():
    """An un-advanced uTime is a frozen river, and the flow map resets
    every period — so the clock is not optional dressing."""
    out = measure("""
import { makeRiver } from './lib/river.js';
const g = makeRiver({ width: 5 });
const before = [], after = [];
g.traverse((o) => {
  for (const m of [].concat(o.material || [])) {
    if (m && m.uniforms && m.uniforms.uTime) {
      before.push(m.uniforms.uTime.value);
    }
  }
});
g.userData.tick(2.5);
g.traverse((o) => {
  for (const m of [].concat(o.material || [])) {
    if (m && m.uniforms && m.uniforms.uTime) after.push(m.uniforms.uTime.value);
  }
});
console.log(JSON.stringify({ before, after }));
""", _LIBS)
    assert out["before"] == [0] and out["after"] == [2.5]


def test_the_stated_depth_actually_changes_how_deep_the_water_looks():
    """The depth ramp must be absolute, not relative to the river's own
    depth. The geometry writes `aChan.y = depth * (1 - q*q)`, so a ramp
    normalised by that same `depth` divides it straight back out and
    every river — a 0.25 m brook and a 3 m channel alike — saturates to
    the deep colour mid-stream behind a constant-width shallow rim. The
    metre is the thing that decides how much light comes back up."""
    out = measure("""
import { makeRiver } from './lib/river.js';
const mid = (r) => {
  const g = r.getObjectByName('Channel').geometry;
  const c = g.getAttribute('aChan');
  let best = 0;
  for (let i = 0; i < c.count; i++) best = Math.max(best, c.getY(i));
  return best;
};
const pts = [[-8, 0, -8], [0, 0, 0], [8, 0, 8]];
const brook = makeRiver({ points: pts, width: 3, depth: 0.25, seed: 4 });
const deepR = makeRiver({ points: pts, width: 3, depth: 3.0, seed: 4 });
const src = brook.getObjectByName('Channel').material.fragmentShader;
console.log(JSON.stringify({
  brookDepth: mid(brook),
  riverDepth: mid(deepR),
  dividesItOut: /vChan\\.y\\s*\\/\\s*uDepthM/.test(src),
}));
""", _LIBS)
    assert out["brookDepth"] < 0.3 and out["riverDepth"] > 2.5, out
    assert not out["dividesItOut"], (
        "the shader divides the measured depth by the depth it was built "
        "from, so `depth` cannot reach the eye")


def test_a_river_at_the_depth_people_actually_pass_is_not_pale():
    """The ramp is absolute, which is right, but the metre it was set to
    was picked from plausibility rather than from what callers pass. At
    uOpaqueM 2.4 a river 1 m deep reached dep 0.42 at its deepest point
    and the smoothstep floor kept nearly all of an 18 m channel in the
    SHALLOW colour — a pale sage ribbon bank to bank. A river is 1-2 m
    deep; the scale has to be near that, and the deep end of the ramp
    has to sit inside where a real river ever gets."""
    out = measure("""
import { makeRiver } from './lib/river.js';
const mid = (r) => {
  const g = r.getObjectByName('Channel').geometry;
  const c = g.getAttribute('aChan');
  let best = 0;
  for (let i = 0; i < c.count; i++) best = Math.max(best, c.getY(i));
  return best;
};
const m = makeRiver({ width: 18, depth: 1.0, flow: 0.45, seed: 3 });
const mat = m.getObjectByName('Channel').material;
const edges = /mix\\(uShallow, uDeep, smoothstep\\(([0-9.]+), ([0-9.]+), dep\\)\\)/
    .exec(mat.fragmentShader);
console.log(JSON.stringify({
  deepest: mid(m),
  opaque: mat.uniforms.uOpaqueM.value,
  lo: edges ? parseFloat(edges[1]) : null,
  hi: edges ? parseFloat(edges[2]) : null,
}));
""", _LIBS)
    dep = out["deepest"] / out["opaque"]
    assert dep >= 0.85, (
        f"a 1 m river only reaches dep {dep:.2f}; it will render as its "
        "shallow colour nearly everywhere")
    assert out["hi"] is not None and out["hi"] <= dep, (
        f"the deep end of the ramp ({out['hi']}) sits past where a real "
        f"river ever gets ({dep:.2f})")
    # ...and it is a RAMP: at 0.34 the colour saturated the moment the
    # bed dropped away and the whole channel became one flat value.
    assert out["hi"] - out["lo"] > 0.4, out


def test_the_surface_takes_its_light_from_the_scene():
    """OURS.  The surface is unlit — it writes its own light out — so a
    daylight constant baked into the factory is a river that glows under
    a moon.  Measured on this harness's night rig with the reference
    defaults, the channel came back at luminance 0.40/0.54 over a frame
    whose mean was 0.21/0.31.  It now reads the key light's direction,
    colour and STRENGTH and the sky half of the hemisphere fill at the
    first render, so a night scene darkens the water with it."""
    out = measure(_LIT_SCENE + """
import * as THREE from 'three';
import { makeRiver } from './lib/river.js';
const day = makeRiver({ width: 6, seed: 3 });
const dayU = adopt(day, litScene(THREE, {}));
// The same river in the night rig's own numbers: a 2.2 moon and a 1.0
// fill, both cool.
const night = makeRiver({ width: 6, seed: 3 });
const nightU = adopt(night, litScene(THREE, {
  sunHex: 0xb5c7e8, sunI: 2.2, skyHex: 0x3f5378, fillI: 1.0 }));
const lum = (c) => 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
console.log(JSON.stringify({
  sun: [+dayU.uSun.value.x.toFixed(3), +dayU.uSun.value.y.toFixed(3),
        +dayU.uSun.value.z.toFixed(3)],
  daySunLum: +lum(dayU.uSunCol.value).toFixed(4),
  nightSunLum: +lum(nightU.uSunCol.value).toFixed(4),
  daySkyLum: +lum(dayU.uSky.value).toFixed(4),
  nightSkyLum: +lum(nightU.uSky.value).toFixed(4),
  nightSkyBlue: nightU.uSky.value.b > nightU.uSky.value.r,
  dayAmbLum: +lum(dayU.uAmb.value).toFixed(4),
  nightAmbLum: +lum(nightU.uAmb.value).toFixed(4),
}));
""", _LIBS)
    # The light at (30, 40, -10) shines DOWN; uSun points back up at it.
    d = out["sun"]
    n = (d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5
    assert abs(n - 1) < 1e-3, out
    assert abs(d[0] / n - 0.588) < 0.01 and abs(d[1] / n - 0.784) < 0.01, out
    # Strength, not just hue: the moon rig has to arrive dimmer than the
    # sun rig in every one of the three terms the body colour is built
    # from, or "night" is a colour swap and nothing else.
    assert out["nightSunLum"] < out["daySunLum"] * 0.55, out
    assert out["nightSkyLum"] < out["daySkyLum"] * 0.35, out
    assert out["nightAmbLum"] < out["dayAmbLum"] * 0.35, out
    # ...and the night sky it reflects is BLUE, not grey: dark but lit.
    assert out["nightSkyBlue"], out


def test_a_colour_the_caller_pinned_survives_the_scene():
    """Reading the scene is a DEFAULT, not a seizure: a caller who wrote
    a sunset river down in hex has to get that river in a scene lit any
    way at all, or the option is decoration."""
    out = measure(_LIT_SCENE + """
import * as THREE from 'three';
import { makeRiver } from './lib/river.js';
const dir = new THREE.Vector3(0, 1, 0);
const g = makeRiver({ width: 6, seed: 3, sky: 0xff0000,
                      sunColor: 0x00ff00, ambient: 0x0000ff,
                      sunDir: dir });
const u = adopt(g, litScene(THREE, {}));
// Nothing pinned: only these move.
const g2 = makeRiver({ width: 6, seed: 3 });
const u2 = adopt(g2, litScene(THREE, {}));
console.log(JSON.stringify({
  sky: hex(u.uSky.value), sun: hex(u.uSunCol.value),
  amb: hex(u.uAmb.value),
  dir: [u.uSun.value.x, u.uSun.value.y, u.uSun.value.z],
  freeSkyIsRed: u2.uSky.value.r > u2.uSky.value.b,
}));
""", _LIBS)
    assert out["sky"] == [1, 0, 0] and out["sun"] == [0, 1, 0], out
    assert out["amb"] == [0, 0, 1], out
    assert out["dir"] == [0, 1, 0], out
    # The control: with nothing pinned the scene's own pale-blue sky is
    # what lands, so the assertions above are not passing vacuously.
    assert not out["freeSkyIsRed"], out


_SCENE = """
import * as THREE from 'three';
import { makeRiver } from './lib/river.js';
import { tickShaders } from './lib/shader.js';

export const BOUNDS = { min: [-20, -2, -20], max: [20, 6, 20] };
export function heightAt() { return 0; }

export async function createScene() {
  const scene = new THREE.Scene();
  // Fogged, because the flow map is one custom shader on the raw route
  // and the fog chunk is exactly what a hand-written shader forgets.
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.004);
  const key = new THREE.DirectionalLight(0xfff0d8, 5.4);
  key.position.set(30, 40, -10);
  scene.add(key);
  scene.add(new THREE.HemisphereLight(0x9db8e8, 0x8a7f6a, 1.4));
  const river = makeRiver({
    points: __POINTS__, width: (u) => 5.6 * (1 - 0.4 * Math.exp(
        -Math.pow((u - 0.5) / 0.08, 2))),
    depth: 1.4, flow: 1.4, seed: 4,
    heightAt: (x, z) => Math.max(0, 1.5 - Math.hypot(x - 1.4, z - 2.2))
        - 1.6,
  });
  scene.add(river);
  // A camera, because the host reports a scene with none as not booted
  // and never reaches the compile stage at all.
  return {
    scene,
    cameras: [{ name: 'a', position: [9, 3, 11], lookAt: [0, 0, 0],
                fov: 45 }],
    update(t) { tickShaders(scene, t); river.userData.tick(t); },
  };
}
""".replace("__POINTS__", _POINTS)


def test_the_river_compiles_on_the_real_renderer():
    """The flow map is one custom shader on the raw route, so the depth
    and fog chunks are the difference between a river and nothing at
    all — and only the GPU can say whether it compiled."""
    code, out = compile_scene(_SCENE, _LIBS)
    assert code == 0, out
    assert "every program compiled" in out
    assert "WARN" not in out, out
