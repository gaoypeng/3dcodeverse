"""A lens is never "inside" a god ray.

Measured on `bench/out/scene_baseline` (2026-09-05).  greenhouse's `scene_frames`
raised, on authored cameras:

    camera inside / touching geometry: inside ['SunShaft_0', 'SunShaft_3']
    camera inside / touching geometry: inside ['SunShaft_2']

`SunShaft` is three slabs of MeshBasicMaterial at opacity 0.075 with
`depthWrite: false` and AdditiveBlending — a shaft of light through a greenhouse roof.
Standing in one is what the shot is; you see straight through it.  The near-geometry
probe counted them because it raycast and bbox-tested every visible mesh.

Same rule as the placement gate (`backdrop.nonSolid`, D54), one spelling for both.
"""

from __future__ import annotations

import pytest

from codeverse.config import get_settings
from tests.scene_runtime.conftest import needs_node, run_node_json

pytestmark = [pytest.mark.node, needs_node]

RUNTIME_JS = get_settings().runtime_js_dir()

JS = """
import * as THREE from 'three';
import { nearGeometry } from './lib/host_metrics.mjs';
const solid = new THREE.MeshStandardMaterial();
const shaftMat = new THREE.MeshBasicMaterial({
  color: 0xfff0c0, transparent: true, opacity: 0.075,
  depthWrite: DEPTH_WRITE, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
});
const scene = new THREE.Scene();
const ground = new THREE.Mesh(new THREE.PlaneGeometry(60, 60, 2, 2), solid);
ground.name = 'Ground'; ground.rotation.x = -Math.PI / 2; scene.add(ground);
// a shaft standing around the eye, and a real bench two metres away
const shaft = new THREE.Mesh(new THREE.BoxGeometry(3, 5, 3), shaftMat);
shaft.name = 'SunShaft_0'; shaft.position.set(0, 2.5, 0); scene.add(shaft);
const bench = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.9, 0.5), solid);
bench.name = 'Bench'; bench.position.set(0, 0.45, -3); scene.add(bench);
scene.updateMatrixWorld(true);
const cam = new THREE.PerspectiveCamera(50, 16 / 9, 0.1, 500);
cam.position.set(0, 1.6, 0);
cam.lookAt(0, 1.2, -3);
cam.updateMatrixWorld(true);
console.log(JSON.stringify(nearGeometry(scene, cam, THREE)));
"""


def _run(depth_write: str) -> dict:
    return run_node_json(JS.replace("DEPTH_WRITE", depth_write).replace("'./lib/", f"'{RUNTIME_JS}/lib/"))


@pytest.fixture(scope="module")
def shaft() -> dict:
    return _run("false")


def test_a_lens_standing_in_a_god_ray_is_not_in_geometry(shaft):
    assert shaft["inside_mesh_bbox"] == []
    assert shaft["camera_in_geometry"] is False


def test_the_bench_behind_it_is_still_seen(shaft):
    """The rule removes the shaft, not the scene: the solid thing the camera looks at
    is still the nearest hit, at its real distance."""
    assert shaft["nearest_hit_name"] == "Bench"
    assert 2.0 < shaft["nearest_hit_m"] < 3.5


def test_the_same_slab_WITH_depth_write_is_still_counted():
    """The control: the rule is `depthWrite`, not the name or the opacity."""
    solid_shaft = _run("true")
    assert solid_shaft["inside_mesh_bbox"] == ["SunShaft_0"]
    assert solid_shaft["camera_in_geometry"] is True


REPAIR_JS = """
import * as THREE from 'three';
import { repairCameraSpec } from './lib/host_metrics.mjs';
const solid = new THREE.MeshStandardMaterial();
const fog = new THREE.MeshBasicMaterial({
  transparent: true, opacity: 0.04, depthWrite: DEPTH_WRITE, side: THREE.DoubleSide,
});
const scene = new THREE.Scene();
const ground = new THREE.Mesh(new THREE.PlaneGeometry(60, 60, 2, 2), solid);
ground.name = 'Ground'; ground.rotation.x = -Math.PI / 2; scene.add(ground);
const shaft = new THREE.Mesh(new THREE.BoxGeometry(3, 5, 3), fog);
shaft.name = 'MoonlightShaft'; shaft.position.set(0, 2.5, 0); scene.add(shaft);
const chair = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.9, 0.8), solid);
chair.name = 'Armchair'; chair.position.set(0, 0.45, -2.4); scene.add(chair);
scene.updateMatrixWorld(true);
const makeCam = (spec) => {
  const cam = new THREE.PerspectiveCamera(spec.fov || 50, 16 / 9, 0.1, 500);
  cam.position.set(...spec.position);
  cam.lookAt(...spec.lookAt);
  cam.updateMatrixWorld(true);
  return cam;
};
// the cozy_cabin shape: an eye standing IN the moonbeam, 2.4 m from the nearest thing
const spec = { name: 'ArmchairHearthEye', position: [0, 1.6, 0], lookAt: [0, 1.2, -2.4], fov: 50 };
console.log(JSON.stringify({ fix: repairCameraSpec(scene, spec, THREE, makeCam) }));
"""


def test_a_camera_standing_in_a_moonbeam_is_not_retreated():
    """`repairCameraSpec` is driven by `camera_in_geometry`, so a false "inside" does not
    only print a finding — it RETREATS the authored lens.  bench/out/scene_baseline
    (2026-09-05) recorded seven repairs, three of them triggered by fog: cozy_cabin's
    ArmchairHearthEye and WindowFrostSnow were each moved back 4 m and up 2 m because
    they stood in a `MoonlightShaft`, and the first of those ended NEARER geometry than
    it started (2.358 m -> 0.916 m).  The judge then scored a shot nobody asked for."""
    out = run_node_json(REPAIR_JS.replace("DEPTH_WRITE", "false").replace("'./lib/", f"'{RUNTIME_JS}/lib/"))
    assert out["fix"] in (None, False), "standing in a light shaft is the shot, not a defect"


def test_a_camera_buried_in_a_solid_slab_is_still_retreated():
    """The control: the same geometry that writes depth is matter, and the lens moves."""
    out = run_node_json(REPAIR_JS.replace("DEPTH_WRITE", "true").replace("'./lib/", f"'{RUNTIME_JS}/lib/"))
    assert out["fix"], "a lens inside a solid slab must still be repaired"
