"""Fixtures: a four-legged stool authored per the three.js contract (one part file per part)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from codeverse3d.workspace import Workspace

SEAT_JS = """\
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
export function buildSeat(THREE_) {
  const g = new THREE.Group(); g.name = 'Seat';
  const mesh = new THREE.Mesh(new RoundedBoxGeometry(0.34, 0.04, 0.34, 3, 0.01), new THREE.MeshStandardMaterial({ color: 0x8b5a2b }));
  mesh.name = 'SeatTop'; mesh.position.set(0, 0.43, 0); g.add(mesh);
  return g;
}
"""

LEGS_JS = """\
import * as THREE from 'three';
export function buildLegs(THREE_) {
  const g = new THREE.Group(); g.name = 'Legs';
  const mat = new THREE.MeshStandardMaterial({ color: 0x333333 });
  const geo = new THREE.CylinderGeometry(0.015, 0.018, 0.41, 16);
  const off = 0.13;
  for (const [x, z] of [[-off, -off], [off, -off], [-off, off], [off, off]]) {
    const m = new THREE.Mesh(geo, mat);
    m.name = `Leg_${x < 0 ? 'L' : 'R'}${z < 0 ? 'B' : 'F'}`;
    m.position.set(x, 0.205, z);
    g.add(m);
  }
  return g;
}
"""

OBJECT_JS = """\
import * as THREE from 'three';
import { buildSeat } from './parts/seat.js';
import { buildLegs } from './parts/legs.js';
export function build(THREE_) {
  const root = new THREE.Group(); root.name = 'Stool';
  root.add(buildSeat(THREE), buildLegs(THREE));
  root.userData.tick = (t, dt) => { root.rotation.y = t * 0.2; };
  return root;
}
"""

STOOL_FILES = {
    "src/object.js": OBJECT_JS,
    "src/parts/seat.js": SEAT_JS,
    "src/parts/legs.js": LEGS_JS,
}


def write_stool(root: Path) -> Workspace:
    ws = Workspace(root)
    for d in ("src/parts", "artifacts"):
        (root / d).mkdir(parents=True, exist_ok=True)
    for rel, text in STOOL_FILES.items():
        (root / rel).write_text(text)
    return ws


@pytest.fixture
def stool_ws(tmp_path: Path) -> Workspace:
    return write_stool(tmp_path / "stool")


@pytest.fixture(scope="session")
def stool_build(tmp_path_factory: pytest.TempPathFactory):
    """Build the stool once per session (needs node)."""
    if shutil.which("node") is None:
        pytest.skip("node not available")
    from codeverse3d.languages.threejs import ThreeJsRuntime

    ws = write_stool(tmp_path_factory.mktemp("stool_session"))
    res = ThreeJsRuntime().build(ws)
    assert res.ok, (res.error_type, res.error_message, res.stderr_tail)
    return res


@pytest.fixture(scope="session")
def stool_glb(stool_build) -> Path:
    return Path(stool_build.glb_path)
