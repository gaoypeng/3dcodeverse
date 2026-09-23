"""clouds.js — regression: `sunDir` was documented and ignored, and a set sun lit the
night deck from under the ground."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node


def test_the_layer_is_lit_by_the_rig_and_a_set_sun_becomes_the_moon():
    """`sunRig().sunDir` keeps reporting the authored sun after it sets; passed
    straight through, every night puff is lit from below.  A set sun becomes a
    moon above the horizon, opposite it."""
    m = measure("""
import * as THREE from 'three';
import { makeClouds } from './lib/clouds.js';
const dir = (o) => makeClouds(Object.assign({ seed: 3 }, o)).material.uniforms.uSunDir.value.toArray();
console.log(JSON.stringify({
  day: dir({ sunDir: new THREE.Vector3(-0.6, 0.55, -0.3) }),
  set: dir({ sunDir: new THREE.Vector3(0.62, -0.34, 0.21) }),
  none: dir({}),
}));
""", ("clouds.js",))
    assert m["day"][1] > 0.5, m
    assert m["set"][1] > 0.4 and m["set"][0] < 0, m
    assert m["none"][1] == 1.0, m
