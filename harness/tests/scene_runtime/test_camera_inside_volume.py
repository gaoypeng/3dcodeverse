"""A lens is "inside" a mesh's VOLUME, not its bounding box (2026-09-07).

Measured on loop 9's windmill (`l9_windmill_hero`, three rounds): the `MillDetail` camera
stood 4.9 m from the nearest surface (`nearest_hit_m` 4.941, `BrickBase_7`) and was still
`camera_in_geometry` because the 22 m lattice sails' world bbox contained the eye — a 22 x
22 m box that is nearly all air.  The repair moved the camera twice for nothing and the judge
marked "camera inside Sails_1" critical both rounds.  `eyeInsideMesh` decides by ray parity
over the mesh's own triangles; the bbox is only the pre-filter.
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
const solid = new THREE.MeshStandardMaterial();           // FrontSide: a room seen from inside is all back faces
const scene = new THREE.Scene();
const ground = new THREE.Mesh(new THREE.PlaneGeometry(80, 80, 2, 2), solid);
ground.name = 'Ground'; ground.rotation.x = -Math.PI / 2; scene.add(ground);
BODY
scene.updateMatrixWorld(true);
const cam = new THREE.PerspectiveCamera(50, 16 / 9, 0.1, 500);
cam.position.set(EYE);
cam.lookAt(0, 8, 0);
cam.updateMatrixWorld(true);
console.log(JSON.stringify(nearGeometry(scene, cam, THREE)));
"""

# four 11 m lattice arms on a hub 12 m up, the sail plane turned 45 deg so the world bbox is a
# 16 x 16 m square around the hub; the eye sits 6 m off the hub inside that square, in the air
SAILS = """
const sails = new THREE.Group(); sails.name = 'Sails_1';
const arm = new THREE.BoxGeometry(22, 0.4, 0.4);
sails.add(new THREE.Mesh(arm, solid));
const armV = new THREE.Mesh(new THREE.BoxGeometry(0.4, 22, 0.4), solid); sails.add(armV);
const merged = new THREE.Group(); merged.name = 'SailsWrap';
for (const m of sails.children) { m.name = 'Sails_1'; }
sails.position.set(0, 12, 0); sails.rotation.y = Math.PI / 4; scene.add(sails);
"""

ROOM = """
const room = new THREE.Mesh(new THREE.BoxGeometry(6, 3, 6), solid);
room.name = 'CottageShell'; room.position.set(0, 1.5, 0); scene.add(room);
"""


def _run(body: str, eye: str) -> dict:
    return run_node_json(JS.replace("BODY", body).replace("EYE", eye).replace("'./lib/", f"'{RUNTIME_JS}/lib/"))


def test_a_lens_near_lattice_sails_is_not_inside_them():
    got = _run(SAILS, "4.5, 12, 4.5")     # inside the 45-degree sails' world bbox, 6.4 m from the hub, in the air
    assert got["inside_mesh_bbox"] == [], got
    assert got["camera_in_geometry"] is False, got


def test_a_lens_in_a_back_face_culled_room_is_still_inside():
    """The case the bbox rule was written for keeps its verdict: a FrontSide box around the
    eye is all back faces to a Raycaster, and parity still counts it."""
    got = _run(ROOM, "0.5, 1.6, 0.5")
    assert got["inside_mesh_bbox"] == ["CottageShell"], got
    assert got["camera_in_geometry"] is True, got


def test_a_lens_inside_a_solid_arm_is_inside():
    got = _run(SAILS, "0, 12, 0")          # the hub itself, inside both arms
    assert "Sails_1" in got["inside_mesh_bbox"], got
    assert got["camera_in_geometry"] is True, got
