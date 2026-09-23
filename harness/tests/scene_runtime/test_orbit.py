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


def test_orbit_rig_fitting():
    """The rig's convention, the orbit views, the per-zone eye and the overview — every
    camera the harness derives for a scene, in one node process."""
    body = f"""
import {{ fitOrbitCameras, fitZoneCamera, fitOverviewCamera, orbitDirection }} from './lib/orbit.mjs';
const zone = {{ min: [20, 0, -5], max: [30, 3, 5], size: [10, 3, 10] }};
console.log(JSON.stringify({{
  front: orbitDirection(0, 0), right: orbitDirection(90, 0),
  none: fitOrbitCameras(null, {json.dumps(VIEWS)}, {{}}),
  cams: fitOrbitCameras({json.dumps(BBOX)}, {json.dumps(VIEWS)}, {{ groundY: 0.0 }}),
  floored: fitZoneCamera(zone, {{ azimuth: 0, floor: 2.0 }}),
  sun_x: fitZoneCamera(zone, {{ azimuth: 90 }}),
  overview: fitOverviewCamera({json.dumps(BBOX)}, {{ azimuth: 90, elevation: 30, groundY: 0.0 }}),
}}));
"""
    out = run_node_json(body)
    # direction convention: front is +Z, azimuth turns counter-clockwise to +X
    front, right = out["front"], out["right"]
    assert front[2] == pytest.approx(1.0) and abs(front[0]) < 1e-9
    assert right[0] == pytest.approx(1.0) and abs(right[2]) < 1e-9

    # orbit cameras: above ground and fitted
    assert out["none"] == []
    cams = out["cams"]
    by = {c["name"]: c for c in cams}
    assert set(by) == {"overview_front_right", "overview_top", "eye_front"}
    for c in cams:
        assert c["position"][1] > 0.5, c
        assert c["kind"] == "orbit"
    ov = by["overview_front_right"]
    assert 80 < math.dist(ov["position"], ov["lookAt"]) < 200
    assert ov["noFog"] is True
    eye = by["eye_front"]
    # eye-level: stands just outside the footprint (z > 40), ~1.6-3 m high, fog kept
    assert 40 < eye["position"][2] < 60
    assert 1.5 < eye["position"][1] < 4
    assert eye["noFog"] is False
    assert by["overview_top"]["position"][1] > 100

    # a zone camera respects the floor and stands on the sun side
    assert out["floored"]["position"][1] >= 3.5   # floor 2.0 + eye height
    assert out["floored"]["lookAt"][1] <= out["floored"]["position"][1]
    # azimuth 90 → eye on the +X side, outside the footprint (x > 30)
    assert out["sun_x"]["position"][0] > 30
    assert out["sun_x"]["lookAt"] == [25, 1.5, 0]

    # the overview: eye on the +X sun side, above ground, looking at the box centre
    cam = out["overview"]
    assert cam["position"][0] > 40 and cam["position"][1] > 0.5
    assert cam["lookAt"] == [0, 4, 0]
    assert 80 < math.dist(cam["position"], cam["lookAt"]) < 200   # corner-frustum fit, not a loose sphere fit
