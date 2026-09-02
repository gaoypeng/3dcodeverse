"""neon.js: the light a sign MAKES, and the light it throws.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_neon_lib.py).  Their laws are kept as they stood — a tube that
stops following its path draws a shape nobody asked for, a spill that never
reaches zero leaves every wall in the scene washed by every sign, two lanes
riding the same direction is a road where all the traffic goes one way, and
added light is the default because a night city is the dark frame added
light is correct over.  Their GPU compile runs through
`_probe.compile_scene`, which stages the fixture as a workspace
`src/scene.js` — the shape `check_shaders.mjs` boots.

THE PORT'S OWN LAWS — each one a frame rendered on our host at
fx/out/neon/, looked at, and measured:

1. THE ADDITIVE STACK IS BUDGETED FOR A HOST THAT TONE MAPS PER FRAGMENT.
   Their renderer resolved a post chain, so an additive shell wrote linear
   HDR and was tone mapped once at the end.  Ours has no post chain: ACES
   and sRGB run in the fragment tail and additive layers then sum in
   DISPLAY space.  The middle of a tube carries four such layers — the near
   and far walls of the double-sided core, and the halo over them twice —
   and the old gains put a sum of ~2.5 there, where every channel of every
   colour clips.  Both signs in the showcase rendered WHITE: measured, 29.9%
   of the effect's glow pixels had all three channels past 0.90, and a cyan
   tube's brightest pixel was (255, 255, 255).  Budgeted to ~1.4 the strong
   channel still clips (a tube must) and the weak ones sit where the hue
   lives: 10.2% white core, cyan peak (241, 255, 255), and the harness's own
   blown_frac for that view fell 0.0122 -> 0.0006 with mean_lum barely moved
   (0.1445 -> 0.1371).

2. THE GAS COLOUR SURVIVES THE MIDDLE OF THE GLASS.  The whitening ran
   `pow(face, 5.0) * hot`, which is more than half of the tube's width and
   went all the way to white — applied BEFORE a 2.5x stack, so it was the
   larger half of law 1.  A fifth of the way to white, inside the middle
   third, is a hot line in coloured glass instead.

3. A HALO MAY NOT DRAW ITS OWN POLYGON.  The chord weighting is exact for a
   cylinder and the shell is an n-gon, so at the outermost facet the
   interpolated normal is still 180/n degrees off perpendicular and the
   glow ended on a live alpha — a 10-gon at 3.6 radii drew a visible
   straight-edged crease around every letter.  18 sides and a toe on the
   chord put that edge under the shader's own discard.

4. HUE VARIANCE INSIDE EVERY EFFECT.  One tube was one flat colour and five
   vehicles were five copies of one lamp.  The gas now drifts a few degrees
   along each stroke (so a word bent one stroke per letter is four related
   colours), and each vehicle carries its own colour temperature — spent as
   a hue ROTATION, which moves a saturated tail-light, and as a warm/cool
   TILT, which is the only thing that moves a near-white head-light.  In the
   rendered frame the tail lane went from five identical reds to amber,
   salmon and deep red (crop pair fx/c_neon_trails_before.png ->
   fx/c_neon_trails_after.png).

5. EVERY WIDE GRADIENT CARRIES A DITHER.  A halo, a metre-wide wash on
   tarmac and a spill pool on a flat wall are the three softest ramps in
   this library and all three banded in 8-bit output.
"""

from __future__ import annotations

import json

from tests.scene_runtime.lib._probe import compile_scene, measure  # noqa: F401

_LIBS = ("shader.js", "neon.js", "surface_wear.js", "windows.js")

# The scene our check_shaders.mjs boots.  Fog, because a night sign ships
# into a fogged scene and asset mode never defines USE_FOG; a camera,
# because a scene with none is reported as not booted and never reaches
# the compile stage at all; both blend modes, because `ambient` picks
# between two different materials; and the wall material carrying the
# spill CHAINED under two other patches, which is the namespace collision
# that only shows up when three libraries share one material's GLSL.
_SCENE = """
import * as THREE from 'three';
import { makeNeonTube, patchNeonSpill, makeLightTrails }
    from './lib/neon.js';
import { patchMicroBreakup } from './lib/surface_wear.js';
import { patchWindowInteriors } from './lib/windows.js';

export const BOUNDS = { min: [-24, 0, -12], max: [24, 12, 12] };
export function heightAt() { return 0; }

export async function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x05070d, 0.010);
  scene.add(new THREE.HemisphereLight(0x0b1626, 0x04050a, 0.22));
  const night = makeNeonTube({
    path: [[[-1.2, 0, 0], [-1.2, 0.9, 0], [-0.4, 0.9, 0]],
           [[0.3, 0, 0], [0.3, 0.9, 0]]],
    radius: 0.05, color: 0xff2f74, flicker: 0.4, seed: 3 });
  night.position.set(0, 3, -0.4);
  scene.add(night);
  const day = makeNeonTube({ radius: 0.05, ambient: 0.8, seed: 5 });
  day.position.set(6, 3, -0.4);
  scene.add(day);
  const wallMat = new THREE.MeshStandardMaterial({ color: 0x3a2a26 });
  patchMicroBreakup(wallMat, { scale: 0.5, strength: 0.14, seed: 5 });
  patchWindowInteriors(wallMat, { rows: 4, cols: 8, lit: 0.3, seed: 4 });
  patchNeonSpill(wallMat, {
    sources: night.userData.spillSources(4), radius: 6, strength: 1.4,
    flicker: 0.4, seed: 3 });
  const wall = new THREE.Mesh(new THREE.BoxGeometry(14, 8, 0.4), wallMat);
  wall.position.set(0, 4, 0);
  scene.add(wall);
  const trails = makeLightTrails({
    path: [[-20, 0, 6], [0, 0, 7], [20, 0, 6]], count: 5, seed: 7 });
  scene.add(trails);
  const hazy = makeLightTrails({
    path: [[-20, 0, 9], [20, 0, 9]], ambient: 0.9, seed: 8 });
  scene.add(hazy);
  return {
    scene,
    cameras: [{ name: 'a', position: [6, 3, 12], lookAt: [0, 3, 0],
                fov: 45 }],
    update(t) {
      night.userData.tick(t); day.userData.tick(t);
      trails.userData.tick(t); hazy.userData.tick(t);
    },
  };
}
"""

# Pull a scalar GLSL helper out of the SHIPPED source and run it as JS.
# The two dialects agree on everything here except the declarations, so
# the numbers below are the numbers the GPU computes — not a second
# implementation that could drift from the one that renders.  `extra`
# carries helpers this one calls, themselves extracted the same way, so a
# hash is never re-written in JS either.
_GLSL_AS_JS = """
const glslFn = (src, name, args, extra) => {
  const re = new RegExp('float ' + name + '\\\\(([\\\\s\\\\S]*?)\\\\)\\\\s*\\\\{');
  const m = re.exec(src);
  if (!m) throw new Error('no ' + name + '() in the shipped source');
  let depth = 0, end = -1;
  for (let i = src.indexOf('{', m.index); i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) { end = i; break; }
  }
  const body = src.slice(src.indexOf('{', m.index) + 1, end)
      .replace(/\\bfloat\\b/g, 'let');
  const names = Object.keys(extra || {});
  const fn = new Function(...args, ...names, 'clamp', 'max', 'min',
                          'fract', 'step', 'smoothstep', 'mix', 'pow',
                          'exp', 'sin', 'floor', body);
  return (...v) => fn(...v, ...names.map((k) => extra[k]),
      (x, a, b) => Math.min(Math.max(x, a), b), Math.max, Math.min,
      (x) => x - Math.floor(x),
      (e, x) => (x < e ? 0 : 1),
      (a, b, x) => {
        const t = Math.min(Math.max((x - a) / (b - a), 0), 1);
        return t * t * (3 - 2 * t);
      },
      (a, b, t) => a + (b - a) * t,
      Math.pow, Math.exp, Math.sin, Math.floor);
};
"""

# Distance from a point to a polyline: the one number that says whether a
# swept body still rides the path it was given.
_TO_PATH = """
const toPath = (p, path) => {
  let best = Infinity;
  for (let i = 1; i < path.length; i++) {
    const a = path[i - 1], b = path[i];
    const dx = b[0] - a[0], dy = b[1] - a[1], dz = b[2] - a[2];
    const ll = dx * dx + dy * dy + dz * dz;
    let t = ll < 1e-12 ? 0
        : ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy + (p[2] - a[2]) * dz)
          / ll;
    t = Math.min(1, Math.max(0, t));
    best = Math.min(best, Math.hypot(p[0] - (a[0] + dx * t),
                                     p[1] - (a[1] + dy * t),
                                     p[2] - (a[2] + dz * t)));
  }
  return best;
};
"""

# The peak DISPLAY alpha one shell writes, from the shipped uniforms and
# the shipped roll-off.  `face` is 1 down the axis of the tube, where the
# toe is fully open, so the whole peak is the gain's own curve.
_STACK = """
const peak = (mesh) => {
  const u = mesh.material.uniforms;
  return 1 - Math.exp(-u.uGain.value * 1.6);
};
"""


def test_every_program_compiles_in_a_fogged_scene():
    """Every shader this module ships, on the GPU, in the scene shape it
    ships into.  Asset mode builds an UNFOGGED scene, so `USE_FOG` is
    never defined and the fog branch of each of these shaders is never
    compiled; neon ships into fogged night scenes, so the branch has to be
    real.  So does the normal-blended `ambient` branch, which is a
    different material, and the spill chained under two other libraries'
    patches, which is where a GLSL namespace collides."""
    code, out = compile_scene(_SCENE, _LIBS)
    assert code == 0, out
    assert "no fog chunks" not in out, out
    # A green exit on an empty compile proves nothing: the scene holds two
    # tubes (two shells each), two lanes of trails twice over and one
    # patched wall, and every one of those nine has to have reached the
    # GPU.
    # A floor, not an equality: `custom_materials` also counts materials a
    # sibling library staged into this scene patches, so an exact number
    # here goes stale on somebody else's edit.
    report = json.loads(out[out.index("{"):out.rindex("}") + 1])
    assert report["compile"]["custom_materials"] >= 9, report
    assert report["compile"]["programs"] >= 4, report


def test_every_glow_mesh_is_kept_out_of_the_override_passes():
    """GTAOPass redraws the scene with an OPAQUE override material, in
    which a glow shell is a solid wall.  The same guard stops the mesh
    casting a shadow, which is what a light source must never do."""
    out = measure("""
import { makeNeonTube, makeLightTrails } from './lib/neon.js';

const rows = [];
for (const g of [makeNeonTube({ path: [[0, 0, 0], [0, 1, 0], [1, 1, 0]],
                                radius: 0.05, seed: 3 }),
                 makeLightTrails({ path: [[-9, 0, 0], [9, 0, 0]],
                                   seed: 7 })]) {
  g.traverse((o) => {
    if (!o.isMesh) return;
    rows.push({ name: o.name,
                guarded: o.userData.astraNoOverride === true,
                shadow: !!o.castShadow,
                transparent: !!o.material.transparent });
  });
}
console.log(JSON.stringify({ rows }));
""", _LIBS)
    rows = out["rows"]
    assert len(rows) == 4, rows
    for r in rows:
        assert r["transparent"], r
        assert r["guarded"], f"{r['name']} would be a solid wall to GTAO"
        assert not r["shadow"], f"{r['name']} casts a shadow"


def test_the_spill_falls_off_with_distance_and_stops_at_its_radius():
    """Read straight out of the GLSL the patch ships.  A bare inverse
    square never reaches zero, so every wall in the scene would keep a wash
    of every sign in it and the night would go flat — the windowed form is
    what makes `radius` mean something."""
    out = measure(_GLSL_AS_JS + """
import * as THREE from 'three';
import { patchNeonSpill } from './lib/neon.js';

const m = new THREE.MeshStandardMaterial({ color: 0x333333 });
patchNeonSpill(m, { sources: [[0, 0, 0]], radius: 4, strength: 1 });
const patch = m.userData.astraPatches.find((p) => p.name === 'neon:spill');
const fall = glslFn(patch.fragmentHead, 'astraNeonFall', ['d', 'r']);
const curve = [];
for (let i = 0; i <= 40; i++) curve.push(fall(i * 0.15, 4));
console.log(JSON.stringify({
  curve, at0: fall(0, 4), atR: fall(4, 4),
  beyond: [fall(4.5, 4), fall(9, 4), fall(400, 4)],
}));
""", _LIBS)
    assert out["at0"] > 0.9, out["at0"]
    curve = out["curve"]
    for a, b in zip(curve, curve[1:], strict=False):
        assert b <= a, f"spill rises with distance: {a} -> {b}"
    inside = [c for c in curve[:27] if c > 0]
    assert len(inside) > 20 and inside[0] > 8 * inside[-1], inside
    assert out["atR"] == 0, out["atR"]
    assert out["beyond"] == [0, 0, 0], out["beyond"]


def test_the_falloff_is_what_actually_lights_the_wall():
    """A falloff nothing multiplies is decorative.  The spill must reach
    the frame through `totalEmissiveRadiance` (light ARRIVING on the
    surface, which no albedo term can fake), weighted by how the surface
    faces the source, with `instanceMatrix` folded in by hand — an
    instanced facade is otherwise lit as though every copy sat at the mesh
    origin."""
    out = measure("""
import * as THREE from 'three';
import { patchNeonSpill } from './lib/neon.js';

const m = new THREE.MeshStandardMaterial();
patchNeonSpill(m, { sources: [[0, 2, 0]], radius: 5 });
const p = m.userData.astraPatches.find((q) => q.name === 'neon:spill');
console.log(JSON.stringify({
  body: p.fragmentBody, vert: p.vertexBody,
  count: m.userData.uniforms.uNeonCount.value,
  radius: m.userData.uniforms.uNeonRadius.value,
}));
""", _LIBS)
    body = out["body"]
    assert "astraNeonFall(d, uNeonRadius)" in body, body
    assert "totalEmissiveRadiance +=" in body, body
    assert "dot(nsN, dv" in body, body
    assert "USE_INSTANCING" in out["vert"], out["vert"]
    assert out["count"] == 1 and out["radius"] == 5, out


def test_the_two_lanes_of_traffic_run_in_opposite_directions():
    """Warm one way and red the other is what says a road has two
    directions.  Both lanes riding the same sign of `uDir` is a road where
    every vehicle travels the same way — measured by tracking each lane's
    comet HEADS along the shipped phase function."""
    out = measure(_GLSL_AS_JS + """
import { makeLightTrails } from './lib/neon.js';

const g = makeLightTrails({ path: [[-20, 0, 0], [20, 0, 0]], count: 5,
                            speed: 14, seed: 7 });
const heads = (mesh, t) => {
  const u = mesh.material.uniforms;
  const phase = glslFn(mesh.material.fragmentShader, 'astraTrailPhase',
                       ['v', 'dir', 't', 'rate', 'count']);
  const at = (v) => phase(v, u.uDir.value, t, u.uRate.value,
                          u.uCount.value);
  const out = [];
  let prev = at(0);
  for (let i = 1; i <= 20000; i++) {
    const v = i / 20000, p = at(v);
    if (Math.abs(prev - p) > 0.5) out.push(v);
    prev = p;
  }
  return out;
};
const track = (mesh) => {
  const a = heads(mesh, 0), b = heads(mesh, 0.05);
  const near = a.reduce((x, y) => (Math.abs(y - 0.5) < Math.abs(x - 0.5)
      ? y : x));
  const to = b.reduce((x, y) => (Math.abs(y - near) < Math.abs(x - near)
      ? y : x));
  return { from: near, to, n: a.length };
};
const warm = g.getObjectByName('TrailsWarm');
const cool = g.getObjectByName('TrailsCool');
console.log(JSON.stringify({
  warm: track(warm), cool: track(cool),
  warmColor: warm.material.uniforms.uColor.value.toArray(),
  coolColor: cool.material.uniforms.uColor.value.toArray(),
  dirs: [warm.material.uniforms.uDir.value,
         cool.material.uniforms.uDir.value],
}));
""", _LIBS)
    warm, cool = out["warm"], out["cool"]
    assert warm["n"] == 5 and cool["n"] == 5, out
    assert warm["to"] > warm["from"], warm
    assert cool["to"] < cool["from"], cool
    assert out["dirs"][0] * out["dirs"][1] < 0, out["dirs"]
    # Head-lights are warm white, tail-lights are red: the red lane must
    # be unmistakably red, and the warm lane must not be.
    wr, wg, wb = out["warmColor"]
    cr, cg, cb = out["coolColor"]
    assert wg > 0.55 * wr and wb > 0.3 * wr, out["warmColor"]
    assert cg < 0.25 * cr and cb < 0.25 * cr, out["coolColor"]


def test_the_trails_run_along_the_road_they_were_given():
    """A streak that crosses the road instead of following it is the whole
    effect lost.  Every ribbon vertex stays inside the lane it was asked
    for, the ribbons span the path end to end, and they lie ON the road
    rather than floating over it."""
    out = measure(_TO_PATH + """
import { makeLightTrails } from './lib/neon.js';

const path = [[-20, 0, 0], [0, 0, 6], [20, 0, 0]];
const g = makeLightTrails({ path, count: 5, width: 0.5, lane: 2.0,
                            lift: 0.03, seed: 7 });
const rows = {};
g.traverse((o) => {
  if (!o.isMesh) return;
  const a = o.geometry.attributes.position.array;
  let far = 0, minX = 1e9, maxX = -1e9, offY = 0;
  for (let i = 0; i < a.length; i += 3) {
    far = Math.max(far, toPath([a[i], 0, a[i + 2]], path));
    minX = Math.min(minX, a[i]);
    maxX = Math.max(maxX, a[i]);
    offY = Math.max(offY, Math.abs(a[i + 1]));
  }
  rows[o.name] = { far, minX, maxX, offY };
});
console.log(JSON.stringify(rows));
""", _LIBS)
    # lane 2.0 + half the 3 m ribbon + the corner fillet's own bound.
    tol = 2.0 + 0.5 * 3.0 + 4.0 / 4
    for name, r in out.items():
        assert r["far"] <= tol, f"{name} strays {r['far']:.2f} m off"
        assert r["minX"] < -19.5 and r["maxX"] > 19.5, r
        assert 0 < r["offY"] < 0.1, r


def test_the_tube_follows_the_path_it_was_bent_along():
    """The sign IS the path.  A sweep that wanders off it draws a shape
    nobody asked for, and a sweep that stops short leaves a letter
    unfinished — so both bounds are checked."""
    out = measure(_TO_PATH + """
import { makeNeonTube } from './lib/neon.js';

// One stroke per character, which is how a sign shop bends a word.
const strokes = [[[0, 0, 0], [0, 1.2, 0], [0.7, 0, 0], [0.7, 1.2, 0]],
                 [[1.2, 0, 0], [1.2, 1.2, 0], [1.9, 1.2, 0],
                  [1.9, 0, 0], [1.2, 0, 0]]];
const g = makeNeonTube({ path: strokes, radius: 0.05, bend: 0.3,
                         seed: 3 });
const core = g.getObjectByName('NeonCore');
const a = core.geometry.attributes.position.array;
let far = 0;
for (let i = 0; i < a.length; i += 3) {
  far = Math.max(far, Math.min(
      ...strokes.map((s) => toPath([a[i], a[i + 1], a[i + 2]], s))));
}
let gap = 0;
for (const s of strokes) {
  for (let k = 0; k <= 60; k++) {
    const t = k / 60 * (s.length - 1);
    const i = Math.min(s.length - 2, Math.floor(t)), f = t - i;
    const p = [0, 1, 2].map((j) => s[i][j] + (s[i + 1][j] - s[i][j]) * f);
    let near = Infinity;
    for (let q = 0; q < a.length; q += 3) {
      near = Math.min(near, Math.hypot(a[q] - p[0], a[q + 1] - p[1],
                                       a[q + 2] - p[2]));
    }
    gap = Math.max(gap, near);
  }
}
const single = makeNeonTube({ path: [[0, 0, 0], [2, 0, 0]], radius: 0.05 });
console.log(JSON.stringify({
  far, gap, verts: a.length / 3,
  singleVerts: single.getObjectByName('NeonCore')
      .geometry.attributes.position.count,
}));
""", _LIBS)
    # radius, plus the bend/4 a filleted corner is allowed to cut.
    assert out["far"] <= 0.05 + 0.3 / 4 + 1e-3, out["far"]
    assert out["gap"] <= 0.05 + 0.3 / 4 + 0.02, out["gap"]
    assert out["verts"] > 400, out
    # A bare polyline is one stroke, not a stroke list, and still builds.
    assert out["singleVerts"] > 100, out


def test_the_same_seed_builds_the_same_sign_twice():
    """No Math.random anywhere: two runs of one brief must be the same
    frame, and a different seed must change the stutter rather than the
    glass — a re-seeded sign is the same sign, flickering differently."""
    out = measure("""
import { makeNeonTube, makeLightTrails } from './lib/neon.js';

const tube = (seed) => {
  const g = makeNeonTube({ path: [[0, 0, 0], [0, 1, 0], [1, 1, 0]],
                           radius: 0.05, flicker: 0.5, seed });
  const c = g.getObjectByName('NeonCore');
  return { pos: Array.from(c.geometry.attributes.position.array),
           key: c.material.uniforms.uKey.value };
};
const trail = (seed) => makeLightTrails({ seed })
    .getObjectByName('TrailsWarm').material.uniforms.uKey.value;
const a = tube(3), b = tube(3), c = tube(4);
console.log(JSON.stringify({
  same: a.pos.length === b.pos.length
      && a.pos.every((v, i) => v === b.pos[i]),
  sameKey: a.key === b.key,
  otherKey: c.key,
  geomStable: a.pos.every((v, i) => v === c.pos[i]),
  trails: [trail(7), trail(7), trail(8)],
}));
""", _LIBS)
    assert out["same"] and out["sameKey"], out
    assert out["otherKey"] != out["sameKey"], out
    assert out["geomStable"], out
    t = out["trails"]
    assert t[0] == t[1] and t[0] != t[2], t


def test_one_tick_advances_every_material_in_the_group():
    """A shader whose uTime never moves is a still frame inside a moving
    one.  `tick` has to reach the halo as well as the core, both lanes,
    and the spill patch through `tickShaders`."""
    out = measure("""
import * as THREE from 'three';
import { makeNeonTube, patchNeonSpill, makeLightTrails }
    from './lib/neon.js';
import { tickShaders } from './lib/shader.js';

const sign = makeNeonTube({ path: [[0, 0, 0], [0, 1, 0]], seed: 3 });
const trails = makeLightTrails({ seed: 7 });
const n = sign.userData.tick(4.25) + trails.userData.tick(4.25);
const times = [];
for (const g of [sign, trails]) {
  g.traverse((o) => {
    if (o.isMesh) times.push(o.material.uniforms.uTime.value);
  });
}
const wall = new THREE.MeshStandardMaterial();
patchNeonSpill(wall, { sources: [[0, 3, 0]], radius: 5 });
const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), wall);
const m = tickShaders(mesh, 4.25);
console.log(JSON.stringify({
  n, times, patched: [m, wall.userData.uniforms.uTime.value],
}));
""", _LIBS)
    assert out["n"] == 4, out
    assert out["times"] == [4.25] * 4, out
    assert out["patched"] == [1, 4.25], out


def test_added_light_is_the_default_and_daylight_is_the_escape():
    """Added light survives a DARK frame and clips over a bright one, and
    a night city is the dark frame — so additive is the default.  The
    escape has to sit BELOW the middle of the range: a composer handed
    "0 an unlit street to 1 open daylight" and no instinct picks 0.5, and
    0.5 must be the mode that cannot clip."""
    out = measure("""
import * as THREE from 'three';
import { makeNeonTube, patchNeonSpill, makeLightTrails }
    from './lib/neon.js';

const mode = (g) => {
  const out = [];
  g.traverse((o) => {
    if (o.isMesh) out.push(o.material.blending === THREE.AdditiveBlending);
  });
  return out;
};
const gain = (amb) => {
  const m = new THREE.MeshStandardMaterial();
  patchNeonSpill(m, { sources: [[0, 3, 0]], radius: 5, strength: 1,
                      ambient: amb });
  return m.userData.uniforms.uNeonGain.value;
};
console.log(JSON.stringify({
  night: mode(makeNeonTube({})).concat(mode(makeLightTrails({}))),
  mid: mode(makeNeonTube({ ambient: 0.5 }))
      .concat(mode(makeLightTrails({ ambient: 0.5 }))),
  day: mode(makeNeonTube({ ambient: 1 }))
      .concat(mode(makeLightTrails({ ambient: 1 }))),
  gains: [gain(undefined), gain(0.5), gain(1)],
}));
""", _LIBS)
    assert out["night"] == [True] * 4, out["night"]
    assert out["mid"] == [False] * 4, out["mid"]
    assert out["day"] == [False] * 4, out["day"]
    g = out["gains"]
    assert g[0] > g[1] > g[2] >= 0, g


def test_a_sign_hands_its_own_light_over_to_the_wall_behind_it():
    """The bridge that makes "the sign lights its wall" one line: the
    sources come back in WORLD space through the group's current matrix,
    so placing the sign moves the light it throws.  Sources read off an
    unplaced group light the world origin instead."""
    out = measure("""
import { makeNeonTube } from './lib/neon.js';

const g = makeNeonTube({ path: [[-1, 0, 0], [1, 0, 0]], radius: 0.05,
                         color: 0x00ff88, seed: 3 });
const before = g.userData.spillSources(4);
g.position.set(10, 4, -2);
const after = g.userData.spillSources(4);
console.log(JSON.stringify({
  n: after.length,
  capped: g.userData.spillSources(99).length,
  before: before.map((s) => s.position.toArray()),
  after: after.map((s) => s.position.toArray()),
  color: after[0].color.toArray(),
  spread: Math.max(...after.map((s) => s.position.x))
      - Math.min(...after.map((s) => s.position.x)),
}));
""", _LIBS)
    assert out["n"] == 4 and out["capped"] == 8, out
    for b, a in zip(out["before"], out["after"], strict=True):
        assert [round(v, 6) for v in a] == [
            round(b[0] + 10, 6), round(b[1] + 4, 6), round(b[2] - 2, 6)], out
    assert out["color"][1] > 0.5 and out["color"][0] < 0.1, out["color"]
    # Spread along the tube, not four copies of one point.
    assert out["spread"] > 1.0, out["spread"]


# ---------------------------------------------------------------------
# The port's own laws.


def test_the_additive_stack_is_budgeted_for_a_host_that_tone_maps():
    """LAW 1.  No post chain here: ACES and sRGB run in the fragment tail,
    so additive layers sum in DISPLAY space.  The middle of a tube carries
    four of them — the near and far walls of the double-sided core, and
    the halo twice over — and a sum past ~2 clips every channel of every
    colour, which is a white sign whatever gas it holds.  It still has to
    reach past 1, because a tube that cannot clip its strongest channel
    does not read as a light source at all."""
    out = measure(_STACK + """
import { makeNeonTube } from './lib/neon.js';

const g = makeNeonTube({ path: [[-1, 0, 0], [1, 0, 0]], seed: 3 });
const core = g.getObjectByName('NeonCore');
const halo = g.getObjectByName('NeonHalo');
const day = makeNeonTube({ path: [[-1, 0, 0], [1, 0, 0]], ambient: 0.8 });
console.log(JSON.stringify({
  core: peak(core), halo: peak(halo),
  sides: [core.material.side, halo.material.side],
  double: 2,
  dayCore: peak(day.getObjectByName('NeonCore')),
  dayAdditive: day.getObjectByName('NeonCore').material.blending,
}));
""", _LIBS)
    # Both shells are double-sided, so each is read twice on the middle
    # of the glass; that is the whole point of the chord weighting.
    stack = 2 * (out["core"] + out["halo"])
    assert 1.05 < stack < 1.80, (stack, out)
    # And the core is the larger half of it: a halo that outweighs its own
    # tube is fog with a line in it.
    assert out["core"] > out["halo"], out
    # The normal-blended daylight branch is a different budget: nothing
    # stacks there, so it is allowed to stay strong.
    assert out["dayCore"] > 0.9, out


def test_the_gas_colour_survives_the_middle_of_the_glass():
    """LAW 2, read out of the shipped GLSL and run as arithmetic.  The
    filament is what whitens the core, and applied before the display-space
    stack of law 1 it is what threw the colour away: at the old
    `pow(face, 5.0) * hot` all the way to white, a cyan tube's red channel
    arrived at 0.90 of its blue and the tube rendered grey."""
    out = measure(_GLSL_AS_JS + """
import { makeNeonTube } from './lib/neon.js';

const g = makeNeonTube({ path: [[-1, 0, 0], [1, 0, 0]], color: 0x2ad7ff,
                         seed: 3 });
const core = g.getObjectByName('NeonCore');
const src = core.material.fragmentShader;
const fil = glslFn(src, 'astraNeonFilament', ['face', 'hot']);
const hot = core.material.uniforms.uHot.value;
const gas = core.material.uniforms.uColor.value.toArray();
// The colour the shader writes: mix(gas, vec3(1.0), filament).
const at = (face) => gas.map(
    (v) => v + (1 - v) * fil(face, hot));
console.log(JSON.stringify({
  hot,
  peak: fil(1.0, hot),
  curve: [0.6, 0.7, 0.8, 0.9, 0.95, 1.0].map((f) => fil(f, hot)),
  mid: at(1.0), edge: at(0.8),
}));
""", _LIBS)
    # Never more than a fifth of the way to white, even dead centre.
    assert 0.05 < out["peak"] <= 0.21, out
    curve = out["curve"]
    for a, b in zip(curve, curve[1:], strict=False):
        assert b >= a, curve
    # Confined to the middle of the glass: two thirds of the way out it is
    # already invisible, which is what leaves the flanks fully coloured.
    assert curve[2] < 0.02, curve
    # And the gas is still cyan where it is hottest: the weak channel of a
    # saturated colour stays far below the strong one.
    r, gg, b = out["mid"]
    assert r < 0.35 * b, out["mid"]
    assert out["edge"][0] < 0.10 * out["edge"][2], out["edge"]


def test_the_halo_shell_never_draws_its_own_polygon():
    """LAW 3.  `astraFacing` is the exact chord through a CYLINDER and the
    shell is an n-gon, so at the outermost facet the interpolated normal is
    still 180/n degrees off perpendicular and the glow ends on a live
    alpha.  At 10 sides and 3.6 radii that was a straight-edged crease
    drawn around every letter.  Measured here at the worst facet the shell
    has, against the shader's own discard."""
    out = measure("""
import { makeNeonTube } from './lib/neon.js';

const g = makeNeonTube({ path: [[-1, 0, 0], [1, 0, 0]], radius: 0.05,
                         halo: 3.6, seed: 3 });
const rows = {};
for (const name of ['NeonHalo', 'NeonCore']) {
  const m = g.getObjectByName(name);
  const uv = m.geometry.attributes.uv.array;
  const us = new Set();
  for (let i = 0; i < uv.length; i += 2) us.add(uv[i].toFixed(5));
  const u = m.material.uniforms;
  // The shipped roll-off, at the worst normal a facet boundary carries.
  const sides = us.size - 1;
  const face = Math.sin(Math.PI / sides);
  const t = Math.min(Math.max(face / u.uToe.value, 0), 1);
  const body = Math.pow(face, u.uCore.value) * t * t * (3 - 2 * t);
  rows[name] = { sides, face,
                 alpha: 1 - Math.exp(-u.uGain.value * body * 1.6),
                 toe: u.uToe.value };
}
console.log(JSON.stringify(rows));
""", _LIBS)
    halo = out["NeonHalo"]
    # A 10-gon is what drew the polygon; the halo is the widest shell in
    # the effect and needs the sides.
    assert halo["sides"] >= 16, halo
    assert halo["toe"] >= 0.4, halo
    # Under the shader's own `if (a < 0.003) discard`, so the last facet is
    # never drawn at all rather than drawn faintly.
    assert halo["alpha"] < 0.003, halo
    # The core is exempt from the alpha bound and not from the sides: its
    # rim IS the edge of the glass and is meant to be visible, but at a
    # tenth of the halo's radius its facets land inside a pixel, so all it
    # needs is a silhouette round enough not to read as a prism.
    assert out["NeonCore"]["sides"] >= 12, out["NeonCore"]


def test_a_lane_is_traffic_and_not_one_lamp_copied():
    """LAW 4, the road half.  Five vehicles sharing one colour is a lane
    of clones.  The temperature offset is read out of the shipped GLSL,
    with the hash it calls extracted the same way rather than rewritten,
    and it has to SPREAD across the vehicles a lane actually carries — and
    to be spent both ways, because a rotation alone leaves a near-white
    head-light exactly where it was."""
    out = measure(_GLSL_AS_JS + """
import { makeLightTrails } from './lib/neon.js';

const g = makeLightTrails({ count: 5, seed: 7 });
const rows = {};
for (const name of ['TrailsWarm', 'TrailsCool']) {
  const m = g.getObjectByName(name);
  const src = m.material.fragmentShader;
  const hash = glslFn(src, 'astraHash11', ['p']);
  const temp = glslFn(src, 'astraTrailTemp', ['id', 'vary'],
                      { astraHash11: hash });
  const key = m.material.uniforms.uKey.value;
  const vary = m.material.uniforms.uVary.value;
  const ws = [];
  for (let i = 0; i < 5; i++) ws.push(temp(i + key, vary));
  rows[name] = { vary, ws, spread: Math.max(...ws) - Math.min(...ws),
                 rotates: src.includes('astraHueShift(uColor, w)'),
                 tilts: src.includes('vec3(1.0 + w') };
}
console.log(JSON.stringify(rows));
""", _LIBS)
    for name, r in out.items():
        assert r["vary"] > 0, (name, r)
        # Both lanes: five vehicles, five different lamps.
        assert len(set(round(w, 6) for w in r["ws"])) == 5, (name, r)
        assert r["spread"] > 0.25 * r["vary"], (name, r)
        # A rotation is what moves a saturated tail-light and a warm/cool
        # tilt is the only thing that moves a near-white head-light, so a
        # library that ships both colours needs both.
        assert r["rotates"] and r["tilts"], (name, r)
    # The head-lights vary more than the tail-lights: a lane of white
    # lamps has nothing else to tell one car from the next.
    assert out["TrailsWarm"]["vary"] > out["TrailsCool"]["vary"], out


def test_the_gas_drifts_along_the_tube_and_every_ramp_is_dithered():
    """LAWS 4 and 5, the sign half.  One flat colour along a bent tube is
    a printed decal; the drift rides the stroke's own v, so a word bent
    one stroke per letter comes out four related colours.  And the three
    widest ramps in this module — the halo, the wash on tarmac and the
    spill pool on a flat wall — all banded in 8-bit output until each
    carried a hash jitter."""
    out = measure("""
import * as THREE from 'three';
import { makeNeonTube, patchNeonSpill, makeLightTrails }
    from './lib/neon.js';

const sign = makeNeonTube({ path: [[-1, 0, 0], [1, 0, 0]], seed: 3 });
const trails = makeLightTrails({ seed: 7 });
const wall = new THREE.MeshStandardMaterial();
patchNeonSpill(wall, { sources: [[0, 3, 0]], radius: 5 });
const spill = wall.userData.astraPatches
    .find((p) => p.name === 'neon:spill');
const rows = {};
for (const g of [sign, trails]) {
  g.traverse((o) => {
    if (!o.isMesh) return;
    const src = o.material.fragmentShader;
    rows[o.name] = {
      dither: src.includes('astraHash21(gl_FragCoord.xy'),
      vary: o.material.uniforms.uVary.value,
      drifts: src.includes('astraHueShift(uColor, drift * uVary)'),
    };
  });
}
rows.spill = { dither: spill.fragmentBody
    .includes('astraHash21(gl_FragCoord.xy') };
console.log(JSON.stringify(rows));
""", _LIBS)
    for name, r in out.items():
        assert r["dither"], f"{name} lays a wide ramp with no dither"
    for name in ("NeonCore", "NeonHalo"):
        assert out[name]["vary"] > 0 and out[name]["drifts"], out[name]
