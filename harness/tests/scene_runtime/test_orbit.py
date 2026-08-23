"""Orbit rig fitting (pure node function)."""

from __future__ import annotations

import json
import math

import pytest

from tests.scene_runtime.conftest import needs_node, run_node_json

pytestmark = [pytest.mark.node, needs_node]

BBOX = {"min": [-40, -1, -40], "max": [40, 9, 40], "size": [80, 10, 80]}
VIEWS = [
    {"name": "overview_front_right", "azimuth": 40, "elevation": 32},
    {"name": "overview_top", "azimuth": 0, "elevation": 87},
    {"name": "eye_front", "azimuth": 0, "elevation": 10},
]


def fit(bbox=BBOX, views=VIEWS, **opts):
    body = f"""
import {{ fitOrbitCameras, orbitDirection }} from './lib/orbit.mjs';
console.log(JSON.stringify(fitOrbitCameras({json.dumps(bbox)}, {json.dumps(views)}, {json.dumps(opts)})));
"""
    return run_node_json(body)


def test_direction_convention_front_is_plus_z_and_ccw():
    body = """
import { orbitDirection } from './lib/orbit.mjs';
console.log(JSON.stringify([orbitDirection(0, 0), orbitDirection(90, 0)]));
"""
    front, right = run_node_json(body)
    assert front[2] == pytest.approx(1.0) and abs(front[0]) < 1e-9
    assert right[0] == pytest.approx(1.0) and abs(right[2]) < 1e-9


def test_orbit_cameras_are_above_ground_and_fitted():
    cams = fit(groundY=0.0)
    by = {c["name"]: c for c in cams}
    assert set(by) == {"overview_front_right", "overview_top", "eye_front"}
    for c in cams:
        assert c["position"][1] > 0.5, c
        assert c["kind"] == "orbit"
    ov = by["overview_front_right"]
    dist = math.dist(ov["position"], ov["lookAt"])
    assert 80 < dist < 200
    assert ov["noFog"] is True
    eye = by["eye_front"]
    # eye-level: stands just outside the footprint (z > 40), ~1.6-3 m high, fog kept
    assert 40 < eye["position"][2] < 60
    assert 1.5 < eye["position"][1] < 4
    assert eye["noFog"] is False
    top = by["overview_top"]
    assert top["position"][1] > 100


def test_empty_bbox_returns_no_cameras():
    assert fit(None) == []


def test_fit_zone_camera_respects_floor_and_sun_side():
    """Single owner of the zone fit (assemble.py consumes it via probe_scene.mjs)."""
    body = """
import { fitZoneCamera } from './lib/orbit.mjs';
const box = { min: [20, 0, -5], max: [30, 3, 5], size: [10, 3, 10] };
console.log(JSON.stringify({
  floored: fitZoneCamera(box, { azimuth: 0, floor: 2.0 }),
  sun_x: fitZoneCamera(box, { azimuth: 90 }),
}));
"""
    out = run_node_json(body)
    assert out["floored"]["position"][1] >= 3.5   # floor 2.0 + eye height
    assert out["floored"]["lookAt"][1] <= out["floored"]["position"][1]
    # azimuth 90 → eye on the +X side, outside the footprint (x > 30)
    assert out["sun_x"]["position"][0] > 30
    assert out["sun_x"]["lookAt"] == [25, 1.5, 0]


def test_fit_overview_camera_corner_fit_and_ground_aware():
    body = """
import { fitOverviewCamera, fitDistance, orbitDirection } from './lib/orbit.mjs';
const box = { min: [-40, -1, -40], max: [40, 9, 40], size: [80, 10, 80] };
const cam = fitOverviewCamera(box, { azimuth: 90, elevation: 30, groundY: 0.0 });
const d = orbitDirection(90, 30);
console.log(JSON.stringify({ cam, d }));
"""
    out = run_node_json(body)
    cam = out["cam"]
    # eye on the +X sun side, above ground, looking at the box centre
    assert cam["position"][0] > 40 and cam["position"][1] > 0.5
    assert cam["lookAt"] == [0, 4, 0]
    dist = math.dist(cam["position"], cam["lookAt"])
    assert 80 < dist < 200   # corner-frustum fit, not a loose sphere fit
