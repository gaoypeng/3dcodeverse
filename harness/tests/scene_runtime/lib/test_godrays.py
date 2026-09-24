"""godrays.js (8 recorded scenes import it) — regressions: a NaN bounding sphere, an
additive default that blew out daylight frames, and a low sun stretching shafts into spears."""

from __future__ import annotations

from tests.scene_runtime.lib._probe import measure

_LIBS = ("shader.js", "godrays.js")

# Per-shaft end rings, recovered from the merged geometry: uv.y is 0 at the
# gap the light comes through and 1 where it lands, and aShaft tags which
# shaft a vertex belongs to.
_RINGS = """
function rings(mesh) {
  const p = mesh.geometry.attributes.position.array;
  const uv = mesh.geometry.attributes.uv.array;
  const tag = mesh.geometry.attributes.aShaft.array;
  const acc = new Map();
  for (let i = 0; i < tag.length; i++) {
    const v = uv[i * 2 + 1];
    const end = v < 1e-6 ? 'top' : (v > 1 - 1e-6 ? 'bot' : null);
    if (!end) continue;
    const k = tag[i].toFixed(4);
    if (!acc.has(k)) acc.set(k, { top: [], bot: [] });
    acc.get(k)[end].push([p[i * 3], p[i * 3 + 1], p[i * 3 + 2]]);
  }
  const mean = (pts) => pts.reduce(
      (a, q) => [a[0] + q[0] / pts.length, a[1] + q[1] / pts.length,
                 a[2] + q[2] / pts.length], [0, 0, 0]);
  const radius = (pts, c) => pts.reduce(
      (a, q) => a + Math.hypot(q[0] - c[0], q[1] - c[1], q[2] - c[2])
          / pts.length, 0);
  return [...acc.values()].map((r) => {
    const t = mean(r.top), b = mean(r.bot);
    const d = [b[0] - t[0], b[1] - t[1], b[2] - t[2]];
    const L = Math.hypot(d[0], d[1], d[2]);
    return {
      axis: [d[0] / L, d[1] / L, d[2] / L],
      run: Math.hypot(d[0], d[2]),
      rTop: radius(r.top, t), rBot: radius(r.bot, b),
    };
  });
}
"""


def test_the_instanced_fields_state_a_real_bounding_sphere():
    """The stated radius was `run + side * 2` on two Vector3s — the string
    "[object Object]NaN" — so both instanced fields shipped a NaN sphere."""
    out = measure("""
import { makeGodRays } from './lib/godrays.js';
const g = makeGodRays({ count: 5, height: 8, width: 0.9, seed: 2 });
const r = (n) => g.getObjectByName(n).geometry.boundingSphere.radius;
console.log(JSON.stringify({
  pools: r('Pools'), motes: r('Motes'),
  finite: Number.isFinite(r('Pools')) && Number.isFinite(r('Motes')),
}));
""", _LIBS)
    assert out["finite"], out
    # It has to actually cover the field: the drop alone is 8 m.
    assert out["pools"] > 8 and out["motes"] > 8, out
    # ...and not the 10 km default, which frames a kilometre of empty air.
    assert out["pools"] < 200 and out["motes"] < 200, out


def test_the_default_is_the_mode_that_cannot_wreck_a_frame():
    """A gorge passed `ambient: 0.5` and got the additive build, a white slab
    over daylight; adding light is the exception a caller asks for."""
    out = measure("""
import * as THREE from 'three';
import { makeGodRays } from './lib/godrays.js';
const mode = (o) => makeGodRays(o).getObjectByName('Shafts')
    .material.blending === THREE.NormalBlending;
console.log(JSON.stringify({
  unspecified: mode({ seed: 1 }),
  composerMiddle: mode({ ambient: 0.5, seed: 1 }),
  daylight: mode({ ambient: 1.0, seed: 1 }),
  unlitInterior: mode({ ambient: 0.0, seed: 1 }),
  dimRoom: mode({ ambient: 0.2, seed: 1 }),
}));
""", _LIBS)
    assert out["unspecified"], "a library's default must be the safe one"
    assert out["composerMiddle"], "0.5 is the value a composer actually picks"
    assert out["daylight"]
    assert not out["unlitInterior"] and not out["dimRoom"], (
        "a genuinely dark room still gets added light")


def test_a_low_sun_cannot_stretch_a_shaft_into_a_spear():
    """At an 18 deg sun a 32 m drop became a 103 m shaft: a delivered gorge drew
    five 100 m bars.  The drawn length is capped; only a shaft that still
    reaches the floor throws a pool."""
    out = measure(_RINGS + """
import { makeGodRays } from './lib/godrays.js';
// 18 deg elevation: the delivered gorge's own sun.
const el = 18 * Math.PI / 180;
const sun = [-Math.cos(el), -Math.sin(el), 0];
const full = (g) => {
  const rs = rings(g.getObjectByName('Shafts'));
  return rs.map((r) => r.run / Math.cos(el));
};
const low = makeGodRays({ count: 5, height: 32, width: 0.85,
                          spread: 22, sunDir: sun, seed: 4 });
const wide = makeGodRays({ count: 5, height: 32, width: 0.85,
                           spread: 22, sunDir: sun, seed: 4,
                           maxLength: 200 });
const steep = makeGodRays({ count: 5, height: 32, width: 0.85,
                            spread: 22, sunDir: [0, -1, -0.2], seed: 4 });
console.log(JSON.stringify({
  capped: Math.max(...full(low)),
  uncapped: Math.max(...full(wide)),
  steep: Math.max(...full(steep)),
  cappedNames: low.children.map((c) => c.name).sort(),
  steepNames: steep.children.map((c) => c.name).sort(),
}));
""", _LIBS)
    # 32 / sin(18 deg) = 103 m if nothing caps it; the cap is 1.6x.
    assert out["uncapped"] > 95, out
    assert out["capped"] <= 32 * 1.6 + 1.0, out
    # A shaft that no longer reaches the floor throws no pool...
    assert out["cappedNames"] == ["Motes", "Shafts"], out
    # ...while a steep sun still lands, and keeps its pool.
    assert out["steep"] < 32 * 1.6, out
    assert out["steepNames"] == ["Motes", "Pools", "Shafts"], out




def test_added_light_fades_with_distance_and_never_takes_the_fog_colour():
    """The pools and dust were additive but built without `additive: true`, so each
    faded its alpha AND ran three's fog chunk, which ADDS fog-coloured haze to an
    added light.  Every additive material ends in the alpha fade only."""
    out = measure("""
import * as THREE from 'three';
import { makeGodRays } from './lib/godrays.js';
const g = makeGodRays({ count: 3, height: 6, seed: 2 });
const mats = [];
g.traverse((o) => { if (o.material && o.material.blending === THREE.AdditiveBlending) mats.push({
  name: o.material.name, mix: o.material.fragmentShader.includes('#include <fog_fragment>'),
  fade: o.material.fragmentShader.includes('gl_FragColor.a *= 1.0 - clamp(astraFogF') }); });
console.log(JSON.stringify(mats));
""", _LIBS)
    assert {m["name"] for m in out} >= {"GodRayPool", "GodRayMotes"}, out
    assert all(m["fade"] and not m["mix"] for m in out), out
