"""place.js clipPlayer — a Blender hero's keyframed motion reaches the scene (2026-09-07).

The Blender wrapper exports keyframes as a glTF clip, the preload keeps it on the root
(`root.animations`), `.clone()` copies it, and `clipPlayer` drives a mixer by ABSOLUTE time
so the harness's t = 0 / 1.5 s frames are deterministic.  Proven here without Blender: a
synthetic clip (a 2 s rotation loop) on a cloned group.
"""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

PROBE = r"""
import * as THREE from 'three';
import { clipPlayer } from './lib/place.js';
const root = new THREE.Group(); root.name = 'Lantern';
const lamp = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.3, 0.3), new THREE.MeshStandardMaterial()); lamp.name = 'Lamp';
root.add(lamp);
const track = new THREE.NumberKeyframeTrack('Lamp.rotation[x]', [0, 1, 2], [-0.35, 0.35, -0.35]);
root.animations = [new THREE.AnimationClip('Swing', 2, [track])];
const clone = root.clone();
const tick = clipPlayer(clone, { offset: 0 });
const lampC = clone.getObjectByName('Lamp');
const at = (t) => { tick(t); return +lampC.rotation.x.toFixed(3); };
const a0 = at(0), a1 = at(1.0), a15 = at(1.5), again0 = at(0), a4 = at(4.0);
const none = clipPlayer(new THREE.Group());
console.log(JSON.stringify({ kept: clone.animations.length, a0, a1, a15, again0, a4, none: none === null }));
"""


def test_a_clone_keeps_the_clip_and_the_player_is_deterministic_in_t():
    m = measure(PROBE, libs=("place.js",))
    assert m["kept"] == 1 and m["none"] is True, m
    assert m["a0"] == pytest.approx(-0.35, abs=0.02) and m["a1"] == pytest.approx(0.35, abs=0.02), m
    assert abs(m["a15"] - m["a0"]) > 0.15, "the harness's second sample (1.5 s) must differ from t=0"
    assert m["again0"] == m["a0"], "absolute time: the same t gives the same pose"
    assert m["a4"] == pytest.approx(m["a0"], abs=0.02), "a 2 s clip loops"
