"""canopy.js — regression: leaf transmission kept every crown glowing at night."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "noise.js", "grass.js", "foliage_shade.js", "canopy.js")


def test_transmission_through_a_leaf_goes_out_when_the_sun_does():
    """`patchLeafSSS` adds its glow to the albedo, so a set sun did not turn it
    off.  Full strength for any sun at or above the horizon."""
    out = measure("""
import * as THREE from 'three';
import { makeCanopy } from './lib/canopy.js';
const amt = (el) => {
  const r = el * Math.PI / 180;
  const dir = new THREE.Vector3(Math.cos(r), Math.sin(r), 0.2).normalize();
  return makeCanopy({ leaves: 200, backlit: { sunDir: dir } })
      .getObjectByName('Leaves').material.userData.uniforms.uLeafAmt.value;
};
const off = makeCanopy({ leaves: 200, backlit: false })
    .getObjectByName('Leaves').material.userData.uniforms.uLeafAmt;
console.log(JSON.stringify({ high: amt(38), low: amt(1), night: amt(-20),
                             off: off === undefined }));
""", _LIBS)
    assert out["high"] > 0.4, out
    # Golden hour keeps the whole effect; it is the best moment for it.
    assert abs(out["low"] - out["high"]) < 1e-6, out
    assert out["night"] == 0, out
    # `backlit: false` never installs the patch at all.
    assert out["off"] is True, out
