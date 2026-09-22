"""Deterministic camera repair: the smallest retreat that clears the lens."""

from __future__ import annotations

import pytest

from codeverse3d.config import get_settings
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
// a lens under a 60 m terrain raised to y = 3 (cmp6's lighthouse SlipwaySurge): lifted to eye level above it
const head = new THREE.Mesh(new THREE.PlaneGeometry(60, 60, 2, 2), mat);
head.name = 'HeadlandTerrain'; head.rotation.x = -Math.PI / 2; head.position.set(0, 3.0, -40); scene.add(head);
scene.updateMatrixWorld(true);
const buried = { name: 'SlipwaySurge', position: [5, 1.6, -40], lookAt: [0, 4.0, -30], fov: 45 };
const lifted = repairCameraSpec(scene, buried, THREE, makeCam);
const liftedAfter = lifted ? nearGeometry(scene, makeCam(lifted.spec), THREE) : null;
// a squat pillar 0.8 m before the lens, the subject 4 m behind it (loop 22's crypt: 9 of 9 rays on a
// well, a black frame): stepped out of it sideways / up, and the line of sight to lookAt is open after
const pillar = new THREE.Mesh(new THREE.BoxGeometry(1.2, 3.2, 1.2), mat);
pillar.name = 'StonePillar_3'; pillar.position.set(20, 1.6, 18.6); scene.add(pillar);
scene.updateMatrixWorld(true);
const staring = { name: 'AthanorDetail', position: [20, 1.6, 20], lookAt: [20, 1.2, 16], fov: 45 };
const stepped = repairCameraSpec(scene, staring, THREE, makeCam);
const steppedAfter = stepped ? nearGeometry(scene, makeCam(stepped.spec), THREE, undefined, staring.lookAt) : null;
console.log(JSON.stringify({ fix, after, noFix, lifted, liftedAfter, stepped, steppedAfter }));
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


def test_a_lens_under_a_ground_surface_is_lifted_to_eye_level_above_it(result):
    lifted = result["lifted"]
    assert lifted is not None and lifted["under_before"] == "HeadlandTerrain", lifted
    assert abs(lifted["spec"]["position"][1] - (3.0 + 1.6)) < 1e-6, lifted     # terrain at 3 m + 1.6 m eye
    after = result["liftedAfter"]
    assert after["ground_above_m"] is None and abs(after["ground_below_m"] - 1.6) < 1e-3, after


def test_a_lens_staring_at_a_surface_is_stepped_out_of_it_and_its_line_of_sight_opened(result):
    stepped = result["stepped"]
    assert stepped is not None and stepped["blocked_before"] is True and stepped["cut_before"] == "StonePillar_3", stepped
    after = result["steppedAfter"]
    assert after["near_rays"] < 6 and not after["camera_in_geometry"], after
    assert after["target_hit_m"] is None or after["target_hit_m"] >= 0.5 * after["target_distance_m"], after
