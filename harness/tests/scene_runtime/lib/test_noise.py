"""noise.js (82 recorded scenes import it): seeded, non-flat, tiling."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_PROBE = """
import * as THREE from 'three';
import { mulberry32, fbm2, fbm3, noiseDataTexture, displaceY } from './lib/noise.js';

const build = (seed) => {
  const g = new THREE.PlaneGeometry(160, 160, 64, 64);
  g.rotateX(-Math.PI / 2);
  displaceY(g, 14, 0.02, seed);
  return g;
};
const a = build(3).attributes.position, b = build(3).attributes.position, c = build(9).attributes.position;
let minY = 1e9, maxY = -1e9, maxSame = 0, maxOther = 0;
for (let i = 0; i < a.count; i++) {
  minY = Math.min(minY, a.getY(i)); maxY = Math.max(maxY, a.getY(i));
  maxSame = Math.max(maxSame, Math.abs(a.getY(i) - b.getY(i)));
  maxOther = Math.max(maxOther, Math.abs(a.getY(i) - c.getY(i)));
}
const n = build(3).attributes.normal;
let tilted = 0;
for (let i = 0; i < n.count; i++) if (n.getY(i) < 0.999) tilted++;

const tex = noiseDataTexture(64, (u, v) => 0.5 + 0.5 * fbm2(u * 6, v * 6, { seed: 5 }));
const d = tex.image.data;
let tMin = 255, tMax = 0;
for (let i = 0; i < d.length; i += 4) { tMin = Math.min(tMin, d[i]); tMax = Math.max(tMax, d[i]); }

const r1 = mulberry32(42), r2 = mulberry32(42);
let seqSame = true, inRange = true;
for (let i = 0; i < 100; i++) { const v1 = r1(), v2 = r2(); if (v1 !== v2) seqSame = false; if (!(v1 >= 0 && v1 < 1)) inRange = false; }

let fMin = 1e9, fMax = -1e9, fDiff = 0;
for (let i = 0; i < 400; i++) {
  const x = (i % 20) * 0.37, z = Math.floor(i / 20) * 0.53;
  const v = fbm2(x, z, { seed: 1 });
  fMin = Math.min(fMin, v); fMax = Math.max(fMax, v);
  fDiff = Math.max(fDiff, Math.abs(v - fbm2(x, z, { seed: 2 })));
  const v3 = fbm3(x, 0.7, z, { octaves: 5 });
  fMin = Math.min(fMin, v3); fMax = Math.max(fMax, v3);
}
console.log(JSON.stringify({ yRange: maxY - minY, maxSame, maxOther, tiltedFrac: tilted / n.count,
  wraps: tex.wrapS === THREE.RepeatWrapping && tex.wrapT === THREE.RepeatWrapping,
  mipmapped: tex.minFilter === THREE.LinearMipmapLinearFilter && tex.generateMipmaps === true,
  texSpread: tMax - tMin, seqSame, inRange, fMin, fMax, fDiff }));
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    return measure(_PROBE, ("noise.js",))


def test_displaced_plane_is_not_flat_and_normals_follow(probe):
    assert probe["yRange"] > 5, probe["yRange"]
    assert probe["tiltedFrac"] > 0.5, probe["tiltedFrac"]


def test_same_seed_rebuilds_the_identical_terrain(probe):
    assert probe["maxSame"] == 0 and probe["maxOther"] > 0.5, probe


def test_noise_texture_wraps_filters_and_is_non_uniform(probe):
    """RepeatWrapping for tiled maps, mipmaps so a puddle mask does not tear
    into stair-steps at grazing angles, and real content."""
    assert probe["wraps"] and probe["mipmapped"]
    assert probe["texSpread"] > 30, probe["texSpread"]


def test_mulberry32_is_reproducible_and_uniform_range(probe):
    assert probe["seqSame"] and probe["inRange"]


def test_fbm_stays_normalized(probe):
    assert probe["fMin"] >= -1.001 and probe["fMax"] <= 1.001, probe
    assert probe["fMax"] - probe["fMin"] > 0.5 and probe["fDiff"] > 0.1, probe
