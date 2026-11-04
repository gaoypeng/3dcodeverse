"""terrain.js (72 recorded scenes import it through the starter's env.js)."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("terrain.js", "materials.js")

_PROBE = """
import * as THREE from 'three';
import * as MAT from './lib/materials.js';
import { ground } from './lib/terrain.js';
const prng = (seed) => { let s = seed; return () => (s = (s * 16807) % 2147483647) / 2147483647; };
const g1 = ground({ size: 200, rand: prng(5), relief: 6 });
const pos = g1.mesh.geometry.attributes.position;
let drift = 0;
for (let i = 0; i < pos.count; i += 7) drift = Math.max(drift, Math.abs(g1.height(pos.getX(i), pos.getZ(i)) - pos.getY(i)));
// the default material is the SHARED library cache: retiling it in place reached into every other asset of the look
const wall = MAT.soil();
const small = ground({ size: 120, rand: prng(5) });
const big = ground({ size: 480, rand: prng(5) });
const mine = new THREE.MeshStandardMaterial({ color: 0x808080 });
const custom = ground({ size: 240, rand: prng(5), material: mine });
console.log(JSON.stringify({ drift, wallRepeat: wall.map.repeat.x, wallReused: small.mesh.material === wall || small.mesh.material.map === wall.map,
  smallRepeat: small.mesh.material.map.repeat.x, bigRepeat: big.mesh.material.map.repeat.x,
  bumpIsMap: small.mesh.material.bumpMap === small.mesh.material.map,
  sharesGpuSource: small.mesh.material.map.source === big.mesh.material.map.source, customKept: custom.mesh.material === mine }));
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    return measure(_PROBE, _LIBS)


def test_ground_returns_the_height_function_it_displaced_with(probe):
    assert probe["drift"] < 1e-5, probe


def test_ground_retiles_a_copy_never_the_shared_library_material(probe):
    """`MAT.soil()` is cached and its maps are shared; ground() set the repeat on it in place (two grounds fought over one texture)."""
    k = probe
    assert k["wallRepeat"] == 1 and not k["wallReused"], k
    assert k["smallRepeat"] == 10 and k["bigRepeat"] == 40, k
    assert k["bumpIsMap"] and k["sharesGpuSource"] and k["customKept"], k


def test_surface_samplers_match_triangles_between_vertices_after_parent_transform():
    result = measure("""
import * as THREE from 'three';
import { ground, cliff } from './lib/terrain.js';
let seed = 37;
const rand = () => (seed = seed * 16807 % 2147483647) / 2147483647;
const terrain = ground({ size: 113, segments: 19, relief: 8, scale: 13, rand });
const wall = cliff({ length: 27, height: 9, relief: 3, rand });
const parent = new THREE.Group();
parent.position.set(8, -3, 12); parent.rotation.set(.2, -.5, .1);
parent.scale.set(1.4, .8, 2.1); parent.add(terrain.mesh, wall.mesh);
parent.updateMatrixWorld(true);
const ray = new THREE.Raycaster();
const error = (mesh, point, direction) => {
  const origin = point.clone().addScaledVector(direction, -100).applyMatrix4(mesh.matrixWorld);
  const endpoint = point.clone().add(direction).applyMatrix4(mesh.matrixWorld);
  ray.set(origin, endpoint.sub(origin).normalize());
  const hit = ray.intersectObject(mesh, false)[0];
  if (!hit) throw new Error('Expected a surface hit');
  return hit.point.distanceTo(point.clone().applyMatrix4(mesh.matrixWorld));
};
let groundError = 0, cliffError = 0;
for (let i = 0; i < 200; i++) {
  const x = (rand() - .5) * 112, z = (rand() - .5) * 112;
  groundError = Math.max(groundError, error(terrain.mesh,
    new THREE.Vector3(x, terrain.height(x, z), z), new THREE.Vector3(0, -1, 0)));
  const s = rand() * 27, y = rand() * 9;
  cliffError = Math.max(cliffError, error(wall.mesh,
    wall.faceAt(s, y), new THREE.Vector3(0, 0, -1)));
}
const flat = cliff({ length: 11, height: 4, relief: 0, rand });
console.log(JSON.stringify({ groundError, cliffError,
  finiteFlatColors: Array.from(flat.mesh.geometry.attributes.color.array).every(Number.isFinite),
  edge: terrain.height(-1000, 1000) === terrain.height(-56.5, 56.5) }));
""", _LIBS)
    assert result["groundError"] < 2e-5, result
    assert result["cliffError"] < 2e-5, result
    assert result["finiteFlatColors"] and result["edge"], result
