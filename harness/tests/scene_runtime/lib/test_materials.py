"""materials.js — one material per look, dark albedos never black, dielectric water/glass.

Ported 2026-09-01 from the scene_multifile_graphics reference (test_material_cache.py,
test_material_dark_albedo.py, test_dielectric_materials.py).  The aesthetic brief's
albedo band (non-emissive 0.02..0.8 linear) and the hue-break contract are pinned here.
"""
from __future__ import annotations

import re

import pytest
from _probe import LIB_DIR, measure

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
const a = MAT.brick({ variant: 0.5 });
const before = a.color.getHex();
const w = MAT.weather(a, 0.5);
const t1 = MAT.tint(a, 0.2), t2 = MAT.tint(a, 0.2), t3 = MAT.tint(a, 0.9);
console.log(JSON.stringify({ fabricMaterials: mats.size, fabricTextures: tex.size,
  seededMaterials: seeded.size, seededTextures: seededTex.size,
  sharesOnIdenticalOptions: a === MAT.brick({ variant: 0.5 }),
  differsOnDistantVariant: a !== MAT.brick({ variant: 0.0 }),
  weatherCopies: w !== a, weatherLeftOriginal: a.color.getHex() === before,
  weatherRougher: w.roughness > a.roughness,
  tintPooled: t1 === t2 && t1 !== t3 && t1 !== a,
  uniqueIsFresh: MAT.brick({ unique: true }) !== MAT.brick({ unique: true }) }));
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


def test_identical_options_share_and_mutators_copy(cache):
    assert cache["sharesOnIdenticalOptions"] and cache["differsOnDistantVariant"]
    assert cache["weatherCopies"] and cache["weatherLeftOriginal"] and cache["weatherRougher"]
    assert cache["tintPooled"] and cache["uniqueIsFresh"]


_ALBEDO = """
import * as MAT from './lib/materials.js';
const L = (c) => 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
const sr = (c) => c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
const authored = (h) => 0.2126 * sr((h >> 16) / 255) + 0.7152 * sr(((h >> 8) & 255) / 255) + 0.0722 * sr((h & 255) / 255);
const out = {};
out.conifer = L(MAT.foliage({ color: 0x163e23, seed: 7 }).color);
out.trunk = L(MAT.weatheredWood({ color: 0x3a2518, seed: 7 }).color);
out.near_black = L(MAT.foliage({ color: 0x040604, seed: 7 }).color);
out.snow = L(MAT.plaster({ color: 0xf4f7fb, seed: 7 }).color);
out.v0 = L(MAT.foliage({ color: 0x163e23, seed: 7, variant: 0 }).color);
out.v1 = L(MAT.foliage({ color: 0x163e23, seed: 7, variant: 1 }).color);
out.authored_conifer = authored(0x163e23);
out.authored_snow = authored(0xf4f7fb);
// Every factory default albedo in the brief's band, 0.02..0.8 linear.
const names = ['plaster','brick','travertine','granite','cobble','asphalt','terracotta','weatheredWood',
  'paintedWood','paintedIron','brushedSteel','giltBronze','fabric','foliage','soil','skin'];
out.albedos = {};
for (const n of names) out.albedos[n] = L(MAT[n]().color);
// The map carries a HUE break, not only value: a granite map's texels
// must spread in R-B difference, not just brightness.
const map = MAT.granite().map.image.data;
let rb = 0, n = 0;
for (let i = 0; i < map.length; i += 4 * 97) { rb += Math.abs(map[i] - map[i + 2]); n++; }
out.granite_rb = rb / n;
const mapPaint = MAT.brushedSteel().map.image.data;
rb = 0; n = 0;
for (let i = 0; i < mapPaint.length; i += 4 * 97) { rb += Math.abs(mapPaint[i] - mapPaint[i + 2]); n++; }
out.steel_rb = rb / n;
// Water and glass are dielectrics with an IOR fresnel; water dark, glass transparent.
const w = MAT.water(), g = MAT.glass();
out.water = { metalness: w.metalness, ior: w.ior, physical: !!w.isMeshPhysicalMaterial, opaque: !w.transparent };
out.glass = { metalness: g.metalness, ior: g.ior, transparent: g.transparent, opacity: g.opacity };
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


def test_a_dark_authored_colour_is_never_black(albedo):
    assert albedo["conifer"] > 0.5 * albedo["authored_conifer"], albedo
    assert albedo["trunk"] > 0.004 and albedo["near_black"] > 0.0004, albedo


def test_a_bright_colour_is_still_reproduced_and_variant_spreads(albedo):
    assert albedo["snow"] > 0.8 * albedo["authored_snow"], albedo
    assert albedo["v1"] > albedo["v0"], albedo


def test_every_factory_albedo_sits_in_the_linear_sane_band(albedo):
    bad = {k: v for k, v in albedo["albedos"].items() if not (0.02 <= v <= 0.8)}
    assert not bad, bad


def test_mineral_maps_carry_hue_variance_and_finishes_carry_less(albedo):
    assert albedo["granite_rb"] > 4, albedo["granite_rb"]
    assert albedo["steel_rb"] < albedo["granite_rb"], albedo


def test_water_and_glass_are_dielectrics(albedo):
    assert albedo["water"]["metalness"] == 0 and abs(albedo["water"]["ior"] - 1.333) < 1e-6
    assert albedo["water"]["physical"] and albedo["water"]["opaque"]
    assert albedo["glass"]["metalness"] == 0 and albedo["glass"]["ior"] == 1.5
    assert albedo["glass"]["transparent"] and 0 < albedo["glass"]["opacity"] < 1


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


def test_the_shipped_source_never_subtracts_a_fixed_lightness_step():
    src = (LIB_DIR / "materials.js").read_text(encoding="utf-8")
    code = "\n".join(ln.split("//")[0] for ln in src.splitlines())
    assert not re.search(r"Math\.random\s*\(", code)
    assert "hsl.l * 0.5" in code, "shade() must scale lightness as a ratio"
    water = code[code.index("export const water"):code.index("export const glass")]
    assert "metalness: 0," in water and not re.search(r"metalness:\s*0\.[1-9]", water)
