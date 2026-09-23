"""figure.js — regressions: carry() put the load on the head and the hands behind
the body, and an unset `skin` rendered every face as a white ball."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("figure.js", "materials.js")


@pytest.fixture(scope="module")
def m() -> dict:
    return measure("""
import * as THREE from 'three';
import { figure, carry } from './lib/figure.js';
const find = (root, name) => { let hit = null; root.traverse((o) => { if (o.name === name) hit = o; }); return hit; };
const c = figure({ height: 1.72 });
const held = new THREE.Object3D();
carry(c, held);
c.updateMatrixWorld(true);
held.updateMatrixWorld(true);
const hand = new THREE.Vector3().setFromMatrixPosition(find(c, 'handL').matrixWorld);
const heldW = new THREE.Vector3().setFromMatrixPosition(held.matrixWorld);
const skin = find(figure({ height: 1.72 }), 'head').material.color;
console.log(JSON.stringify({
  handY: hand.y, handZ: hand.z, heldY: heldW.y, heldZ: heldW.z,
  headMinY: new THREE.Box3().setFromObject(find(c, 'head')).min.y,
  skinPeak: Math.max(skin.r, skin.g, skin.b), skinWarm: skin.r - skin.b,
}));
""", _LIBS)


def test_carry_hands_land_on_the_held_object(m):
    """The old elbow sign left the hands BEHIND the body plane."""
    assert m["handZ"] < -0.25 and 0.80 < m["handY"] < 1.10, m


def test_the_held_object_sits_at_the_hands_not_on_the_head(m):
    """`held` is parented to the waist pivot and was offset by a figure-root
    height as well: the render showed a crate balanced on the carrier's head."""
    assert 0 < m["heldY"] - m["handY"] < 0.12, m
    assert m["heldY"] < m["headMinY"] and m["heldZ"] < -0.25, m


def test_skin_is_never_the_undefined_white_ball(m):
    """`MAT.skin({ color: opts.skin })` with no `skin` copied `color: undefined`
    over the default — albedo 1.0 on every face and hand."""
    assert m["skinPeak"] < 0.72 and m["skinWarm"] > 0.05, m
