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
