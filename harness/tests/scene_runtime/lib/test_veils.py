"""veils.js (15 recorded scenes import it): a veil is lit by the scene it hangs in, and
none of the three fields is a wall to a depth or shadow pass."""

from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import measure

pytestmark = pytest.mark.node


def test_no_veil_is_a_wall_to_a_depth_or_shadow_pass():
    """An override pass (GTAO) draws a stack of transparent sheets as solid
    floors.  Every other veil in the library was guarded; these three were not."""
    out = measure("""
import { makeRainVeil, makeSnowfall, makeMotes } from './lib/veils.js';
const out = {};
for (const [k, g, name] of [['veil', makeRainVeil({ extent: 90, height: 40 }), 'RainSheets'],
                            ['snow', makeSnowfall({ extent: 36, height: 16 }), 'Snowflakes'],
                            ['mote', makeMotes({ extent: 5, height: 3 }), 'DustMotes']]) {
  const m = g.getObjectByName(name);
  out[k] = [!!m.userData.astraNoOverride, m.castShadow === true];
}
console.log(JSON.stringify(out));
""", ("shader.js", "veils.js"))
    for k, (guarded, casts) in out.items():
        assert guarded and not casts, k


def test_a_veil_carries_the_light_of_the_scene_it_hangs_in():
    """An unlit pale card is the brightest thing in any frame that is not
    daylight (a moonlit rain curtain measured +0.189 luminance over a 0.170
    scene).  The probe answers with the irradiance where the field hangs,
    normalised so the library's own day rig is 1.0."""
    out = measure("""
import * as THREE from 'three';
import { makeRainVeil, makeSnowfall, makeMotes } from './lib/veils.js';
import { sunRig } from './lib/environment.js';
// the light probe hangs off onBeforeRender, the only place a Mesh is handed its scene
const R = { info: { render: { frame: 0 } } };
const lit = (field, scene, frame) => {
  scene.add(field);
  scene.updateMatrixWorld(true);
  R.info.render.frame = frame;
  const m = field.children[0];
  m.onBeforeRender(R, scene, new THREE.PerspectiveCamera(), m.geometry, m.material, null);
  return m.material.uniforms.uLight.value.toArray();
};
const rigScene = (elev, fog) => {
  const s = new THREE.Scene();
  const rig = sunRig({ azimuth: 215, elevation: elev });
  s.add(rig.sun, rig.fill);
  s.fog = new THREE.FogExp2(fog, 0.0035);
  return s;
};
const night = rigScene(-20, 0x0b0f1a);
console.log(JSON.stringify({
  day: lit(makeRainVeil({ extent: 70 }), rigScene(38, 0xcfd8e6), 100),
  night: lit(makeRainVeil({ extent: 70 }), night, 200),
  snow: lit(makeSnowfall({ extent: 30, height: 14 }), night, 200),
  mote: lit(makeMotes({ extent: 5, height: 3 }), night, 200),
}));
""", ("shader.js", "veils.js", "environment.js", "sky.js"))
    day, night = out["day"], out["night"]
    assert all(0.85 <= c <= 1.25 for c in day), day      # daylight unchanged
    assert max(night) < 0.55 * min(day) and night[2] > night[0] * 1.3, out  # dark, and blue
    assert max(out["snow"]) < 0.55 * min(day) and max(out["mote"]) < 0.7 * min(day), out
