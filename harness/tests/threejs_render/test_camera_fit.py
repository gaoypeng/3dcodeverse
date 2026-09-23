"""The object rig's camera fit fills exactly `fill` of the frame for every canonical view."""

from __future__ import annotations

import json

import pytest

from codeverse3d.conventions import OBJECT_VIEWS
from codeverse3d.spatial.node import run_node, runtime_js_dir

pytestmark = pytest.mark.node

VIEWS = [{"name": v.name, "azimuth": v.azimuth_deg, "elevation": v.elevation_deg} for v in OBJECT_VIEWS]
BOXES = [
    [[-0.17, 0.0, -0.17], [0.17, 0.47, 0.17]],     # stool: tall-ish, centred
    [[-1.2, 0.0, -0.3], [1.2, 0.9, 0.3]],          # bench: wide and shallow
    [[0.4, 0.4, 0.4], [0.6, 3.0, 0.5]],            # tall, thin and OFF-CENTRE (the old loop diverged here)
]


def _fit_report(tmp_path) -> list[dict]:
    script = tmp_path / "fit.mjs"
    script.write_text(f"""
import * as THREE from 'three';
import {{ fitCameraToBox, viewDirection }} from {json.dumps(str(runtime_js_dir() / 'lib/browser/camera_fit.js'))};
const views = {json.dumps(VIEWS)}, boxes = {json.dumps(BOXES)};
const rows = [];
for (const [bi, [mn, mx]] of boxes.entries()) {{
  const box = new THREE.Box3(new THREE.Vector3(...mn), new THREE.Vector3(...mx));
  for (const v of views) {{
    for (const aspect of [1, 16 / 9]) {{
      const cam = new THREE.PerspectiveCamera(35, aspect, 0.01, 100);
      const fit = fitCameraToBox(cam, box, v.azimuth, v.elevation, {{ fill: 0.85 }});
      let maxExt = 0;
      for (const x of [mn[0], mx[0]]) for (const y of [mn[1], mx[1]]) for (const z of [mn[2], mx[2]]) {{
        const n = new THREE.Vector3(x, y, z).project(cam);
        maxExt = Math.max(maxExt, Math.abs(n.x), Math.abs(n.y));
      }}
      const centre = box.getCenter(new THREE.Vector3());
      rows.push({{ box: bi, name: v.name, aspect, distance: fit.distance, max_ndc: maxExt,
                  inside: box.containsPoint(cam.position), near: cam.near, far: cam.far,
                  look_at: fit.lookAt, centre: centre.toArray() }});
    }}
  }}
}}
const dir = viewDirection(0, 0).toArray();
console.log(JSON.stringify({{ rows, front_dir: dir }}));
""")
    return run_node(script, [], three_hook=True, timeout_s=60).last_json


def test_every_canonical_view_frames_the_box_exactly(tmp_path):
    report = _fit_report(tmp_path)
    # azimuth 0 = +Z (conventions.py) — the shared orbitDirection, not a second copy
    assert report["front_dir"] == pytest.approx([0.0, 0.0, 1.0], abs=1e-12)
    assert len(report["rows"]) == len(OBJECT_VIEWS) * len(BOXES) * 2
    for r in report["rows"]:
        where = f"{r['name']} box={r['box']} aspect={r['aspect']:.2f}"
        assert r["max_ndc"] == pytest.approx(0.85, abs=1e-6), where   # exact frustum fit
        assert r["distance"] > 0 and not r["inside"], where           # never lands inside the object
        assert 0 < r["near"] < r["far"], where
        assert r["look_at"] == pytest.approx(r["centre"], abs=1e-9), where
