"""Cloth and crop have to SHOW the wind, not merely be moved by it.

Each of the three earns its place by one cue, and each of those cues is a
number rather than a look:

  a flag is held at ONE edge, so its wave amplitude is pinned to zero along
  the hoist and largest at the fly — a sine over the whole sheet is a
  rippling billboard with no attachment;

  a banner is hung along its TOP edge, so it swings as the pendulum its own
  drop makes it (sqrt(g/drop), far slower than a flag flaps) and its folds
  are functions of x ALONE, which is what "the fold lines run down" means;

  a wheat field's subject is the WAVE, so two stalks a metre apart move
  together and two fifteen metres apart move with the lag the travel speed
  sets.  Per-stalk motion is a fraction of the shared term, or a hectare of
  crop boils instead of rippling.

The gust field is a seeded, seamlessly tiling texture rather than
GLSL_UTIL's unseeded noise, which is what lets these tests read the exact
numbers the vertex shader samples instead of trusting a claim about one.

Ported 2026-09-01 from the reference's tests/test_cloth_lib.py.  Their
separate `scene_check` (a fogged scene with a casting sun) is folded into the
one compile test here, because OUR check_shaders.mjs takes a workspace SCENE
rather than a bare asset: the fixture below sets its own fog and hangs a
casting sun, so every fog branch and both displaced depth materials are real
programs rather than dead code.

The grading test at the end is OURS — it holds the colour work done for this
renderer (ACES, exposure 1.0, no post chain).
"""
from __future__ import annotations

import json

from tests.scene_runtime.lib._probe import compile_scene, measure

_LIBS = ("shader.js", "noise.js", "grass.js", "cloth.js")

_SCENE = """
import * as THREE from 'three';
import { makeFlag, makeBanner, makeWheatField } from './lib/cloth.js';

export const BOUNDS = { min: [-20, 0, -20], max: [20, 12, 20] };

export function createScene() {
  const scene = new THREE.Scene();
  // Without this USE_FOG is undefined and every fog branch below compiles
  // to nothing — which is how a library ships fog chunks that cannot
  // compile at all and nobody finds out.
  scene.fog = new THREE.FogExp2(0xc9d6e0, 0.02);
  scene.add(new THREE.HemisphereLight(0xbcd6ef, 0x4a4130, 0.9));
  // And without a shadow CASTER the three custom depth materials this
  // library carries are never compiled either.
  const sun = new THREE.DirectionalLight(0xfff0d6, 2.5);
  sun.position.set(5, 8, 7);
  sun.castShadow = true;
  scene.add(sun);
  const wind = { dir: [1, 0.3], strength: 1.1, speed: 1 };
  scene.add(makeFlag({ width: 1.8, height: 1.1, mast: 6, wind, seed: 3 }));
  const b = makeBanner({ width: 2.2, drop: 3.2, wind, seed: 5 });
  b.position.set(5, 0, 0);
  scene.add(b);
  scene.add(makeWheatField({ extent: 12, density: 25, wind, seed: 7,
                            shadows: true }));
  // A camera, because the host reports a scene with none as not booted
  // and never reaches the compile stage at all.
  return {
    scene,
    cameras: [{ name: 'a', position: [8, 4, 10], lookAt: [0, 2, 0],
                fov: 45 }],
    update() {},
  };
}
"""


# The GLSL every wheat probe has to agree with: the gust texture is sampled
# bilinearly with wrapping, and astraStagger decorrelates the per-stalk term.
# Both are read from the shipped uniforms, so a probe cannot drift from the
# field it is measuring.
_WHEAT_READER = """
const smooth = (e0, e1, x) => {
  const t = Math.max(0, Math.min(1, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
};
const stagger = (x) => (Math.sin(x * 311) * 1.70
    + Math.sin(x * 137 + 1.1) * 1.15
    + Math.sin(x * 57 + 2.3) * 0.60) * 4.3;
const reader = (mesh) => {
  const g = mesh.geometry, u = mesh.material.userData.uniforms;
  const pos = g.attributes.iPos.array, crop = g.attributes.iCrop.array;
  const vary = g.attributes.iVar.array;
  const tex = u.uWheatWave.value;
  const W = tex.image.width, H = tex.image.height, D = tex.image.data;
  const at = (i, j) => D[((((j % H) + H) % H) * W
      + (((i % W) + W) % W)) * 4] / 255;
  const sample = (x, y) => {
    const fx = x * W - 0.5, fy = y * H - 0.5;
    const i0 = Math.floor(fx), j0 = Math.floor(fy);
    const tx = fx - i0, ty = fy - j0;
    return at(i0, j0) * (1 - tx) * (1 - ty) + at(i0 + 1, j0) * tx * (1 - ty)
        + at(i0, j0 + 1) * (1 - tx) * ty + at(i0 + 1, j0 + 1) * tx * ty;
  };
  const w = u.uWheatWind.value, tile = u.uWheatTile.value;
  const n = g.instanceCount;
  const A = new Float64Array(n), C = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    A[i] = pos[i * 3] * w.x + pos[i * 3 + 2] * w.y;
    C[i] = -pos[i * 3] * w.y + pos[i * 3 + 2] * w.x;
  }
  const wave = (i, t) => smooth(0.34, 0.98,
      sample((A[i] - u.uWheatMps.value * t) * tile.x, C[i] * tile.y));
  const solo = (i, t) => u.uWheatJitter.value
      * Math.sin(t * u.uWheatRate.value + stagger(vary[i * 3]));
  const bend = (i, t) => crop[i * 4 + 3] * (u.uWheatBase.value
      + u.uWheatGust.value * wave(i, t) + solo(i, t));
  return { n, u, A, C, wave, solo, bend,
           near: (a, c) => {
             let best = -1, bd = 1e9;
             for (let i = 0; i < n; i++) {
               const d = (A[i] - a) * (A[i] - a) + (C[i] - c) * (C[i] - c);
               if (d < bd) { bd = d; best = i; }
             }
             return best;
           } };
};
const pearson = (a, b, off, n) => {
  let sa = 0, sb = 0;
  for (let k = 0; k < n; k++) { sa += a[k]; sb += b[k + off]; }
  sa /= n; sb /= n;
  let s = 0, va = 0, vb = 0;
  for (let k = 0; k < n; k++) {
    const x = a[k] - sa, y = b[k + off] - sb;
    s += x * y; va += x * x; vb += y * y;
  }
  return s / Math.sqrt(Math.max(va * vb, 1e-12));
};
"""


def _wrapper_json(out: str) -> dict:
    """The JSON line check_shaders prints after its human summary."""
    return json.loads(out.strip().splitlines()[-1])


def test_all_three_compile_on_the_real_renderer():
    """Three patched built-ins whose vertex bodies replace `transformed` and
    rewrite `vNormal`, plus two custom depth materials so the shadows follow
    the displacement, plus a vertex TEXTURE fetch.  Only the GPU can say
    that GLSL is legal."""
    _, out = compile_scene(_SCENE, _LIBS)
    report = _wrapper_json(out)
    # Every finding that is not glsl_audit's uTime pair.  patchStandard
    # DECLARES and BINDS uTime itself (shader.js `withTime`), which the
    # static audit cannot see: it fires on six shipped lib modules,
    # cloth.js among them, and is reported to the foundations agent.  A
    # third kind here is a real one.
    stale = {"undeclared_uniform", "unbound_uniform"}
    real = [e for e in report["errors"] if e["kind"] not in stale]
    assert real == [], out
    for e in report["errors"]:
        assert e["file"].endswith("cloth.js"), out
    # A WARN here is the fog trap or the GTAO trap: both ship an effect that
    # looks wrong rather than one that errors.
    assert report["warnings"] == [], out
    # It got as far as the GPU, and built more programs than it has
    # materials: the three custom depth materials are the shadow half of
    # this library and compile separately.
    #
    # `custom_materials` counts every material with captured sources: the three
    # cloths.  (It read six until 2026-09-22 because shader_probe compiled with the
    # harness's post chain ON and every patched material compiled twice; the probe
    # now runs raw, like the build's own compile.)  Asserted as a floor: the exact
    # number also moves when a sibling library staged into this scene patches one
    # more material.
    comp = report["compile"]
    assert comp and comp["custom_materials"] >= 3, out
    assert comp["programs"] >= 7, out


def test_the_flag_is_pinned_at_the_mast_and_free_at_the_fly():
    """The one cue that says "held at an edge".  The envelope is CPU data,
    not GLSL, so this reads the exact numbers the vertex shader multiplies
    its travelling sine by: zero along the hoist, largest at the fly, and
    GROWING — a linear ramp reads as a hinge."""
    out = measure("""
import { makeFlag } from './lib/cloth.js';
const shape = (opts) => {
  const f = makeFlag(Object.assign({ width: 2, height: 1.2, seed: 3 },
                                   opts));
  const m = f.getObjectByName('FlagSheet');
  const a = m.geometry.attributes.aFlag.array;
  const p = m.geometry.attributes.position;
  const u = m.material.userData.uniforms;
  const rows = new Map();
  for (let i = 0; i < p.count; i++) {
    rows.set(a[i * 4].toFixed(6), [a[i * 4], a[i * 4 + 1]]);
  }
  const list = [...rows.values()].sort((x, y) => x[0] - y[0]);
  let mono = true;
  for (let i = 1; i < list.length; i++) {
    if (list[i][1] < list[i - 1][1] - 1e-9) mono = false;
  }
  const at = (q) => list.reduce(
      (b, r) => Math.abs(r[0] - q) < Math.abs(b[0] - q) ? r : b, list[0]);
  const amp = u.uFlagAmp.value;
  return {
    cols: list.length, mono,
    uMin: list[0][0], uMax: list[list.length - 1][0],
    envMast: list[0][1], envFly: list[list.length - 1][1],
    metres: [0, 0.25, 0.5, 0.75, 1].map((q) => amp * at(q)[1]),
    amp, sag: u.uFlagSag.value, rate: u.uFlagRate.value,
    k: u.uFlagK.value, shrink: u.uFlagShrink.value,
    yaw: f.getObjectByName('FlagFly').rotation.y,
  };
};
console.log(JSON.stringify({
  base: shape({ wind: { dir: [1, 0], strength: 1 } }),
  hard: shape({ wind: { dir: [1, 0], strength: 2 } }),
  calm: shape({ wind: { dir: [1, 0], strength: 0.2 } }),
  east: shape({ wind: { dir: [0, 1], strength: 1 } }),
}));
""", _LIBS)
    b = out["base"]
    assert b["cols"] > 40, out
    assert b["uMin"] < 1e-6 and b["uMax"] == 1, out
    # Pinned at the mast, free at the fly.
    assert b["envMast"] < 1e-9, out
    assert b["envFly"] > 1 - 1e-6, out
    assert b["mono"], out
    m = b["metres"]
    assert m[0] < 1e-9, out
    assert m[4] > 0.15, out
    # GROWING, not ramping: half way out the sheet carries well under a
    # third of the fly's swing.
    assert m[2] < 0.34 * m[4], out
    assert m[4] / m[2] > 2.9, out
    for i in range(4):
        assert m[i] < m[i + 1], out
    # The wave TRAVELS: a phase that runs over the chord at a rate.
    assert b["k"] > 5 and b["rate"] > 5, out
    # A slack flag hangs and a taut one flies, on the one wind option.
    assert out["hard"]["amp"] > 1.9 * b["amp"], out
    assert out["calm"]["sag"] > 2.5 * out["hard"]["sag"], out
    # The sheet is TURNED to the wind; the shader carries no direction.
    assert abs(b["yaw"]) < 1e-9, out
    assert abs(out["east"]["yaw"] + 1.5707963) < 1e-6, out


def test_the_banner_hangs_from_its_top_edge():
    """A hung cloth swings about the edge it is hung from, so the swing
    envelope is zero there and full at the hem, and the rate is the pendulum
    its own drop makes it — which is what separates a heavy curtain from a
    flag, and a long curtain from a short one."""
    out = measure("""
import { makeBanner, makeFlag } from './lib/cloth.js';
const b = makeBanner({ width: 2, drop: 3, hang: 4, seed: 5,
                       wind: { dir: [0, 1], strength: 1 } });
const cloth = b.getObjectByName('BannerCloth');
const g = cloth.geometry, a = g.attributes.aBan.array;
const p = g.attributes.position;
let topS = -1, hemS = 2, topY = -1e9, botY = 1e9, worst = 0;
for (let i = 0; i < p.count; i++) {
  const y = p.getY(i), s = a[i * 4];
  topY = Math.max(topY, y); botY = Math.min(botY, y);
  if (y > -1e-9) topS = Math.max(topS, s);
  if (y < -3 + 1e-9) hemS = Math.min(hemS, s);
  worst = Math.max(worst, Math.abs(s + y / 3));
}
const u = cloth.material.userData.uniforms;
const rateOf = (d) => makeBanner({ drop: d }).getObjectByName('BannerCloth')
    .material.userData.uniforms.uBanRate.value;
const flapRate = makeFlag({ width: 2, height: 1.2 })
    .getObjectByName('FlagSheet').material.userData.uniforms
    .uFlagRate.value;
console.log(JSON.stringify({
  topS, hemS, topY, botY, worst,
  hang: b.getObjectByName('BannerHang').position.y,
  yaw: b.getObjectByName('BannerHang').rotation.y,
  rod: !!b.getObjectByName('BannerRod'),
  noRod: !makeBanner({ rod: false }).getObjectByName('BannerRod'),
  rate: u.uBanRate.value, lean: u.uBanLean.value, sway: u.uBanSway.value,
  lag: u.uBanLag.value, flapRate,
  rate1: rateOf(1), rate4: rateOf(4),
}));
""", _LIBS)
    # Zero swing at the hanging edge, full swing at the hem.
    assert out["topS"] == 0, out
    assert out["hemS"] == 1, out
    assert out["worst"] < 1e-6, out
    # The cloth hangs BELOW the point it is hung from, and that point is
    # `hang` above the group origin.
    assert out["topY"] == 0 and out["botY"] == -3, out
    assert out["hang"] == 4, out
    assert out["rod"] and out["noRod"], out
    # A pendulum, not a flap: sqrt(g / drop), and a quarter of the rate the
    # same library flaps a flag at.
    assert abs(out["rate"] - (9.81 / 3) ** 0.5) < 1e-9, out
    assert abs(out["rate1"] / out["rate4"] - 2) < 1e-6, out
    assert out["rate"] < 0.25 * out["flapRate"], out
    # It leans downwind and swings about that lean, and the hem arrives
    # after the rod.
    assert out["lean"] > 0 and out["sway"] > 0 and out["lag"] > 0, out
    assert abs(out["yaw"]) < 1e-9, out


def test_the_banner_folds_run_down_from_where_it_is_hung():
    """The fold profile, its slope and the width it gathers are all
    functions of x ALONE — held per vertex, so a column of the cloth carries
    one fold value from the rod to the hem.  Anything that varies down the
    drop is a diagonal ripple, which is a flag's cue and not a curtain's."""
    out = measure("""
import { makeBanner } from './lib/cloth.js';
const read = (folds) => {
  const cloth = makeBanner({ width: 2.4, drop: 3, folds, seed: 5 })
      .getObjectByName('BannerCloth');
  const g = cloth.geometry, a = g.attributes.aBan.array;
  const p = g.attributes.position;
  const cols = new Map();
  for (let i = 0; i < p.count; i++) {
    const key = p.getX(i).toFixed(6);
    if (!cols.has(key)) cols.set(key, []);
    cols.get(key).push([a[i * 4 + 1], a[i * 4 + 2], a[i * 4 + 3]]);
  }
  let spread = 0;
  for (const rows of cols.values()) {
    for (let k = 0; k < 3; k++) {
      const v = rows.map((r) => r[k]);
      spread = Math.max(spread, Math.max(...v) - Math.min(...v));
    }
  }
  const xs = [...cols.keys()].map(Number).sort((x, y) => x - y);
  const line = xs.map((x) => cols.get(x.toFixed(6))[0][0]);
  let crossings = 0, peak = 0;
  for (let i = 1; i < line.length; i++) {
    if (line[i] * line[i - 1] < 0) crossings++;
    peak = Math.max(peak, Math.abs(line[i]));
  }
  return { spread, crossings, peak, cols: xs.length,
           fold: cloth.material.userData.uniforms.uBanFold.value };
};
console.log(JSON.stringify({ f3: read(3), f5: read(5), f8: read(8) }));
""", _LIBS)
    for key, folds in (("f3", 3), ("f5", 5), ("f8", 8)):
        r = out[key]
        # ONE fold value per column, from the rod to the hem.
        assert r["spread"] < 1e-7, (key, out)
        # Real folds, and as many of them as were asked for.
        assert 0.75 < r["peak"] <= 1.01, (key, out)
        assert 2 * folds - 2 <= r["crossings"] <= 2 * folds + 6, (key, out)
    # More folds in the same width means SHALLOWER ones, or the cloth
    # gathers back more than its own span.
    assert out["f8"]["fold"] < out["f3"]["fold"], out


def test_the_wheat_wave_crosses_the_field_not_each_stalk_alone():
    """The claim the whole library turns on.  Two stalks a metre apart move
    as one; two fifteen metres apart move with the lag the stated travel
    speed puts between them, and at ZERO lag they disagree.  Per-stalk
    motion is a fraction of the shared wave — reverse that and a hectare of
    crop boils instead of rippling."""
    out = measure("""
import { makeWheatField } from './lib/cloth.js';
""" + _WHEAT_READER + """
const f = makeWheatField({ extent: 60, density: 12, height: 1.1, seed: 7,
                           waveMps: 7, maxStalks: 60000,
                           wind: { dir: [1, 0], strength: 1, speed: 1 } });
const r = reader(f.getObjectByName('Stalks'));
const DT = 0.02, N = 240, LAGS = 200;
const series = (fn, i, n) => {
  const s = new Float64Array(n);
  for (let k = 0; k < n; k++) s[k] = fn(i, k * DT);
  return s;
};
const varOf = (s) => {
  let m = 0;
  for (const v of s) m += v;
  m /= s.length;
  let q = 0;
  for (const v of s) q += (v - m) * (v - m);
  return q / s.length;
};
const median = (v) => v.slice().sort((a, b) => a - b)[v.length >> 1];
// Sample where the wave IS: a crop between crests is genuinely still, and
// stillness has nothing to correlate.  Every seed puts its calm patches
// somewhere else, so the calm ones are dropped by measurement and not by
// being chosen around.
const spots = [];
for (let a = -18; a <= 18; a += 6) {
  for (let c = -12; c <= 12; c += 12) spots.push([a, c]);
}
const pairs = (gap) => {
  const out = [];
  for (const [a0, c0] of spots) {
    const p = r.near(a0, c0), q = r.near(r.A[p] + gap, r.C[p]);
    if (varOf(series(r.wave, p, N)) < 0.01) continue;
    const sa = series(r.bend, p, N), sb = series(r.bend, q, N + LAGS);
    let best = -2, bestLag = 0;
    for (let l = 0; l <= LAGS; l++) {
      const c = pearson(sa, sb, l, N);
      if (c > best) { best = c; bestLag = l * DT; }
    }
    const d = r.A[q] - r.A[p];
    out.push({ d, best, bestLag, zero: pearson(sa, sb, 0, N),
               travel: bestLag > 1e-9 ? d / bestLag : 1e9 });
  }
  return {
    n: out.length,
    best: median(out.map((o) => o.best)),
    lag: median(out.map((o) => o.bestLag)),
    zero: median(out.map((o) => o.zero)),
    travel: median(out.map((o) => o.travel)),
  };
};
const mid = r.near(0, 0);
const waveVar = varOf(series((i, t) => r.u.uWheatGust.value * r.wave(i, t),
                             mid, N));
const soloVar = varOf(series(r.solo, mid, N));
console.log(JSON.stringify({
  mps: r.u.uWheatMps.value, n: r.n, spots: spots.length,
  gust: r.u.uWheatGust.value, jitter: r.u.uWheatJitter.value,
  waveVar, soloVar, near: pairs(1.5), far: pairs(15),
}));
""", _LIBS)
    assert out["n"] > 5000, out
    assert out["near"]["n"] >= 8 and out["far"]["n"] >= 8, out
    # A metre and a half apart is a metre and a half apart: they move as one
    # field, at the travel lag and very nearly at none.
    assert out["near"]["best"] > 0.90, out
    assert out["near"]["zero"] > 0.70, out
    # Fifteen metres downwind, the SAME motion arrives later — and at the
    # same instant the two disagree.
    far = out["far"]
    assert far["best"] > 0.88, out
    assert far["lag"] > 0.1, out
    assert abs(far["travel"] - out["mps"]) < 0.15 * out["mps"], out
    assert far["zero"] < 0.35, out
    # The wave is the effect; the stalk is the texture that shows it.
    assert out["jitter"] < 0.15 * out["gust"], out
    assert out["soloVar"] < 0.05 * out["waveVar"], out


def test_the_wheat_is_sown_in_rows_and_the_grass_is_not():
    """A crop was planted by a machine, and the drill rows are the cue no
    meadow has — the difference between this library and a taller copy of
    grass.js.  Measured as the strength of the `rowGap` period in the stalk
    positions, over every possible sowing angle."""
    out = measure("""
import { makeWheatField } from './lib/cloth.js';
import { makeGrass } from './lib/grass.js';
const rowiness = (pts, gap) => {
  let best = 0, bestAng = 0;
  for (let a = 0; a < 180; a++) {
    const th = a * Math.PI / 180;
    const nx = Math.cos(th), nz = Math.sin(th);
    let sr = 0, si = 0;
    for (let i = 0; i < pts.length; i += 3) {
      const p = (pts[i] * nx + pts[i + 2] * nz) / gap * 2 * Math.PI;
      sr += Math.cos(p); si += Math.sin(p);
    }
    const m = Math.hypot(sr, si) / (pts.length / 3);
    if (m > best) { best = m; bestAng = a; }
  }
  return { best, bestAng };
};
const wheat = makeWheatField({ extent: 12, density: 40, seed: 7,
                               rowGap: 0.18 })
    .getObjectByName('Stalks').geometry.attributes.iPos.array;
const grass = makeGrass({ extent: 12, density: 40, seed: 7 })
    .getObjectByName('Blades').geometry.attributes.iPos.array;
console.log(JSON.stringify({
  wheat: rowiness(wheat, 0.18),
  wheatWide: rowiness(
      makeWheatField({ extent: 12, density: 40, seed: 7, rowGap: 0.3 })
          .getObjectByName('Stalks').geometry.attributes.iPos.array, 0.3),
  grass: rowiness(grass, 0.18),
  nWheat: wheat.length / 3, nGrass: grass.length / 3,
}));
""", _LIBS)
    # Sown: nearly every stalk sits on a line at the drill spacing.
    assert out["wheat"]["best"] > 0.6, out
    assert out["wheatWide"]["best"] > 0.6, out
    # Scattered: grass has no such period at all.
    assert out["grass"]["best"] < 0.15, out
    assert out["nWheat"] > 1000 and out["nGrass"] > 1000, out


def test_one_wind_option_moves_the_wheat_and_the_grass_alike():
    """`windOf` is imported from grass.js rather than re-read, so a scene
    that hands one option to its meadow and its crop gets one wind: the same
    direction, and a strength that scales both."""
    out = measure("""
import { makeGrass } from './lib/grass.js';
import { makeWheatField } from './lib/cloth.js';
const read = (wind) => {
  const g = makeGrass({ extent: 6, density: 20, wind })
      .getObjectByName('Blades').material.userData.uniforms;
  const w = makeWheatField({ extent: 6, density: 8, wind })
      .getObjectByName('Stalks').material.userData.uniforms;
  return {
    grassDir: g.uGrassWind.value.toArray(),
    wheatDir: w.uWheatWind.value.toArray(),
    grassAmp: g.uGrassAmp.value, wheatGust: w.uWheatGust.value,
    grassSpeed: g.uGrassSpeed.value, wheatMps: w.uWheatMps.value,
  };
};
const wind = { dir: [0.6, -0.8], strength: 1.4, speed: 1.3 };
console.log(JSON.stringify({
  full: read(wind),
  half: read({ dir: [0.6, -0.8], strength: 0.7, speed: 1.3 }),
  slow: read({ dir: [0.6, -0.8], strength: 1.4, speed: 0.65 }),
  bare: read(1),
}));
""", _LIBS)
    f = out["full"]
    # One direction, to the last bit.
    assert f["grassDir"] == f["wheatDir"], out
    assert abs(f["grassDir"][0] - 0.6) < 1e-6, out
    assert out["bare"]["grassDir"] == out["bare"]["wheatDir"], out
    # One strength: halve it and both fields bend half as far.
    assert abs(out["half"]["grassAmp"] / f["grassAmp"] - 0.5) < 1e-6, out
    assert abs(out["half"]["wheatGust"] / f["wheatGust"] - 0.5) < 1e-6, out
    # One speed: the crop wave's metres per second rides it.
    assert f["grassSpeed"] == 1.3, out
    assert abs(f["wheatMps"] - 7 * 1.3) < 1e-6, out
    assert abs(out["slow"]["wheatMps"] - 7 * 0.65) < 1e-6, out


def test_the_stated_sphere_covers_every_moving_part():
    """Every moving part lives in the vertex shader, so the bounds are a
    PROMISE the CPU makes: three CULLS on the sphere, and a field that
    leaves the one it states blinks out the moment its origin leaves the
    frame.  The two sheets are checked against their own displacement,
    replayed over a full period; the crop against the fact that a stalk
    bends along an arc of its own length."""
    out = measure("""
import { makeFlag, makeBanner, makeWheatField } from './lib/cloth.js';
const cover = (geo, pts) => {
  const b = geo.boundingBox, s = geo.boundingSphere;
  let box = -1e9, sph = -1e9;
  for (const p of pts) {
    box = Math.max(box, b.min.x - p[0], p[0] - b.max.x,
                   b.min.y - p[1], p[1] - b.max.y,
                   b.min.z - p[2], p[2] - b.max.z);
    sph = Math.max(sph, Math.hypot(p[0] - s.center.x, p[1] - s.center.y,
                                   p[2] - s.center.z) - s.radius);
  }
  return { box, sph, radius: s.radius };
};
const flagPts = (strength) => {
  const m = makeFlag({ width: 2, height: 1.2, seed: 3,
                       wind: { dir: [1, 0], strength } })
      .getObjectByName('FlagSheet');
  const g = m.geometry, u = m.material.userData.uniforms;
  const a = g.attributes.aFlag.array, p = g.attributes.position;
  const pts = [];
  for (let f = 0; f < 24; f++) {
    const t = f / 24 * (2 * Math.PI / u.uFlagRate.value);
    for (let i = 0; i < p.count; i++) {
      const ph = u.uFlagK.value * a[i * 4] - u.uFlagRate.value * t
          + u.uFlagPhase.value + u.uFlagTilt.value * p.getY(i);
      const sN = Math.sin(ph) + 0.42 * Math.sin(1.7 * ph + 1.3);
      pts.push([
        p.getX(i) - Math.min(u.uFlagShrink.value * a[i * 4 + 3],
                             u.uFlagMaxShrink.value),
        p.getY(i) - u.uFlagSag.value * a[i * 4 + 1],
        p.getZ(i) + u.uFlagAmp.value * a[i * 4 + 1] * sN]);
    }
  }
  return { g, pts };
};
const banPts = (strength) => {
  const m = makeBanner({ width: 2, drop: 3, seed: 5,
                         wind: { dir: [0, 1], strength } })
      .getObjectByName('BannerCloth');
  const g = m.geometry, u = m.material.userData.uniforms;
  const a = g.attributes.aBan.array, p = g.attributes.position;
  const pts = [];
  for (let f = 0; f < 24; f++) {
    const t = f / 24 * (2 * Math.PI / u.uBanRate.value);
    for (let i = 0; i < p.count; i++) {
      const s = a[i * 4], e = s * (2 - s);
      const ang = u.uBanLean.value + u.uBanSway.value * Math.sin(
          u.uBanRate.value * t + u.uBanPhase.value - u.uBanLag.value * s);
      const d = u.uBanDrop.value * s;
      pts.push([
        p.getX(i) - Math.max(-u.uBanShrinkMax.value, Math.min(
            u.uBanShrinkMax.value,
            u.uBanShrink.value * a[i * 4 + 3] * e * e)),
        -d * Math.cos(ang),
        d * Math.sin(ang) + u.uBanFold.value * a[i * 4 + 1] * e]);
    }
  }
  return { g, pts };
};
const wheat = makeWheatField({ extent: 20, density: 20, height: 1.2,
                               heightAt: (x, z) => 0.3 * Math.sin(x * 0.2),
                               seed: 4 }).getObjectByName('Stalks');
const wp = wheat.geometry.attributes.iPos.array;
const wc = wheat.geometry.attributes.iCrop.array;
const wpts = [];
for (let i = 0; i < wheat.geometry.instanceCount; i++) {
  const x = wp[i * 3], y = wp[i * 3 + 1], z = wp[i * 3 + 2];
  const h = wc[i * 4];
  wpts.push([x, y, z], [x + h, y, z], [x - h, y, z],
            [x, y, z + h], [x, y, z - h], [x, y + h, z]);
}
const f1 = flagPts(1), f2 = flagPts(2.5);
const b1 = banPts(1), b2 = banPts(2.5);
console.log(JSON.stringify({
  flag: cover(f1.g, f1.pts), flagHard: cover(f2.g, f2.pts),
  banner: cover(b1.g, b1.pts), bannerHard: cover(b2.g, b2.pts),
  wheat: cover(wheat.geometry, wpts),
  nWheat: wheat.geometry.instanceCount,
}));
""", _LIBS)
    for key in ("flag", "flagHard", "banner", "bannerHard", "wheat"):
        assert out[key]["box"] <= 1e-6, (key, out)
        assert out[key]["sph"] <= 1e-6, (key, out)
        # A 1e4 placeholder would "cover" anything and cull nothing.
        assert out[key]["radius"] < 100, (key, out)
    assert out["flag"]["radius"] < 3, out
    assert out["nWheat"] > 3000, out


def test_two_fields_with_one_seed_are_identical():
    """Determinism is the contract for every shipped factory: a re-run that
    reshuffles the crop is a re-run nobody can compare against.  The gust
    texture is included, because grass.js's gusts come from GLSL_UTIL's
    UNSEEDED noise — every grass field in every scene gusts identically, and
    nothing on the CPU can read what the shader will see."""
    out = measure("""
import { makeBanner, makeWheatField } from './lib/cloth.js';
const banner = (s) => JSON.stringify(Array.from(
    makeBanner({ width: 2, drop: 3, seed: s })
        .getObjectByName('BannerCloth').geometry.attributes.aBan.array));
const stalks = (s) => JSON.stringify(['iPos', 'iCrop', 'iVar'].map((k) =>
    Array.from(makeWheatField({ extent: 8, density: 20, seed: s })
        .getObjectByName('Stalks').geometry.attributes[k].array)));
const gust = (s) => JSON.stringify(Array.from(
    makeWheatField({ extent: 4, density: 4, seed: s })
        .getObjectByName('Stalks').material.userData.uniforms
        .uWheatWave.value.image.data));
console.log(JSON.stringify({
  bannerSame: banner(4) === banner(4), bannerDiffer: banner(4) !== banner(9),
  stalksSame: stalks(4) === stalks(4), stalksDiffer: stalks(4) !== stalks(9),
  gustSame: gust(4) === gust(4), gustDiffer: gust(4) !== gust(9),
  n: JSON.parse(stalks(4))[0].length,
}));
""", _LIBS)
    assert out["bannerSame"] and out["bannerDiffer"], out
    assert out["stalksSame"] and out["stalksDiffer"], out
    # A seeded gust field, so two fields in one scene do not gust in
    # lockstep and a test can read what the GPU will sample.
    assert out["gustSame"] and out["gustDiffer"], out
    assert out["n"] > 3000, out


def test_tick_advances_every_material_including_the_shadows():
    """Every moving part lives in the vertex shader, so an un-advanced uTime
    is a photograph of a model.  The two custom depth materials share the
    surface's uniform map on purpose: patch them with their own and the
    shadow freezes at t = 0 while the cloth keeps moving, which is worse
    than casting no shadow at all."""
    out = measure("""
import * as THREE from 'three';
import { makeFlag, makeBanner, makeWheatField } from './lib/cloth.js';
const g = new THREE.Group();
g.add(makeFlag({ seed: 2 }));
g.add(makeBanner({ seed: 2 }));
g.add(makeWheatField({ extent: 4, density: 10, seed: 2 }));
const times = () => {
  const out = [];
  g.traverse((o) => {
    for (const m of [].concat(o.material || [],
                              o.customDepthMaterial || [])) {
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
console.log(JSON.stringify({ before, after: times(), n: before.length }));
""", _LIBS)
    # Three surfaces plus two shadow materials.
    assert out["n"] == 5, out
    assert out["before"] == [0] * 5, out
    assert out["after"] == [2.5] * 5, out


def test_wheat_shadows_are_opt_in_and_displaced_when_on():
    """Same call grass.js makes: 70k stalks in the shadow map are a real
    SwiftShader cost, so the crop casts only when asked — and then only
    through a depth material carrying the SAME wave displacement on the
    surface's own uniform map (its vertex texture fetch included), or the
    crop's shadow is nothing / a frozen field."""
    out = measure("""
import { makeWheatField } from './lib/cloth.js';
const pick = (g) => {
  let m;
  g.traverse((o) => { if (o.name === 'Stalks') m = o; });
  return m;
};
const off = pick(makeWheatField({ extent: 8, density: 30, seed: 3 }));
const on = pick(makeWheatField({ extent: 8, density: 30, seed: 3,
                                 shadows: true }));
console.log(JSON.stringify({
  offCasts: off.castShadow, offDepth: !!off.customDepthMaterial,
  onCasts: on.castShadow,
  onShared: !!on.customDepthMaterial
      && on.customDepthMaterial.userData.uniforms
          === on.material.userData.uniforms,
  waveInMap: !!(on.customDepthMaterial
      && on.customDepthMaterial.userData.uniforms.uWheatWave),
}));
""", _LIBS)
    assert not out["offCasts"] and not out["offDepth"], out
    assert out["onCasts"] and out["onShared"] and out["waveInMap"], out


def test_every_cloth_albedo_survives_this_renderer_s_shade():
    """OURS, not the reference's: the grading pass for a host that runs ACES
    at exposure 1.0 with no post chain.

    Three claims, all of them numbers.  (1) No shipped default has a channel
    under 0.02 in LINEAR — the reference's flag red and banner crimson sat at
    0.021 and 0.019, so every part of a sheet the sun missed tone-mapped to a
    black-red hole with no cloth in it, and none of them is over 0.8 either.
    (2) Every effect carries HUE variance rather than one flat hex: the flag
    and the banner break their dye across the sheet, and the crop ripens in
    patches from a field-scale term the stalk's own scatter rides on.  (3)
    The fuzz that lights a turning edge is a real term on all three."""
    out = measure("""
import * as THREE from 'three';
import { makeFlag, makeBanner, makeWheatField } from './lib/cloth.js';
const lin = (c) => [c.r, c.g, c.b];
const flag = makeFlag({ seed: 1 }).getObjectByName('FlagSheet');
const ban = makeBanner({ seed: 1 }).getObjectByName('BannerCloth');
const wheat = makeWheatField({ extent: 6, density: 8, seed: 1 })
    .getObjectByName('Stalks');
const fu = flag.material.userData.uniforms;
const bu = ban.material.userData.uniforms;
const wu = wheat.material.userData.uniforms;
console.log(JSON.stringify({
  flagRGB: lin(flag.material.color),
  banRGB: lin(ban.material.color),
  strawRGB: lin(wu.uWheatStraw.value),
  earRGB: lin(wu.uWheatEar.value),
  dye: [fu.uFlagDye.value, bu.uBanDye.value],
  fade: fu.uFlagFade.value, dust: bu.uBanDust.value,
  sheen: [fu.uFlagSheen.value, bu.uBanSheen.value, wu.uWheatEdge.value],
  patch: wu.uWheatPatch.value, ripen: wu.uWheatRipen.value,
  // The tinted default is a DEFAULT: a caller's colour still wins.
  custom: lin(makeFlag({ color: 0x123456 })
      .getObjectByName('FlagSheet').material.color),
}));
""", _LIBS)
    for key in ("flagRGB", "banRGB", "strawRGB", "earRGB"):
        for ch in out[key]:
            assert 0.02 <= ch <= 0.8, (key, out)
    # Cloth is dyed, not printed: both sheets break their hue across
    # themselves, at a swing wide enough to see (astraHueBreak's own
    # calibration note puts anything under ~0.3 below the visible floor).
    assert min(out["dye"]) >= 0.3, out
    assert out["fade"] > 0 and out["dust"] > 0, out
    # And the crop ripens in PATCHES: a field-scale term, so a stalk's
    # neighbours are near it in tone rather than random.
    assert 0.01 < out["patch"] < 0.5, out
    assert out["ripen"] > 0, out
    # The fuzz that lights a turning edge — present, and never a rim so hot
    # it doubles the albedo.
    for s in out["sheen"]:
        assert 0.1 < s < 1.0, out
    assert [round(c, 6) for c in out["custom"]] != [
        round(c, 6) for c in out["flagRGB"]], out
