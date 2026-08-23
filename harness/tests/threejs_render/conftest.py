"""Fixtures: a four-legged stool authored per the three.js contract (one part file per part)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from codeverse.workspace import Workspace

SEAT_JS = """\
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
export function buildSeat(THREE_) {
  const g = new THREE.Group(); g.name = 'Seat';
  const mesh = new THREE.Mesh(new RoundedBoxGeometry(0.34, 0.04, 0.34, 3, 0.01),
    new THREE.MeshStandardMaterial({ color: 0x8b5a2b, roughness: 0.6 }));
  mesh.name = 'SeatTop';
  mesh.position.set(0, 0.43, 0);
  g.add(mesh);
  return g;
}
"""

LEGS_JS = """\
import * as THREE from 'three';
export function buildLegs(THREE_) {
  const g = new THREE.Group(); g.name = 'Legs';
  const mat = new THREE.MeshStandardMaterial({ color: 0x333333, roughness: 0.4, metalness: 0.6 });
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

STRETCHERS_JS = """\
import * as THREE from 'three';
export function buildStretchers(THREE_) {
  const g = new THREE.Group(); g.name = 'Stretchers';
  const mat = new THREE.MeshStandardMaterial({ color: 0x333333, roughness: 0.4, metalness: 0.6 });
  const geo = new THREE.CylinderGeometry(0.008, 0.008, 0.26, 12);
  for (let i = 0; i < 4; i++) {
    const m = new THREE.Mesh(geo, mat);
    m.name = `Stretcher_${i}`;
    const a = (i * Math.PI) / 2;
    m.position.set(0.13 * Math.cos(a), 0.15, 0.13 * Math.sin(a));
    m.rotateY(-a); m.rotateX(Math.PI / 2);
    g.add(m);
  }
  return g;
}
"""

OBJECT_JS = """\
import * as THREE from 'three';
import { buildSeat } from './parts/seat.js';
import { buildLegs } from './parts/legs.js';
import { buildStretchers } from './parts/stretchers.js';
export function build(THREE_) {
  const root = new THREE.Group(); root.name = 'Stool';
  root.add(buildSeat(THREE), buildLegs(THREE), buildStretchers(THREE));
  root.userData.tick = (t, dt) => { root.rotation.y = t * 0.2; };
  return root;
}
"""

STOOL_FILES = {
    "src/object.js": OBJECT_JS,
    "src/parts/seat.js": SEAT_JS,
    "src/parts/legs.js": LEGS_JS,
    "src/parts/stretchers.js": STRETCHERS_JS,
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


@pytest.fixture
def node_available() -> bool:
    return shutil.which("node") is not None


@pytest.fixture(scope="session")
def stool_glb(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the stool once per session (needs node); skipped when node is missing."""
    if shutil.which("node") is None:
        pytest.skip("node not available")
    from codeverse.languages.threejs.runtime import ThreeJsRuntime

    ws = write_stool(tmp_path_factory.mktemp("stool_session"))
    res = ThreeJsRuntime().build(ws)
    assert res.ok, res.error_message
    return Path(res.glb_path)
