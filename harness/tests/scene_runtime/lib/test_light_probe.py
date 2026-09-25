"""Effects must choose a light that actually contributes at their sample point."""
from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import measure

pytestmark = pytest.mark.node


@pytest.fixture(scope="module")
def lights():
    return measure("""
import * as THREE from 'three';
import { makeLightProbe } from './lib/shader.js';
const read = makeLightProbe(), near = new THREE.Vector3();
const selected = (lights) => {
  const scene = new THREE.Scene(); scene.add(...lights);
  const value = read(scene, near);
  return { sun: value.sun?.name ?? null, point: value.point?.name ?? null };
};
const directional = (name, color, intensity) => {
  const light = new THREE.DirectionalLight(color, intensity); light.name = name; return light;
};
const point = (name, color, intensity, x, range = 0, decay = 2) => {
  const light = new THREE.PointLight(color, intensity, range, decay);
  light.position.x = x; light.name = name; return light;
};
const results = {
  directionalColor: selected([directional('red', 0xff0000, 2), directional('green', 0x00ff00, 1)]),
  black: selected([directional('black', 0, 1e6), point('black', 0, 1e6, .1)]),
  pointColor: selected([point('red', 0xff0000, 2, 2), point('green', 0x00ff00, 1, 2)]),
  outsideRange: selected([point('out', 0xffffff, 1e6, 2, 1), point('in', 0xffffff, 1, 3)]),
  smoothRange: selected([point('edge', 0xffffff, 100, 9.99, 10), point('far', 0xffffff, 1, 20)]),
  decay: selected([point('decaying', 0xffffff, 100, 10), point('constant', 0xffffff, 2, 10, 0, 0)]),
};
const scene = new THREE.Scene(), hidden = new THREE.Group(); hidden.visible = false;
hidden.add(directional('hidden', 0xffffff, 1e6), point('hidden', 0xffffff, 1e6, .1));
scene.add(hidden, new THREE.AmbientLight(0xff0000, .5), new THREE.HemisphereLight(0x00ff00, 0x0000ff, 2));
const rig = new THREE.Group(); rig.position.set(3, 4, 5);
const sun = directional('world', 0xffffff, 1); sun.position.set(2, 3, 4);
sun.target.position.set(2, 1, 4); rig.add(sun, sun.target); scene.add(rig);
const value = read(scene, near);
results.world = { sun: value.sun.name, point: value.point?.name ?? null,
  direction: value.sunDirection.toArray(), ambient: value.ambient.toArray(),
  sky: value.sky.toArray(), ground: value.ground.toArray() };
const empty = read(new THREE.Scene());
results.reset = { sun: empty.sun, point: empty.point, direction: empty.sunDirection.toArray(),
  ambient: empty.ambient.toArray(), sky: empty.sky.toArray(), ground: empty.ground.toArray() };
console.log(JSON.stringify(results));
""")


def test_directional_selection_uses_emitted_color(lights):
    assert lights["directionalColor"]["sun"] == "green"
    assert lights["black"] == {"sun": None, "point": None}


@pytest.mark.parametrize("case,expected", [
    ("pointColor", "green"), ("outsideRange", "in"),
    ("smoothRange", "far"), ("decay", "constant"),
])
def test_point_selection_accounts_for_color_range_and_decay(lights, case, expected):
    assert lights[case]["point"] == expected


def test_visible_scene_lights_sum_and_use_world_space(lights):
    world = lights["world"]
    assert world["sun"] == "world" and world["point"] is None
    assert world["direction"] == pytest.approx([0, 1, 0])
    assert world["ambient"] == pytest.approx([.5, 0, 0])
    assert world["sky"] == pytest.approx([0, 2, 0])
    assert world["ground"] == pytest.approx([0, 0, 2])


def test_reused_probe_clears_previous_scene_lights(lights):
    assert lights["reset"] == {"sun": None, "point": None, "direction": [0, 1, 0],
                               "ambient": [0, 0, 0], "sky": [0, 0, 0], "ground": [0, 0, 0]}
