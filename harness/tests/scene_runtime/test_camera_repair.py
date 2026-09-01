"""Deterministic camera repair: the smallest retreat that clears the lens."""

from __future__ import annotations

import pytest

from codeverse.config import get_settings
from tests.scene_runtime.conftest import needs_node, run_node_json

pytestmark = [pytest.mark.node, needs_node]

RUNTIME_JS = get_settings().runtime_js_dir()

JS = """
import * as THREE from 'three';
import { nearGeometry, repairCameraSpec } from './lib/host_metrics.mjs';
const mat = new THREE.MeshStandardMaterial();
const scene = new THREE.Scene();
const ground = new THREE.Mesh(new THREE.PlaneGeometry(60, 60, 2, 2), mat);
ground.name = 'Ground'; ground.rotation.x = -Math.PI / 2; scene.add(ground);
const counter = new THREE.Mesh(new THREE.BoxGeometry(3, 1.1, 1.2), mat);
counter.name = 'BarCounter'; counter.position.set(0, 0.55, 0); scene.add(counter);
scene.updateMatrixWorld(true);
const makeCam = (spec) => {
  const cam = new THREE.PerspectiveCamera(spec.fov || 50, 16 / 9, 0.1, 500);
  cam.position.set(...spec.position);
  cam.lookAt(...spec.lookAt);
  cam.updateMatrixWorld(true);
  return cam;
};
// the fv_izakaya failure shape: an eye INSIDE the counter, looking along it
const stuck = { name: 'PotDetail', position: [0.2, 0.6, 0.1], lookAt: [-1.5, 0.8, -0.5], fov: 45 };
const fix = repairCameraSpec(scene, stuck, THREE, makeCam);
const after = fix ? nearGeometry(scene, makeCam(fix.spec), THREE) : null;
// a camera with open air in front needs no repair
const fine = { name: 'Establishing', position: [10, 4, 10], lookAt: [0, 1, 0], fov: 45 };
const noFix = repairCameraSpec(scene, fine, THREE, makeCam);
console.log(JSON.stringify({ fix, after, noFix }));
"""


@pytest.fixture(scope="module")
def result() -> dict:
    return run_node_json(JS.replace("'./lib/", f"'{RUNTIME_JS}/lib/"))


def test_a_lens_inside_geometry_retreats_until_clear(result):
    fix = result["fix"]
    assert fix is not None, "the stuck camera must be repaired"
    assert fix["inside_before"] == ["BarCounter"]
    assert fix["moved_back_m"] + fix["moved_up_m"] > 0
    after = result["after"]
    assert not after["camera_in_geometry"]
    assert after["nearest_hit_m"] is None or after["nearest_hit_m"] >= 0.5


def test_a_clear_camera_is_left_exactly_as_authored(result):
    assert result["noFix"] is None
