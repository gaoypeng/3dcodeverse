"""materials.js (imported by the library's own factories): a city of one look costs one
material bucket, and every noise map tiles without a seam."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_CACHE = """
import * as MAT from './lib/materials.js';
const mats = new Set(), tex = new Set();
for (let i = 0; i < 2552; i++) {
  const m = MAT.plaster({ variant: (i * 0.37) % 1, color: 0xd8cfc0 });
  mats.add(m.uuid);
  for (const s of ['map', 'roughnessMap']) if (m[s]) tex.add(m[s].uuid);
}
const seeded = new Set(), seededTex = new Set();
for (let i = 0; i < 500; i++) {
  const m = MAT.foliage({ color: 0x3d662f, scale: 16, seed: 40 + i * 7 });
  seeded.add(m.uuid);
  for (const s of ['map', 'roughnessMap']) if (m[s]) seededTex.add(m[s].uuid);
}
console.log(JSON.stringify({ fabricMaterials: mats.size, fabricTextures: tex.size,
  seededMaterials: seeded.size, seededTextures: seededTex.size }));
"""


@pytest.fixture(scope="module")
def cache() -> dict:
    return measure(_CACHE, ("materials.js",))


def test_a_city_of_one_look_costs_one_material_bucket(cache):
    """2 552 plaster calls at jittered variants collapse to the 9 variant buckets
    (and their texture pairs), not 2 552 materials / 1 309 textures."""
    assert cache["fabricMaterials"] <= 9, cache
    assert cache["fabricTextures"] <= 18, cache


def test_a_per_copy_seed_does_not_buy_a_per_copy_texture(cache):
    assert cache["seededMaterials"] <= 6 and cache["seededTextures"] <= 12, cache


_ALBEDO = """
import * as MAT from './lib/materials.js';
const out = {};
// SEAMS: a repeated map whose lattice does not wrap shows a hard line at
// every tile edge.  Compare the wrap-edge step (last column against the
// first) with an interior step of the same texture: a seamless field makes
// them the same size, an unwrapped one makes the wrap ~12x bigger.
const seam = (o) => {
  const t = MAT.noiseTexture(o), d = t.image.data, n = t.image.width;
  let wrapX = 0, innerX = 0, wrapY = 0, innerY = 0;
  for (let y = 0; y < n; y++) {
    wrapX += Math.abs(d[(y * n + n - 1) * 4] - d[y * n * 4]);
    innerX += Math.abs(d[(y * n + n / 2 - 1) * 4] - d[(y * n + n / 2) * 4]);
  }
  for (let x = 0; x < n; x++) {
    wrapY += Math.abs(d[((n - 1) * n + x) * 4] - d[x * 4]);
    innerY += Math.abs(d[(n / 2 - 1) * n * 4 + x * 4] - d[(n / 2) * n * 4 + x * 4]);
  }
  return { wrapX: wrapX / n, innerX: innerX / n, wrapY: wrapY / n, innerY: innerY / n };
};
out.seams = {
  soil: seam({ scale: 20, seed: 3, contrast: 0.4, hueBreak: 0.18 }),
  wood: seam({ scale: 8, seed: 5, contrast: 0.4, streak: 0.9 }),
  steel: seam({ scale: 18, seed: 2, contrast: 0.12, streak: 0.95 }),
  rough: seam({ scale: 34, seed: 50, contrast: 0.5, linear: true }),
  fractional: seam({ scale: 10.2, seed: 8, contrast: 0.5 }),
};
// The field still averages the authored colour after the wrap.
const meanOf = (o) => { const d = MAT.noiseTexture(o).image.data; let s = 0, n = 0;
  for (let i = 0; i < d.length; i += 4) { s += d[i]; n++; } return s / n / 255; };
out.means = { soil: meanOf({ scale: 20, seed: 3, contrast: 0.4 }),
              plain: meanOf({ scale: 6, seed: 1, contrast: 0.3 }) };
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def albedo() -> dict:
    return measure(_ALBEDO, ("materials.js",))


def test_every_noise_map_tiles_without_a_seam(albedo):
    """Measured 2026-09-01: the reference lattice did not wrap, so a soil map's
    wrap edge stepped 11.83 grey levels against an interior 0.94 — the hard grid
    lines a 120 m ground at repeat 10 showed as paving joints in the dirt."""
    for name, s in albedo["seams"].items():
        assert s["wrapX"] <= max(1.6 * s["innerX"], 1.0), (name, s)
        assert s["wrapY"] <= max(1.6 * s["innerY"], 1.0), (name, s)


def test_the_wrapped_field_still_delivers_the_authored_colour(albedo):
    """A colour map MULTIPLIES the authored colour, so the field must still
    average ~0.5 of full scale after the lattice wrap (base 1, so ~0.93 here)."""
    for name, m in albedo["means"].items():
        assert 0.85 <= m <= 1.0, (name, m)
