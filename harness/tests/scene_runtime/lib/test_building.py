"""building.js — regressions for the facade and cottage bugs the showcase render caught."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("building.js", "materials.js")

_PRELUDE = """
import * as THREE from 'three';
import { block, cottage } from './lib/building.js';

const mk = (s0) => { let s = s0;
  return () => (s = (s * 16807) % 2147483647) / 2147483647; };

/** Emissive hex of every named mesh part. */
function parts(root) {
  const out = {};
  root.traverse((o) => { if (o.isMesh) out[o.name] = o.material.emissive ? o.material.emissive.getHex() : null; });
  return out;
}

/** Name of the first mesh a ray meets, or null. */
function firstHit(root, from, dir) {
  const meshes = [];
  root.traverse((o) => { if (o.isMesh) meshes.push(o); });
  const hit = new THREE.Raycaster(
      new THREE.Vector3().fromArray(from),
      new THREE.Vector3().fromArray(dir).normalize(), 0, 400)
      .intersectObjects(meshes, true);
  return hit.length ? hit[0].object.name : null;
}

/** Texels per metre per triangle: p10/p50/p90 over one merged mesh. */
function texelDensity(geo) {
  const pos = geo.attributes.position, uv = geo.attributes.uv;
  const idx = geo.index;
  const n = idx ? idx.count : pos.count;
  const A = new THREE.Vector3(), B = new THREE.Vector3(), C = new THREE.Vector3();
  const dens = [];
  for (let i = 0; i < n; i += 3) {
    const a = idx ? idx.getX(i) : i;
    const b = idx ? idx.getX(i + 1) : i + 1;
    const c = idx ? idx.getX(i + 2) : i + 2;
    A.fromBufferAttribute(pos, a);
    B.fromBufferAttribute(pos, b);
    C.fromBufferAttribute(pos, c);
    const wa = B.clone().sub(A).cross(C.clone().sub(A)).length() * 0.5;
    const ua = Math.abs((uv.getX(b) - uv.getX(a)) * (uv.getY(c) - uv.getY(a))
                      - (uv.getX(c) - uv.getX(a)) * (uv.getY(b) - uv.getY(a)))
             * 0.5;
    if (wa > 1e-6 && ua > 1e-12) dens.push(Math.sqrt(ua / wa));
  }
  dens.sort((p, q) => p - q);
  const q = (f) => dens[Math.floor(f * (dens.length - 1))];
  return { p10: q(0.1), p50: q(0.5), p90: q(0.9), n: dens.length };
}
"""


def _measure(script: str) -> dict:
    return measure(_PRELUDE + script, _LIBS)


@pytest.fixture(scope="module")
def facade() -> dict:
    return _measure("""
const b = block({ w: 14, d: 12, h: 24, rand: mk(11), style: 'brick' });
console.log(JSON.stringify({ density: texelDensity(b.getObjectByName('Walls').geometry) }));
""")


def test_one_texel_scale_across_the_whole_facade(facade):
    """three gives every box face a full 0..1 UV whatever it measures, so one
    merged facade stretched a brick tile over a 14 m wall and squeezed it onto
    a 0.34 m mullion -- the piers rendered as vertical woodgrain stripes."""
    d = facade["density"]
    assert d["n"] > 200, d
    assert d["p90"] / d["p10"] < 1.05, (
        f"texel density varies {d['p90'] / d['p10']:.1f}x across the walls", d)


@pytest.fixture(scope="module")
def lit_block() -> dict:
    return _measure("""
const b = block({ w: 16, d: 14, h: 26, style: 'masonry', lit: 0.45,
                  rand: mk(5) });
const glow = [];
b.traverse((o) => {
  if (o.isMesh && o.material.emissive && o.material.emissiveIntensity > 0
      && o.material.emissive.getHex() !== 0) {
    glow.push({ name: o.name, emissive: o.material.emissive.getHex(),
                intensity: o.material.emissiveIntensity,
                tris: o.geometry.index ? o.geometry.index.count / 3
                    : o.geometry.attributes.position.count / 3 });
  }
});
// Panes are boxes: 12 triangles each, lit or dark. The ratio IS the
// `lit` fraction the caller asked for -- unless the whole shell glows.
const litPanes = glow.reduce((a, g) => a + g.tris, 0) / 12;
let darkPanes = 0;
b.traverse((o) => { if (o.isMesh && o.name.startsWith('Glazing'))
  darkPanes += (o.geometry.index ? o.geometry.index.count / 3
      : o.geometry.attributes.position.count / 3) / 12; });
console.log(JSON.stringify({
  revealEmissive: parts(b).Reveals,
  glow,
  litPanes, darkPanes,
}));
""")


def test_a_lit_block_lights_windows_not_the_whole_shell(lit_block):
    """The reveal shell is ONE box spanning the building. Pushing lit panes
    onto it and making it emissive lit every window at once, so `lit: 0.45`
    rendered as a solid glowing lantern with no dark windows at all."""
    m = lit_block
    assert m["revealEmissive"] == 0, (
        "the interior shell itself glows -- every window in the building "
        "lights from one box")
    assert all(g["name"].startswith("LitRooms") for g in m["glow"]), m["glow"]
    # `lit: 0.45` has to mean 45% of the windows, not the building.
    frac = m["litPanes"] / (m["litPanes"] + m["darkPanes"])
    assert 0.25 < frac < 0.65, (frac, m["litPanes"], m["darkPanes"])
    assert m["darkPanes"] >= 8, ("no window stayed dark", m["darkPanes"])


# ------------------------------------------------------------- the cottage

@pytest.fixture(scope="module")
def cot() -> dict:
    return _measure("""
const W = 8, D = 7, H = 3.2, PITCH = 0.85 * W / 2;
const c = cottage({ w: W, d: D, h: H, dormers: 2, roof: 'gable',
                    rand: mk(23) });

// A bay window on the -Z gable wall, aimed off its mullion cross: the
// first thing seen from outside must be GLASS. A frame box filled solid
// (or a pane buried inside the wall) answers 'Trim' or 'Walls'.
const bayX = -W / 2 + (W / Math.max(1, Math.round(W / 2.6))) * 0.5;
const gableWindow = firstHit(c, [bayX + 0.2, H * 0.55 + 0.2, -30], [0, 0, 1]);
// The same on an eaves (+-X) wall, which carried no openings at all.
// Off the mullion cross in BOTH axes, or the ray meets a glazing bar.
const eavesZ = -D / 2 + (D / Math.max(1, Math.round(D / 2.8))) * 0.5;
const eavesWindow = firstHit(c, [-30, H * 0.55 + 0.2, eavesZ + 0.2], [1, 0, 0]);
// And a dormer, which sat buried INSIDE the roof mass.
const dormerZ = -D / 2 + (D / 3) * 1;
const dormerHit = firstHit(c, [-30, H + PITCH * 0.42, dormerZ], [1, 0, 0]);

// WHICH WAY UP IS THE GABLE. Apex up: the triangle is narrow near the
// ridge and wide at the eaves. Fire along Z at the gable plane, against
// the WALLS mesh only, high in the gable and low in it.
const wallsOnly = new THREE.Group();
c.traverse((o) => { if (o.isMesh && o.name === 'Walls') wallsOnly.add(o.clone()); });
const gableAt = (frac) => firstHit(
    wallsOnly, [W * 0.35, H + PITCH * frac, 40], [0, 0, -1]);

const glazing = c.getObjectByName('Glazing');
const gb = new THREE.Box3().setFromObject(glazing);

console.log(JSON.stringify({
  names: Object.keys(parts(c)).sort(),
  gableWindow, eavesWindow, dormerHit,
  gableHigh: gableAt(0.75), gableLow: gableAt(0.15),
  glazingFrontZ: gb.min.z,
  trimIsNotRoof:
      c.getObjectByName('Trim').material.uuid
      !== c.getObjectByName('Roof').material.uuid,
  chimneyIsNotWall: c.getObjectByName('Chimney').material.uuid
      !== c.getObjectByName('Walls').material.uuid,
}));
""")


def test_cottage_window_is_glass_not_a_painted_slab(cot):
    """The frame was a SOLID box covering the whole opening and the pane sat
    inside the wall behind it, so every cottage window rendered as an opaque
    rectangle in the roof's terracotta -- the flat-facade complaint itself."""
    m = cot
    assert m["gableWindow"] == "Glazing", (
        f"looking into a bay from outside hits '{m['gableWindow']}'")
    assert m["glazingFrontZ"] < -3.5, (
        "the pane is buried inside the wall box", m["glazingFrontZ"])


def test_cottage_has_openings_on_every_elevation(cot):
    """The eaves (+-X) walls had no windows at all: turned any way but one, a
    cottage showed a blank white elevation to the camera."""
    m = cot
    assert m["eavesWindow"] == "Glazing", m["eavesWindow"]


def test_cottage_dormers_sit_on_the_slope_they_open_through(cot):
    """The ridge runs along Z, so the slopes face +-X; dormers spread along X
    at one Z were boxes buried inside the roof mass and rendered as nothing."""
    m = cot
    assert m["dormerHit"] in ("Glazing", "Trim"), (
        f"a ray at dormer height meets '{m['dormerHit']}' first")


def test_cottage_gable_ends_are_apex_up(cot):
    """three's first radial vertex is at +Z, so `rotateX(+PI/2)` puts the
    3-gon's APEX at y=-1 and a flat edge at +0.5: every gable shipped upside
    down, its two top corners poking out through the roof as white wings.
    A bbox sees the same triangle either way, so this measures the shape."""
    m = cot
    assert m["gableLow"] == "Walls", (
        "no gable wall low down: the end is open", m["gableLow"])
    assert m["gableHigh"] is None, (
        "the gable is still wide near the ridge -- it is upside down "
        f"({m['gableHigh']})")


def test_cottage_joinery_is_not_roof_tile(cot):
    """`trimMat` was built and then discarded (`void trimMat`) while the
    frames, sills and door surround were merged into the ROOF mesh."""
    m = cot
    assert m["trimIsNotRoof"], "the joinery still shares the roof material"
    assert "Trim" in m["names"] and "Door" in m["names"], m["names"]
    assert m["chimneyIsNotWall"], (
        "the stack is limewash, not masonry", m["names"])


