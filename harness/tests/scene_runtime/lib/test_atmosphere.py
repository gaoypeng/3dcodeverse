"""atmosphere.js — regression: the fog bank was a baked daylight white, a slab of milk
on a night rig."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node


def test_the_bank_takes_its_colour_from_the_scene_it_stands_in():
    """At 0xdce9f2 the bank rendered the ground under it (69,66,64) against a
    (26,36,64) night sky.  Its colour is the scene's own air: the graded sky
    `sunRig` bakes into `scene.environment`; an explicit `color` still wins."""
    out = measure("""
import * as THREE from 'three';
import { makeHeightFog } from './lib/atmosphere.js';
import { sunRig } from './lib/environment.js';
const lum = (c) => 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
const color = (elevation, fogHex, o = {}) => {
  const s = new THREE.Scene();
  const rig = sunRig({ azimuth: 215, elevation });
  s.add(rig.sun, rig.fill);
  s.environment = rig.envTex;
  s.fog = new THREE.FogExp2(fogHex, 0.0035);
  return makeHeightFog(Object.assign({ scene: s, height: 2.2 }, o)).children[0].material.uniforms.uColor.value;
};
const day = color(38, 0xcfd8e6), night = color(-20, 0x0b0f1a);
console.log(JSON.stringify({
  day: lum(day), night: lum(night), fog: lum(new THREE.Color(0x0b0f1a)),
  nightRB: night.r / night.b, forced: color(-20, 0x0b0f1a, { color: 0xff0000 }).toArray(),
}));
""", ("atmosphere.js", "environment.js", "sky.js"))
    assert out["day"] > 0.5 and 0.02 < out["night"] < 0.08, out
    assert out["night"] > out["fog"] * 4 and out["nightRB"] < 0.75, out  # lit, and blue
    assert out["forced"][0] > 0.9 and out["forced"][2] < 0.05, out
