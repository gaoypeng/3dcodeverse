"""The census measures a stamped ring: >= 8 copies of a family, how evenly they sit on a circle
round their centroid and how alike their sizes are (`groups[].stamps`, 2026-09-09)."""

from __future__ import annotations

import pytest

from tests.scene_runtime.conftest import needs_node, run_node_json

pytestmark = [pytest.mark.node, needs_node]


JS = """
import * as THREE from 'three';
import { sceneCensus } from './lib/host_census.mjs';
const scene = new THREE.Scene();
const zone = new THREE.Group(); zone.name = 'FarShore'; scene.add(zone);
const mat = new THREE.MeshStandardMaterial();
// 16 cones on a ring: an InstancedMesh (JITTER = 0 → a perfect stamp)
const peaks = new THREE.InstancedMesh(new THREE.ConeGeometry(4, 12, 6), mat, 16); peaks.name = 'Peaks';
const d = new THREE.Object3D();
for (let i = 0; i < 16; i++) {
  const a = (i / 16) * Math.PI * 2 + JITTER * Math.sin(i * 7.3);
  const r = 120 * (1 + JITTER * Math.cos(i * 3.1));
  d.position.set(Math.cos(a) * r, 6, Math.sin(a) * r); d.scale.setScalar(1 + JITTER * 2 * Math.sin(i * 5.7)); d.updateMatrix();
  peaks.setMatrixAt(i, d.matrix);
}
zone.add(peaks);
// 10 numbered plain meshes on a small circle (a rotunda's columns): a family by name
for (let i = 0; i < 10; i++) {
  const c = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.3, 4, 8), mat); c.name = 'Column_' + i;
  const a = (i / 10) * Math.PI * 2; c.position.set(Math.cos(a) * 5, 2, Math.sin(a) * 5); zone.add(c);
}
scene.updateMatrixWorld(true);
const g = sceneCensus(scene, THREE).groups.find((x) => x.name === 'FarShore');
console.log(JSON.stringify(g.stamps));
"""


def _stamps(jitter: float) -> dict[str, dict]:
    rows = run_node_json(JS.replace("JITTER", repr(jitter)))
    return {r["name"]: r for r in rows}


def test_a_perfect_ring_of_instances_reads_as_stamped():
    st = _stamps(0.0)
    peaks = st["Peaks"]
    assert peaks["n"] == 16 and abs(peaks["radius_m"] - 120) < 1.0, peaks
    assert peaks["radius_cv"] < 0.01 and peaks["gap_cv"] < 0.01 and peaks["size_cv"] < 0.01, peaks
    cols = st["Column"]                                    # numbered plain meshes are one family
    assert cols["n"] == 10 and abs(cols["radius_m"] - 5) < 0.2, cols


def test_a_jittered_ring_is_not():
    peaks = _stamps(0.25)["Peaks"]
    assert peaks["radius_cv"] > 0.1 and peaks["size_cv"] > 0.1, peaks
