"""Orbit rig: content framing box + tight per-corner fit (pure node functions)."""

from __future__ import annotations

import json
import math

import pytest

from tests.scene_runtime.conftest import needs_node, run_node_json

pytestmark = [pytest.mark.node, needs_node]


def _call(fn: str, *args):
    body = f"""
import {{ fitOrbitCameras, framingBox, fitDistance, orbitDirection }} from './lib/orbit.mjs';
console.log(JSON.stringify({fn}(...{json.dumps(list(args))})));
"""
    return run_node_json(body)


def _box(mn, mx):
    return {"min": mn, "max": mx, "size": [b - a for a, b in zip(mn, mx, strict=True)]}


GARDEN = {
    "bbox": _box([-300, -300, -300], [300, 300, 300]),
    "content_bbox": _box([-12.3, -0.3, -9.1], [10.4, 5.8, 11.7]),
    "ground_y": 0.37,
    "groups": [
        {"name": "Environment", "kind": "ground", "bbox": _box([-300, -300, -300], [300, 300, 300])},
        {"name": "MapleGrove", "kind": "content", "bbox": _box([-12.3, 0, -0.1], [10.4, 5.8, 11.7])},
        {"name": "KoiPond", "kind": "content", "bbox": _box([-3, -0.3, -9.1], [3.5, 1.1, -0.1])},
        {"name": "Fence", "kind": "content", "bbox": _box([-5, 0, -6], [5, 1.8, 4])},
    ],
}
BOUNDS = {"min": [-13, -1.5, -13], "max": [13, 6.5, 13]}


def test_framing_box_is_content_union_not_ground_or_sky():
    box = _call("framingBox", GARDEN, BOUNDS)
    assert box["min"] == pytest.approx([-12.3, -0.3, -9.1]) and box["max"] == pytest.approx([10.4, 5.8, 11.7])
    # without bounds: same union; no groups → the census content_bbox; no census → None
    assert _call("framingBox", GARDEN)["size"] == pytest.approx(box["size"])
    assert _call("framingBox", {"content_bbox": GARDEN["content_bbox"], "groups": []})["size"] == pytest.approx(GARDEN["content_bbox"]["size"])
    assert _call("framingBox", None) is None


def test_framing_box_drops_scatter_that_sprawls_past_bounds_and_clamps():
    census = json.loads(json.dumps(GARDEN))
    census["groups"].append({"name": "GrassScatter", "kind": "content", "bbox": _box([-60, 0, -60], [60, 0.3, 60])})
    box = _call("framingBox", census, BOUNDS)
    assert box["max"][0] == pytest.approx(10.4) and box["min"][2] == pytest.approx(-9.1)  # scatter ignored
    # no bounds → nothing to judge sprawl against, but the union is still content only
    box2 = _call("framingBox", census)
    assert box2["size"][0] == pytest.approx(120)
    # a lone sprawling group is kept but clamped to the bounds (×1.1)
    lone = {"groups": [{"name": "Only", "kind": "content", "bbox": _box([-50, 0, -50], [50, 4, 50])}]}
    box3 = _call("framingBox", lone, BOUNDS)
    assert box3["min"][0] == pytest.approx(-13 * 1.1) and box3["max"][2] == pytest.approx(13 * 1.1)


def _ndc_of_corners(bbox, cam, aspect):
    """Project bbox corners with a THREE-style lookAt camera (Y-up); returns max |x|,|y| in NDC."""
    eye, tgt, fov = cam["position"], cam["lookAt"], cam["fov"]
    f = [t - e for t, e in zip(tgt, eye, strict=True)]
    fl = math.hypot(*f)
    f = [v / fl for v in f]
    up0 = [0, 1, 0]
    r = [f[1] * up0[2] - f[2] * up0[1], f[2] * up0[0] - f[0] * up0[2], f[0] * up0[1] - f[1] * up0[0]]
    rl = math.hypot(*r)
    r = [v / rl for v in r]
    u = [r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0]]
    tv = math.tan(math.radians(fov) / 2)
    th = tv * aspect
    worst = 0.0
    for x in (bbox["min"][0], bbox["max"][0]):
        for y in (bbox["min"][1], bbox["max"][1]):
            for z in (bbox["min"][2], bbox["max"][2]):
                rel = [x - eye[0], y - eye[1], z - eye[2]]
                depth = sum(a * b for a, b in zip(rel, f, strict=True))
                px = sum(a * b for a, b in zip(rel, r, strict=True)) / (depth * th)
                py = sum(a * b for a, b in zip(rel, u, strict=True)) / (depth * tv)
                worst = max(worst, abs(px), abs(py))
    return worst


@pytest.mark.parametrize("bbox", [GARDEN["content_bbox"], _box([-3, 0, -1], [3, 12, 1])])
def test_overview_fit_is_tight_every_corner_just_inside_frame(bbox):
    views = [{"name": "overview_front_right", "azimuth": 40, "elevation": 32},
             {"name": "overview_back_left", "azimuth": 220, "elevation": 32},
             {"name": "overview_top", "azimuth": 0, "elevation": 87}]
    cams = _call("fitOrbitCameras", bbox, views, {"groundY": bbox["min"][1], "aspect": 16 / 9})
    for cam in cams:
        worst = _ndc_of_corners(bbox, cam, 16 / 9)
        assert worst <= 1.0 + 1e-6, (cam["name"], worst)        # every corner inside the frame
        assert worst >= 0.80, (cam["name"], worst)               # ...and the box fills it (tight, not a sphere fit)
