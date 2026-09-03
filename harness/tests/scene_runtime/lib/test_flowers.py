"""flowers.js — a patch of flowering plants, and what comes down through the air.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_flowers_lib.py).  The reference's properties are kept: the plants
sit on the CALLER's ground, no two of them are alike, the three kinds are three
silhouettes out of ONE compiled program, one wind moves the meadow and the
flowers together, a falling leaf does not come down in a straight line, and
both fields stay inside the box they advertise.

Their ``scene_check`` compile case is restored at the end of this file, through
``_probe.compile_scene`` (19 programs, 8 custom materials).  What is asserted
before it is the STRUCTURE of the GLSL, read back off ``onBeforeCompile``; the
frames are at fx/out/flowers/.

The last three tests are new, and each pins a change the port made:
the depth pass now cuts the shadow to the petal's own profile (three's depth
shader has no ``<color_fragment>``, so ``patchStandard`` drops a fragment body
there silently and the flowers cast the shadow of their RECTANGLES); the petal
albedo is clamped into a physical range last in the chain (base * rim * tone
plus the transmitted lift reached 1.36 on the daisy, which is a petal with no
shading left on it); and the per-plant hue jitter is now small on a saturated
kind, because ``astraHueShift`` rotates in RGB and drives a near-primary's
small channels negative — measured on the render, 0.25 rad split the poppy
field into an amber lobe and a magenta one with nothing in between.
"""
from __future__ import annotations

import pytest
from _probe import compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "noise.js", "grass.js", "foliage_shade.js",
         "finish.js", "flowers.js")

# Perpendicular deviation of a horizontal path from its own chord: the one
# number that separates a leaf from a falling stone.
_DEVIATE = """
const deviate = (pts) => {
  const a = pts[0], b = pts[pts.length - 1];
  const dx = b[0] - a[0], dz = b[1] - a[1];
  const len = Math.hypot(dx, dz);
  if (len < 1e-9) {
    let m = 0;
    for (const p of pts) m = Math.max(m, Math.hypot(p[0] - a[0], p[1] - a[1]));
    return m;
  }
  let m = 0;
  for (const p of pts) {
    m = Math.max(m, Math.abs((p[0] - a[0]) * dz - (p[1] - a[1]) * dx) / len);
  }
  return m;
};
const jumped = (pts, lim) => {
  for (let i = 1; i < pts.length; i++) {
    if (Math.hypot(pts[i][0] - pts[i - 1][0],
                   pts[i][1] - pts[i - 1][1]) > lim) return true;
  }
  return false;
};
"""

# Run a material's patch chain the way three does, and hand back the GLSL.
_COMPILE = """
const compile = (mat, vs, fs) => {
  const shader = { uniforms: {}, vertexShader: vs, fragmentShader: fs };
  mat.onBeforeCompile(shader, {});
  return shader;
};
const STD_V = '#include <begin_vertex>\\n';
const STD_F = 'void main() {\\n#include <color_fragment>\\n}\\n';
const DEPTH_F = 'void main() {\\n#include <alphatest_fragment>\\n}\\n';
"""


def test_each_field_is_one_draw_call_with_position_at_the_origin():
    """The bet both factories make: instances are free, meshes are not, so a
    whole plant — stem, petals and boss — is ONE instance of one geometry.  And
    GTAO redraws with an override material that ignores custom vertex shaders,
    so a petal left in `position` would stack every copy at the world origin."""
    out = measure("""
import { makeFlowers, makeFalling } from './lib/flowers.js';
const shape = (g) => {
  const drawn = [];
  g.traverse((o) => { if (o.isMesh || o.isPoints) drawn.push(o); });
  const m = drawn[0], geo = m.geometry;
  return {
    meshes: drawn.length,
    instanced: !!geo.isInstancedBufferGeometry,
    groups: geo.groups.length,
    materials: [].concat(m.material).length,
    instances: geo.instanceCount,
    verts: geo.attributes.position.count,
    posAllZero: Array.from(geo.attributes.position.array)
        .every((v) => v === 0),
    hasCorner: !!geo.attributes.aCorner,
    hasNormal: !!geo.attributes.normal,
    guarded: !!m.userData.astraNoOverride,
    casts: m.castShadow === true,
    depthShared: !!m.customDepthMaterial
        && m.customDepthMaterial.userData.uniforms
            === m.material.userData.uniforms,
  };
};
console.log(JSON.stringify({
  small: shape(makeFlowers({ extent: 2, density: 9, seed: 2,
                            shadows: true })),
  big: shape(makeFlowers({ extent: 20, density: 9, seed: 2,
                           shadows: true })),
  poppy: shape(makeFlowers({ kind: 'poppy', extent: 4, density: 9,
                             shadows: true })),
  unshadowed: shape(makeFlowers({ extent: 8, density: 9, seed: 2 })),
  petals: shape(makeFalling({ count: 300 })),
  leaves: shape(makeFalling({ kind: 'leaf', count: 900 })),
}));
""", _LIBS)
    # A meadow of flower heads in the shadow map stamps a hard block on the
    # ground under each one, so casting is opt-in as it is for grass.
    assert out["unshadowed"]["casts"] is False, out["unshadowed"]
    assert out["unshadowed"]["guarded"], out["unshadowed"]
    for k in ("small", "big", "poppy", "petals", "leaves"):
        f = out[k]
        assert f["meshes"] == 1, (k, out)
        assert f["instanced"] and f["materials"] == 1, (k, out)
        # Geometry groups are per-material sub-draws: one draw call means none.
        assert f["groups"] == 0, (k, out)
        assert f["posAllZero"] and f["hasCorner"], (k, out)
        # A zero normal NaNs through the shadow-bias path of every lit
        # material, and instancedQuad ships none.
        assert f["hasNormal"], (k, out)
        assert f["guarded"], (k, out)
    for k in ("small", "big", "poppy"):
        assert out[k]["casts"] and out[k]["depthShared"], (k, out)
    for k in ("petals", "leaves"):
        assert not out[k]["casts"], (k, out)
    assert out["small"]["verts"] == out["big"]["verts"], out
    assert out["big"]["instances"] > 10 * out["small"]["instances"], out
    assert out["petals"]["instances"] == 300, out
    assert out["leaves"]["instances"] == 900, out


def test_the_stated_sphere_covers_every_flower_at_full_bend():
    """`position` is all zeros, so the bounds three would derive are a point at
    the origin — and three CULLS on the sphere.  The box also has to hold at
    every t: the stem is laid over on an arc by a gust the CPU never sees, so
    the reach is computed from the LARGEST bend that gust term can reach."""
    out = measure("""
import { makeFlowers } from './lib/flowers.js';
const check = (kind) => {
  const g = makeFlowers({ kind, extent: 5, density: 12, seed: 4,
                          heightAt: (x, z) => 0.1 * x, wind: 1.4 });
  const m = g.children[0], geo = m.geometry;
  const u = m.material.userData.uniforms;
  const P = geo.attributes.iPos.array, S = geo.attributes.iShape.array;
  const B = geo.attributes.iBend.array;
  const len = u.uFlowSize.value.x, wid = u.uFlowSize.value.y;
  const curl = Math.abs(u.uFlowCurl.value);
  const cup = Math.abs(u.uFlowCup.value);
  const disc = u.uFlowDisc.value, bulge = u.uFlowBulge.value;
  const b = geo.boundingBox, s = geo.boundingSphere;
  let worstBox = -1e9, worstSphere = -1e9, need = 0, top = -1e9;
  for (let i = 0; i < geo.instanceCount; i++) {
    const h = S[i * 4], hs = S[i * 4 + 2];
    const k = Math.min(u.uFlowBend.value, B[i * 4 + 2] + B[i * 4] * 1.19);
    const fs = hs * 1.25;
    const outR = disc * hs + len * fs + 0.5 * wid * fs;
    const upR = curl * len * fs + 0.25 * cup * wid * fs + bulge * hs;
    const reach = h * (1 - Math.cos(k)) / k + outR;
    const high = P[i * 3 + 1] + h * Math.sin(k) / k + upR;
    need = Math.max(need, reach);
    top = Math.max(top, high);
    for (const [x, y, z] of [[P[i * 3] + reach, high, P[i * 3 + 2] + reach],
                             [P[i * 3] - reach, P[i * 3 + 1],
                              P[i * 3 + 2] - reach]]) {
      worstBox = Math.max(worstBox, b.min.x - x, x - b.max.x,
                          b.min.y - y, y - b.max.y,
                          b.min.z - z, z - b.max.z);
      worstSphere = Math.max(worstSphere,
          Math.hypot(x - s.center.x, y - s.center.y, z - s.center.z)
          - s.radius);
    }
  }
  return { worstBox, worstSphere, radius: s.radius, need,
           box: [b.min.toArray(), b.max.toArray()], top,
           n: geo.instanceCount };
};
console.log(JSON.stringify({
  daisy: check('daisy'), poppy: check('poppy'), lavender: check('lavender'),
}));
""", _LIBS)
    for kind in ("daisy", "poppy", "lavender"):
        f = out[kind]
        assert f["n"] > 100, (kind, out)
        assert f["worstBox"] <= 1e-6, (kind, out)
        assert f["worstSphere"] <= 1e-6, (kind, out)
        # And not a made-up huge number: instancedQuad's 1e4 default would
        # "cover" anything and cull nothing.
        true_half = 2.5 + f["need"]
        assert f["box"][1][0] <= true_half * 1.2, (kind, out)
        assert f["radius"] <= true_half * 1.7, (kind, out)
        assert f["box"][0][1] < -0.2, (kind, out)
        assert f["box"][1][1] < 1.2, (kind, out)


def test_the_flowers_are_rooted_on_the_callers_ground():
    """A flower that ignores `heightAt` hovers over a slope or sinks into it,
    and no amount of shader work rescues that."""
    out = measure("""
import { makeFlowers } from './lib/flowers.js';
const bed = (x, z) => 0.22 * x - 0.13 * z;
const g = makeFlowers({ extent: 8, density: 10, seed: 8, heightAt: bed });
const geo = g.children[0].geometry;
const P = geo.attributes.iPos.array;
let err = 0, span = 0, lo = 1e9, hi = -1e9;
for (let i = 0; i < geo.instanceCount; i++) {
  const x = P[i * 3], y = P[i * 3 + 1], z = P[i * 3 + 2];
  err = Math.max(err, Math.abs(y - bed(x, z)));
  lo = Math.min(lo, y);
  hi = Math.max(hi, y);
  span = Math.max(span, Math.abs(x), Math.abs(z));
}
const flat = makeFlowers({ extent: 4, density: 10 }).children[0].geometry;
const F = flat.attributes.iPos.array;
let flatMax = 0;
for (let i = 1; i < F.length; i += 3) flatMax = Math.max(flatMax, F[i]);
console.log(JSON.stringify({ err, lo, hi, span, flatMax,
                             n: geo.instanceCount }));
""", _LIBS)
    assert out["err"] < 1e-6, out
    assert out["hi"] - out["lo"] > 1.0, out
    assert out["span"] <= 4.0 + 1e-9, out
    assert out["flatMax"] == 0, out


def test_no_two_plants_in_a_patch_are_alike():
    """A field of identical flowers is a texture.  Six things differ per plant,
    and how far it has OPENED is the one that matters most, because a patch
    where every bloom is at full spread reads as plastic."""
    out = measure("""
import { makeFlowers } from './lib/flowers.js';
const geo = makeFlowers({ extent: 6, density: 12, seed: 5 })
    .children[0].geometry;
const S = geo.attributes.iShape.array, B = geo.attributes.iBend.array;
const V = geo.attributes.iVar.array;
const stat = (get) => {
  const v = [];
  for (let i = 0; i < geo.instanceCount; i++) v.push(get(i));
  const mean = v.reduce((a, b) => a + b, 0) / v.length;
  const sd = Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / v.length);
  return { spread: (Math.max(...v) - Math.min(...v)) / Math.abs(mean),
           rel: sd / Math.abs(mean), uniq: new Set(v).size };
};
console.log(JSON.stringify({
  height: stat((i) => S[i * 4]),
  width: stat((i) => S[i * 4 + 1]),
  head: stat((i) => S[i * 4 + 2]),
  open: stat((i) => S[i * 4 + 3]),
  lean: stat((i) => B[i * 4 + 2]),
  hue: stat((i) => V[i * 4] + 2),
  value: stat((i) => V[i * 4 + 3]),
  n: geo.instanceCount,
}));
""", _LIBS)
    for key, spread in (("height", 0.4), ("width", 0.4), ("head", 0.4),
                        ("open", 0.4), ("lean", 0.4), ("value", 0.25)):
        f = out[key]
        # Near-continuous, not three sizes: a per-plant draw, not a lookup
        # into a table of variants.
        assert f["uniq"] > 0.9 * out["n"], (key, out)
        assert f["rel"] > 0.08, (key, out)
        assert f["spread"] > spread, (key, out)
    assert out["hue"]["uniq"] > 0.9 * out["n"], out


def test_the_three_kinds_are_three_silhouettes():
    """A disc of petals, a cup and a spike.  They share ONE compiled program —
    the cache key is the patch chain, and the first material to compile a key
    decides the GLSL for every material that shares it — so the silhouette can
    only live in uniforms and in the petal layout."""
    out = measure("""
import { makeFlowers } from './lib/flowers.js';
const probe = (kind) => {
  const m = makeFlowers({ kind, extent: 2, density: 6 }).children[0];
  const u = m.material.userData.uniforms;
  const a = m.geometry.attributes.aPart.array;
  let petals = 0;
  for (let i = 0; i < a.length; i += 3) petals = Math.max(petals, a[i + 1]);
  return {
    key: m.material.customProgramCacheKey(),
    elev: u.uFlowElev.value, curl: u.uFlowCurl.value,
    cup: u.uFlowCup.value, spike: u.uFlowSpike.value,
    step: u.uFlowStep.value, disc: u.uFlowDisc.value,
    prof: u.uFlowProf.value.toArray(), blotch: u.uFlowBlotch.value,
    len: u.uFlowSize.value.x, wid: u.uFlowSize.value.y,
    petals: petals + 1,
  };
};
console.log(JSON.stringify({
  daisy: probe('daisy'), poppy: probe('poppy'), lavender: probe('lavender'),
}));
""", _LIBS)
    d, p, lv = out["daisy"], out["poppy"], out["lavender"]
    assert d["key"] == p["key"] == lv["key"], out
    # A daisy is a FLAT disc of many narrow rays round a boss.
    assert d["elev"] < 0.3 and d["petals"] >= 10, out
    assert d["disc"] > 0 and d["wid"] < 0.5 * d["len"], out
    # A poppy is a CUP: few broad petals, lifted and curled inward, with a
    # dark blotch at the throat.
    assert p["elev"] > 2 * d["elev"] and p["petals"] <= 6, out
    assert p["cup"] > d["cup"] and p["curl"] > 0, out
    assert p["wid"] > 0.7 * p["len"] and p["blotch"] > 0.5, out
    # A spike spreads its florets DOWN the stem; the other two gather them.
    assert lv["spike"] > 0.2 and d["spike"] == 0 and p["spike"] == 0, out
    assert lv["petals"] > 2 * d["petals"], out
    assert lv["len"] < 0.6 * d["len"], out
    assert len({round(d["step"], 4), round(p["step"], 4),
                round(lv["step"], 4)}) == 3, out


def test_one_wind_moves_the_grass_and_the_flowers_together():
    """`windOf` is IMPORTED from grass.js, not re-read here: a meadow whose
    flowers lean the other way is worse than a still one."""
    out = measure("""
import { makeGrass } from './lib/grass.js';
import { makeFlowers, makeFalling } from './lib/flowers.js';
const wind = { dir: [0.3, -0.9], strength: 1.6, speed: 1.7 };
const grass = makeGrass({ extent: 4, density: 20, wind, sward: false })
    .children[0].material.userData.uniforms;
const flow = makeFlowers({ extent: 4, density: 6, wind })
    .children[0].material.userData.uniforms;
const bendOf = (w) => {
  const geo = makeFlowers({ extent: 4, density: 6, seed: 2, wind: w })
      .children[0].geometry;
  const B = geo.attributes.iBend.array;
  let s = 0;
  for (let i = 0; i < geo.instanceCount; i++) s += B[i * 4];
  return s / geo.instanceCount;
};
const drift = (w) => makeFalling({ wind: w })
    .children[0].material.userData.uniforms.uFallDrift.value;
console.log(JSON.stringify({
  grassDir: grass.uGrassWind.value.toArray(),
  flowDir: flow.uFlowWind.value.toArray(),
  grassSpeed: grass.uGrassSpeed.value,
  flowSpeed: flow.uFlowSpeed.value,
  bendWeak: bendOf(0.5), bendStrong: bendOf(2),
  driftWeak: drift(0.5).length(), driftStrong: drift(2).length(),
  driftDir: drift(wind).clone().normalize().toArray(),
  defaultDir: makeFlowers({ extent: 1, density: 4 })
      .children[0].material.userData.uniforms.uFlowWind.value.toArray(),
}));
""", _LIBS)
    assert out["flowDir"] == out["grassDir"], out
    assert out["flowSpeed"] == out["grassSpeed"] == 1.7, out
    # grass.js's default is a light breeze toward +X, and a flower that
    # invented its own default would lean across the meadow.
    assert abs(out["defaultDir"][0] - 0.9113) < 1e-3, out
    assert abs(out["defaultDir"][1] - 0.4101) < 1e-3, out
    assert abs(out["bendStrong"] / out["bendWeak"] - 4) < 1e-6, out
    assert abs(out["driftStrong"] / out["driftWeak"] - 4) < 1e-6, out
    assert abs(out["driftDir"][0] - 0.3162) < 1e-3, out
    assert abs(out["driftDir"][1] + 0.9487) < 1e-3, out


def test_a_falling_leaf_does_not_come_down_in_a_straight_line():
    """The whole difference between a leaf and a dropped stone.  `sample(i, t)`
    is the CPU mirror of the vertex shader, so the path measured is drawn."""
    out = measure(_DEVIATE + """
import { makeFalling } from './lib/flowers.js';
const probe = (kind) => {
  const g = makeFalling({ kind, count: 300, extent: 12, height: 6,
                          seed: 7, wind: { dir: [1, 0], strength: 1 } });
  const geo = g.children[0].geometry;
  const F = geo.attributes.aFall.array, A = geo.attributes.aAxis.array;
  const dev = [], drop = [];
  for (let i = 0; i < geo.instanceCount; i++) {
    const pts = [];
    for (let k = 0; k <= 24; k++) {
      const p = g.userData.sample(i, k * 0.25);
      pts.push([p.x, p.z]);
    }
    if (!jumped(pts, 3)) dev.push(deviate(pts));
    drop.push(g.userData.sample(i, 0).y - g.userData.sample(i, 0.5).y);
  }
  dev.sort((a, b) => a - b);
  const rate = [], spin = [], axes = new Set();
  for (let i = 0; i < geo.instanceCount; i++) {
    rate.push(F[i * 4]);
    spin.push(F[i * 4 + 2]);
    axes.add([A[i * 3], A[i * 3 + 1], A[i * 3 + 2]].join(','));
  }
  let unit = 0;
  for (let i = 0; i < geo.instanceCount; i++) {
    unit = Math.max(unit, Math.abs(
        Math.hypot(A[i * 3], A[i * 3 + 1], A[i * 3 + 2]) - 1));
  }
  return {
    paths: dev.length, minDev: dev[0], medDev: dev[dev.length >> 1],
    rateSpread: Math.max(...rate) / Math.min(...rate),
    minSpin: Math.min(...spin.map(Math.abs)),
    bothWays: spin.some((v) => v > 0) && spin.some((v) => v < 0),
    spins: new Set(spin).size, axes: axes.size, unit,
    fell: drop.filter((d) => d > 0).length,
    rose: drop.filter((d) => d <= 0 && d > -3).length,
    fall: g.children[0].material.userData.uniforms.uFallRate.value,
    spinRate: g.children[0].material.userData.uniforms.uFallSpin.value,
  };
};
console.log(JSON.stringify({ petal: probe('petal'), leaf: probe('leaf') }));
""", _LIBS)
    for kind in ("petal", "leaf"):
        f = out[kind]
        assert f["paths"] >= 100, (kind, out)
        assert f["minDev"] > 0.002, (kind, out)
        assert f["medDev"] > 0.02, (kind, out)
        # Its own rate: the slowest and the fastest differ by half again,
        # or the field descends as one sheet.
        assert f["rateSpread"] > 1.5, (kind, out)
        assert f["minSpin"] > 0 and f["spinRate"] > 0, (kind, out)
        assert f["bothWays"] and f["spins"] > 250, (kind, out)
        assert f["axes"] > 250 and f["unit"] < 1e-6, (kind, out)
        # DOWN, for every one of them: the rest wrapped from the floor back
        # to the ceiling inside the window.
        assert f["fell"] > 250 and f["rose"] == 0, (kind, out)
    # A petal hangs where a leaf drops: slower, and sliding further.
    assert out["petal"]["fall"] < 0.6 * out["leaf"]["fall"], out
    assert out["petal"]["medDev"] > out["leaf"]["medDev"], out


def test_the_falling_field_stays_inside_the_volume_it_states():
    """Every position is computed in the shader from `uTime`, so the box is a
    PROMISE the CPU keeps, at every t and for every piece."""
    out = measure("""
import { makeFalling } from './lib/flowers.js';
const check = (kind, extent, height) => {
  const g = makeFalling({ kind, count: 200, extent, height, seed: 9,
                          wind: { dir: [1, 0.6], strength: 2 } });
  const m = g.children[0], geo = m.geometry;
  const u = m.material.userData.uniforms;
  const F = geo.attributes.aFall.array;
  const b = geo.boundingBox, s = geo.boundingSphere;
  const w = u.uFallSize.value.x, l = u.uFallSize.value.y;
  const curl = u.uFallCurl.value;
  let worstBox = -1e9, worstSphere = -1e9;
  for (let i = 0; i < geo.instanceCount; i++) {
    const span = 0.5 * Math.hypot(w, l) * F[i * 4 + 1]
        + 0.25 * curl * w * F[i * 4 + 1];
    for (let k = 0; k <= 40; k++) {
      const p = g.userData.sample(i, k * 0.37);
      worstBox = Math.max(worstBox,
          b.min.x - (p.x - span), (p.x + span) - b.max.x,
          b.min.y - (p.y - span), (p.y + span) - b.max.y,
          b.min.z - (p.z - span), (p.z + span) - b.max.z);
      worstSphere = Math.max(worstSphere,
          Math.hypot(p.x - s.center.x, p.y - s.center.y, p.z - s.center.z)
          + span - s.radius);
    }
  }
  return { worstBox, worstSphere, radius: s.radius,
           box: [b.min.toArray(), b.max.toArray()] };
};
console.log(JSON.stringify({
  petal: check('petal', 9, 5), leaf: check('leaf', 14, 8),
}));
""", _LIBS)
    for kind, extent, height in (("petal", 9, 5), ("leaf", 14, 8)):
        f = out[kind]
        assert f["worstBox"] <= 0, (kind, out)
        assert f["worstSphere"] <= 0, (kind, out)
        assert f["box"] == [[-extent / 2, 0, -extent / 2],
                            [extent / 2, height, extent / 2]], (kind, out)
        want = 0.5 * (2 * (extent ** 2) + height ** 2) ** 0.5
        assert abs(f["radius"] - want) < 1e-6, (kind, out)


def test_the_same_seed_builds_the_same_field():
    """Determinism is the contract for every shipped factory: a re-run that
    reshuffles the patch is a re-run nobody can compare against."""
    out = measure("""
import { makeFlowers, makeFalling } from './lib/flowers.js';
const plants = (s) => JSON.stringify(['iPos', 'iShape', 'iBend', 'iVar']
    .map((k) => Array.from(makeFlowers({ extent: 4, density: 9, seed: s,
        heightAt: (x, z) => 0.1 * x }).children[0].geometry.attributes[k]
        .array)));
const fall = (s) => JSON.stringify(['aPos', 'aFall', 'aAxis', 'aWob']
    .map((k) => Array.from(makeFalling({ kind: 'leaf', count: 60, seed: s })
        .children[0].geometry.attributes[k].array)));
console.log(JSON.stringify({
  plantsSame: plants(4) === plants(4), plantsDiffer: plants(4) !== plants(9),
  fallSame: fall(4) === fall(4), fallDiffer: fall(4) !== fall(9),
  n: JSON.parse(plants(4))[0].length,
}));
""", _LIBS)
    assert out["plantsSame"] and out["plantsDiffer"], out
    assert out["fallSame"] and out["fallDiffer"], out
    assert out["n"] > 300, out


def test_tick_advances_every_material():
    """Every moving part lives in the vertex shader, so an un-advanced uTime is
    a meadow frozen mid-gust with its petals hanging in the air."""
    out = measure("""
import * as THREE from 'three';
import { makeFlowers, makeFalling } from './lib/flowers.js';
const g = new THREE.Group();
for (const kind of ['daisy', 'poppy', 'lavender']) {
  g.add(makeFlowers({ kind, extent: 2, density: 5 }));
}
g.add(makeFalling({ kind: 'petal', count: 20 }));
g.add(makeFalling({ kind: 'leaf', count: 20 }));
const times = () => {
  const out = [];
  g.traverse((o) => {
    for (const m of [].concat(o.material || [])) {
      if (!m) continue;
      const u = (m.uniforms && m.uniforms.uTime) ? m.uniforms
          : (m.userData && m.userData.uniforms);
      if (u && u.uTime) out.push(u.uTime.value);
    }
  });
  return out;
};
const before = times();
for (const c of g.children) c.userData.tick(2.5);
console.log(JSON.stringify({ before, after: times() }));
""", _LIBS)
    assert out["before"] == [0] * 5, out
    assert out["after"] == [2.5] * 5, out


def test_the_shadow_is_cut_to_the_petal_and_not_to_its_card():
    """PORT FIX.  A petal is a rectangle the FRAGMENT stage carves a petal out
    of, and three's depth shader never sees that carving — worse,
    `patchStandard` writes a fragment body through `<color_fragment>`, which
    the depth shader does not contain, so the hook is dropped in silence.
    Measured on the showcase render before this: every shadowed daisy stamped
    a fused slate slab on the ground and the patch read as litter dropped
    round the plants.  The cut is spliced above three's own alpha test, which
    is the one place a depth pass may discard, and it reads the SURFACE's
    profile uniform so the shadow is cut by the curve that is drawn."""
    out = measure(_COMPILE + """
import { makeFlowers } from './lib/flowers.js';
const m = makeFlowers({ kind: 'daisy', extent: 2, density: 4,
                        shadows: true }).children[0];
const dep = m.customDepthMaterial;
const d = compile(dep, STD_V, DEPTH_F);
const surf = compile(m.material, STD_V, STD_F);
console.log(JSON.stringify({
  cuts: /discard/.test(d.fragmentShader),
  byProfile: d.fragmentShader.indexOf('flowProfile(vFlow.y, uFlowProf)') >= 0,
  aboveAlphaTest: d.fragmentShader.indexOf('discard')
      < d.fragmentShader.indexOf('#include <alphatest_fragment>'),
  // The one thing that makes the cut the SAME cut: the depth material is
  // handed the surface's own uniform map, profile and all.
  sameProfileObject: dep.userData.uniforms.uFlowProf
      === m.material.userData.uniforms.uFlowProf,
  uploaded: !!d.uniforms.uFlowProf,
  varying: /varying vec4 vFlow;/.test(d.fragmentShader),
  writesVarying: /vFlow = vec4/.test(d.vertexShader),
  // and the surface still discards by the same profile.
  surfaceProfile: surf.fragmentShader.indexOf('flowProfile(') >= 0,
  // one helper, not two: the vertex head carries it as well and a second
  // copy in one stage is a GLSL redefinition.
  vertexCopies: (d.vertexShader.match(/float flowProfile\\(/g) || []).length,
  fragCopies: (d.fragmentShader.match(/float flowProfile\\(/g) || []).length,
}));
""", _LIBS)
    assert out["cuts"] and out["byProfile"], out
    assert out["aboveAlphaTest"], out
    assert out["sameProfileObject"] and out["uploaded"], out
    assert out["varying"] and out["writesVarying"], out
    assert out["surfaceProfile"], out
    assert out["fragCopies"] == 1, out
    assert out["vertexCopies"] == 0, out


def test_a_petal_albedo_stays_inside_the_range_a_petal_can_have():
    """PORT FIX.  Three multipliers ride on the petal's colour — the rim goes
    paler, the plant has its own tone, and `patchLeafSSS` adds the light that
    came THROUGH — and on the daisy they reached 1.36.  An albedo over 1 is
    not a brighter petal: it is a petal with no shading left, because the tone
    map pins every pixel of it to the same white, and the whole silhouette
    goes to a paper cut-out.  The clamp is last in the chain, after the
    transmitted lift, or it clamps a number that then grows again."""
    out = measure(_COMPILE + """
import { makeFlowers, makeFalling } from './lib/flowers.js';
const m = makeFlowers({ extent: 2, density: 4 }).children[0];
const fs = compile(m.material, STD_V, STD_F).fragmentShader;
const fall = makeFalling({ count: 8 }).children[0];
console.log(JSON.stringify({
  chain: m.material.customProgramCacheKey(),
  clampAfterLift: fs.indexOf('clamp(diffuseColor.rgb')
      > fs.indexOf('uLeafTint * lT'),
  ceiling: /clamp\\(diffuseColor\\.rgb, vec3\\(0\\.015\\), vec3\\(0\\.86\\)\\)/
      .test(fs),
  fallCeiling: /min\\(flCol, vec3\\(0\\.86\\)\\)/.test(
      compile(fall.material, STD_V, STD_F).fragmentShader),
}));
""", _LIBS)
    # The lift is applied by name, so the order is the chain's order.
    assert out["chain"].endswith("flowers:albedoRange"), out
    assert out["clampAfterLift"], out
    assert out["ceiling"] and out["fallCeiling"], out


def test_one_patch_is_one_species_and_still_no_two_flowers_alike():
    """PORT FIX.  `astraHueShift` rotates in RGB about the grey axis, so on a
    near-primary albedo it drives the small channels NEGATIVE and the clamp
    turns one swing into two: measured on the render, 0.25 rad of jitter split
    the poppies into an amber lobe (20-40 deg) and a magenta one (330-355) with
    nothing in between — 0.72 pink pixels per red one.  A saturated kind now
    rotates barely at all and takes its variety from a stated SIBLING albedo
    instead, which is an offset in HSL and so follows `opts.color` too.  After:
    0.12 pink pixels per red one, and the spread is still there."""
    out = measure("""
import * as THREE from 'three';
import { makeFlowers, makeFalling } from './lib/flowers.js';
const hsl = (c) => { const o = {}; c.getHSL(o); return o; };
// astraHueShift, spelled in JS: a Rodrigues rotation about the grey axis.
// Its result is only a hue rotation while it stays inside the cube.
const K = 0.5773502691896258;
const rot = (col, a) => {
  const c = [col.r, col.g, col.b];
  const ca = Math.cos(a), sa = Math.sin(a);
  const cr = [K * (c[2] - c[1]), K * (c[0] - c[2]), K * (c[1] - c[0])];
  const d = K * (c[0] + c[1] + c[2]);
  return c.map((v, i) => v * ca + cr[i] * sa + K * d * (1 - ca));
};
const gamut = (col, swing) => Math.min(
    ...rot(col, swing / 2), ...rot(col, -swing / 2));
const probe = (kind) => {
  const geo = makeFlowers({ kind, extent: 4, density: 9, seed: 5 })
      .children[0];
  const u = geo.material.userData.uniforms;
  const V = geo.geometry.attributes.iVar.array;
  let lo = 1e9, hi = -1e9;
  const seen = new Set();
  for (let i = 0; i < geo.geometry.instanceCount; i++) {
    lo = Math.min(lo, V[i * 4]);
    hi = Math.max(hi, V[i * 4]);
    seen.add(V[i * 4]);
  }
  const a = hsl(u.uFlowPetal.value), b = hsl(u.uFlowPetalB.value);
  let dh = Math.abs(a.h - b.h);
  dh = Math.min(dh, 1 - dh);
  return { swing: hi - lo, uniq: seen.size, n: geo.geometry.instanceCount,
           sat: a.s, satB: b.s, dh, dl: Math.abs(a.l - b.l),
           gamut: gamut(u.uFlowPetal.value, hi - lo),
           siblingDiffers: u.uFlowPetal.value.getHex()
               !== u.uFlowPetalB.value.getHex() };
};
const custom = makeFlowers({ kind: 'poppy', color: 0x2f6ad0, extent: 2,
                             density: 4 }).children[0].material.userData
    .uniforms;
const fall = (kind) => {
  const u = makeFalling({ kind, count: 8 }).children[0].material.userData
      .uniforms;
  const a = hsl(u.uFallColor.value), b = hsl(u.uFallColorB.value);
  let dh = Math.abs(a.h - b.h);
  return { dh: Math.min(dh, 1 - dh), sat: a.s,
           differs: u.uFallColor.value.getHex()
               !== u.uFallColorB.value.getHex() };
};
console.log(JSON.stringify({
  daisy: probe('daisy'), poppy: probe('poppy'), lavender: probe('lavender'),
  customA: custom.uFlowPetal.value.getHexString(),
  customB: custom.uFlowPetalB.value.getHexString(),
  petal: fall('petal'), leaf: fall('leaf'),
}));
""", _LIBS)
    for kind in ("daisy", "poppy", "lavender"):
        f = out[kind]
        # Still a per-plant draw, never a table of three variants.
        assert f["uniq"] > 0.9 * f["n"], (kind, out)
        assert f["siblingDiffers"], (kind, out)
        # THE rule, stated as the mechanism rather than as a number: the
        # jitter a kind allows must keep the rotation inside the cube.  Once
        # a channel goes negative the clamp — not the angle — decides the
        # colour, and the swing stops being a hue and becomes a fork.  A
        # near-primary can afford almost none of it, a violet (no small
        # channel) a fair amount, a white as much as it likes.
        # (measured: the poppy reaches -0.0033 at its 0.07, and reached
        # -0.042 at the 0.5 every kind used to share.)
        assert f["swing"] > 0.02, (kind, out)
        assert f["gamut"] > -0.008, (kind, out)
        # The sibling carries the variety instead: a real second colour, and
        # still inside the species — either under a fifth of the wheel away,
        # or so weakly coloured that half a wheel is warm cream to cool
        # paper rather than a second flower.  Both ends have to be pale for
        # that, which is why the daisy's cool end drops saturation as it
        # turns: a white that keeps its saturation across the wheel is blue.
        assert f["dh"] <= 0.2 or max(f["sat"], f["satB"]) < 0.16, (kind, out)
        assert f["dh"] > 0.005 or f["dl"] > 0.02, (kind, out)
    # The poppy is the one that forked, and it is the tightest.
    assert out["poppy"]["swing"] < out["lavender"]["swing"], out
    assert out["lavender"]["swing"] < out["daisy"]["swing"], out
    # A caller's own colour gets its own sibling, not the table's.
    assert out["customA"] != out["customB"], out
    assert out["customA"].startswith("2f6a"), out
    for kind in ("petal", "leaf"):
        assert out[kind]["differs"] and out[kind]["dh"] <= 0.2, (kind, out)

# A FOGGED, shadow-casting scene: `USE_FOG` and the shadow branches only exist
# in a program built from one, and a patch that drops a chunk compiles fine in
# an unfogged fixture and renders as a sticker in a real scene.
_COMPILE_SCENE = """
import * as THREE from 'three';
import { tickShaders } from './lib/shader.js';
import { makeFlowers, makeFalling } from './lib/flowers.js';

export const BOUNDS = { min: [-30, 0, -30], max: [30, 20, 30] };
export function heightAt(x, z) { return Math.sin(x * 0.1) * 0.3; }

export async function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.004);
  scene.add(new THREE.HemisphereLight(0xbcd6ef, 0x4a4130, 1.0));
  const sun = new THREE.DirectionalLight(0xffffff, 3);
  sun.position.set(10, 20, 10); sun.castShadow = true; scene.add(sun);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(80, 80),
                                new THREE.MeshStandardMaterial({ color: 0x6d6a52 }));
  ground.rotation.x = -Math.PI / 2; ground.receiveShadow = true; scene.add(ground);
  scene.add(makeFlowers({ extent: 8, heightAt }));
  scene.add(makeFalling({ extent: 8, height: 5 }));
  return {
    scene,
    cameras: [{ name: 'a', position: [12, 5, 14], lookAt: [0, 2, 0], fov: 45 }],
    update(t) { tickShaders(scene, t); },
  };
}
"""


def test_every_program_compiles_on_the_gpu():
    """Two separate instanced programs — a rooted field that bends from its base
    and a falling one that integrates its own drift — each with a vertex stage
    three never wrote.

    The reference had this case and the port dropped it: `_probe.shader_check`
    staged only `src/fixture.js` while `check_shaders.mjs` boots the workspace's
    `src/scene.js`, so it could never run.  `_probe.compile_scene` stages the
    scene, so the case is back (consolidation, 2026-09-01)."""
    code, out = compile_scene(_COMPILE_SCENE, _LIBS)
    assert code == 0, out
    assert "ERROR" not in out, out
