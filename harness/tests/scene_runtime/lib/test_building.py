"""building.js — facades ship with real openings, because a painted window is a box.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_building_lib.py).  Their geometry claims are pinned exactly as they
were: real gaps at window height, a cornice that overhangs, a dielectric
glazing, the origin/size contract, a city-affordable triangle count, and the
casement's recessed pane, projecting sill, hinged shutters and lit card.

Nothing here asserts THEIR renderer contract.  What the port added instead are
the four things the reference never measured and our showcase render caught on
this host:

* ``cottage()`` merged its window frames, sills and door surround into the
  ROOF mesh (``trimMat`` was built and then discarded with ``void trimMat``),
  so every cottage window was a terracotta rectangle painted on a white wall;
* its panes sat INSIDE the solid wall box, invisible — which also meant the
  ``lit`` path it exists for produced glow nothing could see at night;
* its gable end walls were built upside down (``rotateX(+PI/2)`` puts the
  3-gon's apex at y = -1), so two corners stuck out through the roof slopes;
* ``block({lit})`` made the whole 22 m interior shell emissive, lighting every
  window in the building from one box — ``lit: 0.4`` still came out a lantern.

building.js builds no custom shaders (every material is a stock
Standard/Physical), so the reference's ``scene_check`` GPU cases would prove
nothing here; the real compile is covered by the showcase render and by the
windows.js port, which patches these meshes.
"""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("building.js", "materials.js")

_PRELUDE = """
import * as THREE from 'three';
import { block, cottage, tower, casement, roofClutter, cityFabric }
  from './lib/building.js';

const mk = (s0) => { let s = s0;
  return () => (s = (s * 16807) % 2147483647) / 2147483647; };

/** Named parts of a built asset: triangles, world box, material facts. */
function census(root) {
  root.updateMatrixWorld(true);
  const parts = {};
  let tris = 0;
  root.traverse((o) => {
    if (!o.isMesh) return;
    const n = o.geometry.index ? o.geometry.index.count / 3
                               : o.geometry.attributes.position.count / 3;
    tris += n;
    const b = new THREE.Box3().setFromObject(o);
    const c = o.material.color ? o.material.color.getHex() : 0;
    parts[o.name] = {
      tris: n,
      box: [b.min.x, b.min.y, b.min.z, b.max.x, b.max.y, b.max.z],
      material: o.material.type,
      metalness: o.material.metalness,
      transparent: !!o.material.transparent,
      albedo: Math.max(c >> 16 & 255, c >> 8 & 255, c & 255) / 255,
      emissive: o.material.emissive ? o.material.emissive.getHex() : null,
      emissiveIntensity: o.material.emissiveIntensity,
    };
  });
  return { parts, tris };
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


# --------------------------------------------------------------- the block

@pytest.fixture(scope="module")
def facade() -> dict:
    """One node launch, shared by every test that reads a plain block."""
    return _measure("""
const b = block({ w: 14, d: 12, h: 24, rand: mk(11), style: 'brick' });
const { parts, tris } = census(b);
const whole = new THREE.Box3().setFromObject(b);

// Sweep rays horizontally across the facade at window height. On a solid
// box EVERY ray hits the wall; with real openings the ones aimed at a bay
// pass the wall plane and land on the dark reveal behind.
const wallMesh = [];
b.traverse((o) => { if (o.isMesh && o.name === 'Walls') wallMesh.push(o); });
let clear = 0, fired = 0;
for (let i = 0; i < 60; i++) {
  const x = -6.6 + (13.2 * i) / 59;
  const ray = new THREE.Raycaster(
      new THREE.Vector3(x, 14.6, 40), new THREE.Vector3(0, 0, -1), 0, 200);
  fired++;
  if (ray.intersectObjects(wallMesh, true).length === 0) clear++;
}

// Every distinct wall colour a STREET of one style produces. One rand
// stream, the way cityFabric feeds a district -- a caller who seeds a
// fresh Lehmer PRNG per building gets a near-zero first draw from every
// one of them and lands them all in the same bucket.
const tints = new Set();
const street = mk(11);
for (let i = 0; i < 12; i++) {
  block({ w: 14, d: 12, h: 20, style: 'brick', rand: street })
      .traverse((o) => { if (o.isMesh && o.name === 'Walls')
        tints.add(o.material.color.getHex()); });
}
// The brightest non-emissive albedo anywhere in the module.
let peak = 0, peakName = '';
for (const style of ['brick', 'masonry', 'stone', 'glass']) {
  for (const s of [3, 11, 29, 77]) {
    block({ w: 14, d: 12, h: 20, style, rand: mk(s) }).traverse((o) => {
      if (!o.isMesh || o.material.transparent) return;
      const c = o.material.color.getHex();
      const a = Math.max(c >> 16 & 255, c >> 8 & 255, c & 255) / 255;
      if (a > peak) { peak = a; peakName = style + '/' + o.name; }
    });
  }
}
for (const s of [3, 11, 29]) {
  cottage({ rand: mk(s), dormers: 1 }).traverse((o) => {
    if (!o.isMesh || o.material.transparent) return;
    const c = o.material.color.getHex();
    const a = Math.max(c >> 16 & 255, c >> 8 & 255, c & 255) / 255;
    if (a > peak) { peak = a; peakName = 'cottage/' + o.name; }
  });
}

const walls = b.getObjectByName('Walls');
console.log(JSON.stringify({
  names: Object.keys(parts).sort(),
  tris,
  height: whole.max.y - whole.min.y,
  baseY: whole.min.y,
  width: whole.max.x - whole.min.x,
  trimOverhangsWall: parts.Trim.box[3] - parts.Walls.box[3],
  glazingType: parts.Glazing.material,
  glazingMetalness: parts.Glazing.metalness,
  revealAlbedo: parts.Reveals.albedo,
  revealEmissive: parts.Reveals.emissive,
  floors: b.userData.floors,
  forward: b.userData.forward,
  clearFraction: clear / fired,
  wallBox: parts.Walls.box,
  distinctTints: tints.size,
  peakAlbedo: peak, peakName,
  density: texelDensity(walls.geometry),
}));
""")


def test_a_facade_has_walls_trim_glazing_and_a_dark_interior(facade):
    """Four named parts, because the complaints name four things.

    Unlit, the two glazing batches collapse to whichever ones drew — the
    part names an agent (and mirror.js's glazeFacade) targets are stable.
    """
    m = facade
    for part in ("Walls", "Trim", "Glazing", "Reveals"):
        assert part in m["names"], (part, m["names"])
    assert not [n for n in m["names"] if n.startswith("LitRooms")], m["names"]


def test_openings_are_real_gaps_not_painted_on(facade):
    """A ray through a window bay must miss the wall.

    This is the whole claim of the module. A textured box passes every other
    check here and still reads as a box in silhouette and shadow.
    """
    m = facade
    assert m["clearFraction"] > 0.35, (
        f"only {m['clearFraction']:.0%} of rays across the facade at window "
        "height passed the wall -- the openings are not real (a solid box "
        "scores 0%)")


def test_the_roofline_and_base_are_not_flush_with_the_wall(facade):
    """"Trim and mouldings" is 15% of the building complaints."""
    m = facade
    assert m["trimOverhangsWall"] > 0.1, (
        f"cornice does not project past the wall: {m['trimOverhangsWall']}")


def test_glazing_is_a_dielectric(facade):
    """Matte glass towers are a named verdict; metal glass is wrong physics."""
    m = facade
    assert m["glazingType"] == "MeshPhysicalMaterial", m["glazingType"]
    assert m["glazingMetalness"] == 0, m["glazingMetalness"]


def test_it_rests_on_zero_at_the_requested_size(facade):
    """Same origin contract as every other asset, so `seat()` works."""
    m = facade
    assert abs(m["baseY"]) < 0.01, m["baseY"]
    assert abs(m["height"] - 24) < 1.6, m["height"]
    # `w` is the WALL width; the bbox is legitimately wider because the
    # cornice projects past it -- that projection is the point.
    wall_w = m["wallBox"][3] - m["wallBox"][0]
    assert abs(wall_w - 14) < 1.0, wall_w
    assert m["width"] - wall_w < 2.0, (m["width"], wall_w)
    assert m["forward"] == "-Z"
    assert m["floors"] >= 6, m["floors"]


def test_a_city_can_afford_it(facade):
    """The whole scene has a ~600k triangle budget.

    A 12-cliff run shipped 6.7M tris and crawled; a block a city places by
    the hundred stays cheap even before instancing merges it.
    """
    m = facade
    assert m["tris"] < 4000, f"{m['tris']} triangles for one block"


def test_the_reveal_behind_a_window_is_dark_but_not_a_hole(facade):
    """0x14161a is 0.08 albedo: under our ACES/exposure-1.0 pipeline with no
    post chain it rendered as pure black, and a black rectangle reads as a
    hole punched in the wall rather than a room behind glass."""
    m = facade
    assert 0.1 < m["revealAlbedo"] < 0.28, m["revealAlbedo"]
    assert m["revealEmissive"] == 0, (
        "an unlit block must not glow anywhere", m["revealEmissive"])


def test_a_street_of_blocks_is_not_one_colour(facade):
    """Seeded blocks draw a batch tint, so a district stops reading as one
    stamped asset. Bucketed, so a hundred blocks still cost a few materials."""
    m = facade
    assert m["distinctTints"] >= 3, m["distinctTints"]
    assert m["distinctTints"] <= 9, ("tint buckets leaked", m["distinctTints"])


def test_no_wall_is_brighter_than_a_lit_albedo(facade):
    """Non-emissive albedos stay under 0.8 sRGB. Plaster and travertine ship
    near 0.92, which at exposure 1.0 with no post chain lands a whole facade
    at the top of the range -- a white card no shadow can shape."""
    m = facade
    assert m["peakAlbedo"] <= 0.80, (m["peakName"], m["peakAlbedo"])


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
    """One node launch for the night facade."""
    return _measure("""
const b = block({ w: 16, d: 14, h: 26, style: 'masonry', lit: 0.45,
                  rand: mk(5) });
const { parts } = census(b);
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
  names: Object.keys(parts).sort(),
  revealEmissive: parts.Reveals.emissive,
  revealTris: parts.Reveals.tris,
  glow,
  litPanes, darkPanes,
  warm: glow.filter((g) => (g.emissive >> 16 & 255) > (g.emissive & 255)).length,
  cool: glow.filter((g) => (g.emissive & 255) > (g.emissive >> 16 & 255)).length,
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


def test_lit_windows_split_into_room_classes(lit_block):
    """A night facade on one emissive material is a slab. Real windows split
    by room -- tungsten, warm white, the odd cool kitchen -- and that split
    is the whole night read."""
    m = lit_block
    hexes = {g["emissive"] for g in m["glow"]}
    assert len(hexes) >= 2, ("one emissive colour for every lit window", m["glow"])
    assert m["warm"] >= 1 and m["cool"] >= 1, m["glow"]


def test_lit_windows_stay_in_the_bloom_friendly_band(lit_block):
    """Peak 1.5-4, never 20: an emissive that clips is a white hole with no
    colour left in it, and nothing downstream can grade it back."""
    m = lit_block
    for g in m["glow"]:
        assert 1.0 <= g["intensity"] <= 4.0, g


def test_lit_windows_are_not_matched_by_the_mirror_sweep(lit_block):
    """mirror.js `glazeFacade` retargets every `/glaz|pane/i` mesh and
    documents that lit windows are left glowing. Naming the lit cards
    `LitGlazing` (as cottage did) walked them straight into that sweep."""
    m = lit_block
    import re
    for name in m["names"]:
        if re.search(r"glaz|pane", name, re.I):
            assert not name.startswith("Lit"), name


# ------------------------------------------------------------ the casement

@pytest.fixture(scope="module")
def casement_probe() -> dict:
    """One node launch, shared by every casement test."""
    return _measure("""
const c = casement({ w: 1.2, h: 1.5, shutters: true, rand: mk(7) });
const { parts, tris } = census(c);

let litBacking = null;
casement({ lit: true, rand: mk(3) }).traverse((o) => {
  if (o.isMesh && o.name === 'Backing') litBacking = {
    emissive: o.material.emissive.getHex(),
    emissiveIntensity: o.material.emissiveIntensity,
  };
});

const countGlow = (g) => { let n = 0; g.traverse((o) => {
  if (o.isMesh && o.material.emissive && o.material.emissiveIntensity > 0
      && o.material.emissive.getHex() !== 0) n++; }); return n; };

const sl = parts.ShutterL.box;
console.log(JSON.stringify({
  names: Object.keys(parts).sort(),
  tris,
  frameFrontZ: parts.Frame.box[2],
  paneRecess: parts.Pane.box[2] - parts.Frame.box[2],
  paneBehindWall: parts.Pane.box[2],
  sillFrontZ: parts.Sill.box[2],
  shutterAngleDeg: Math.atan2(sl[5] - sl[2], sl[3] - sl[0]) * 180 / Math.PI,
  shutterTris: parts.ShutterL.tris,
  shutterMaxZ: Math.max(sl[5], parts.ShutterR.box[5]),
  backingEmissive: parts.Backing.emissive,
  backingAlbedo: parts.Backing.albedo,
  litBacking,
  forward: c.userData.forward,
  cottageGlowDark: countGlow(cottage({ rand: mk(11) })),
  cottageGlowLit: countGlow(cottage({ lit: 1, rand: mk(11) })),
}));
""")


def test_casement_pane_is_recessed_behind_a_proud_frame(casement_probe):
    """The whole claim: depth, not a flush quad.

    Audited failures were flush single quads; good scenes recess the pane
    >=0.08 m behind a proud frame, measured by world-Z.
    """
    m = casement_probe
    assert m["frameFrontZ"] < -0.02, (
        f"frame front at z={m['frameFrontZ']} is not proud of the wall")
    assert m["paneRecess"] >= 0.08, (
        f"pane only {m['paneRecess']:.3f} m behind the frame front -- "
        "that is the flush-quad failure this function exists to kill")
    assert m["paneBehindWall"] > 0, (
        f"pane at z={m['paneBehindWall']} floats OUTSIDE the wall")


def test_casement_sill_projects_past_the_frame(casement_probe):
    """A sill that does not project is a paint stripe."""
    m = casement_probe
    assert m["sillFrontZ"] < m["frameFrontZ"], (
        f"sill front {m['sillFrontZ']} does not clear the frame "
        f"front {m['frameFrontZ']}")


def test_casement_shutters_hinge_open_off_the_wall(casement_probe):
    """Louvered panels at ~55 deg, the best audited scene's form.

    World-bbox angle: ~0 deg is flat on the wall, ~90 straight out; the
    hinge swing must land between, on the OUTSIDE of the wall.
    """
    m = casement_probe
    assert 35 < m["shutterAngleDeg"] < 75, (
        f"shutter plane at {m['shutterAngleDeg']:.0f} deg to the wall")
    assert m["shutterMaxZ"] < 0.02, (
        f"a shutter reaches z={m['shutterMaxZ']} -- through the wall")
    assert m["shutterTris"] >= 60, (
        f"{m['shutterTris']} tris is a flat panel, not louvers")


def test_casement_lit_swaps_in_the_warm_card(casement_probe):
    """`lit: true` must glow warm; unlit must stay a dark interior.

    One window is one room, so it draws a WARM class only -- a lone blue
    window in a wall reads as a fault, not as a fluorescent kitchen.
    """
    m = casement_probe
    assert m["backingEmissive"] == 0, m["backingEmissive"]
    assert 0.1 < m["backingAlbedo"] < 0.28, m["backingAlbedo"]
    lit = m["litBacking"]
    assert 1 < lit["emissiveIntensity"] <= 4, lit
    assert (lit["emissive"] >> 16 & 0xFF) > (lit["emissive"] & 0xFF), (
        f"lit backing 0x{lit['emissive']:06x} is not warm")


def test_cottage_lit_path_glows(casement_probe):
    """5/5 failing dusk scenes had zero window emissive; cottage() was the
    only building silhouette with no lit path at all."""
    m = casement_probe
    assert m["cottageGlowDark"] == 0, m["cottageGlowDark"]
    assert m["cottageGlowLit"] >= 1, (
        "cottage({lit: 1}) produced no emissive mesh")


def test_casement_parts_and_budget(casement_probe):
    """Named parts a fix agent can target, at a cost a village affords."""
    m = casement_probe
    for part in ("Frame", "Sill", "Pane", "Backing", "ShutterL", "ShutterR"):
        assert part in m["names"], (part, m["names"])
    assert m["tris"] < 900, f"{m['tris']} triangles for one window"
    assert m["forward"] == "-Z"


# ---------------------------------------------------- the interior casement

@pytest.fixture(scope="module")
def interior() -> dict:
    """One node launch, shared by every interior-casement test."""
    return _measure("""
const c = casement({ w: 1.2, h: 1.5, interior: true });
const { parts } = census(c);

// From the ROOM (+Z), the first thing a ray hits must be the frame or the
// pane. If the daylight card wins, it is floating INSIDE the room instead
// of standing in for the sky outside the glass.
const firstHits = [0, 0.3, -0.45].map(
    (x) => firstHit(c, [x, 0.2, 6], [0, 0, -1]));

let nightCard = null;
casement({ interior: true, view: 'night' }).traverse((o) => {
  if (o.isMesh && o.name === 'Daylight') nightCard = {
    emissive: o.material.emissive.getHex(),
    emissiveIntensity: o.material.emissiveIntensity,
  };
});

console.log(JSON.stringify({
  names: Object.keys(parts).sort(),
  firstHits,
  cardMaxZ: parts.Daylight.box[5],
  paneMinZ: parts.Pane.box[2],
  frameMaxZ: parts.Frame.box[5],
  sillMaxZ: parts.Sill.box[5],
  cardEmissive: parts.Daylight.emissive,
  cardIntensity: parts.Daylight.emissiveIntensity,
  nightCard,
  forward: c.userData.forward,
}));
""")


def test_interior_casement_puts_daylight_outside_the_pane(interior):
    """The whole claim: a lit opening with depth, not a black hole.

    The emissive card must sit BEHIND the pane (lower Z, outside the room);
    a ray from the room must hit frame or pane, never the card.
    """
    m = interior
    for hit in m["firstHits"]:
        assert hit in ("Frame", "Pane"), m["firstHits"]
    assert m["cardMaxZ"] < m["paneMinZ"], (
        f"daylight card front z={m['cardMaxZ']} is not behind the pane "
        f"back z={m['paneMinZ']}")


def test_interior_casement_frame_and_sill_face_the_room(interior):
    """The reveal is on the +Z (room) side, and the stool clears it."""
    m = interior
    assert m["frameMaxZ"] > 0.02, (
        f"frame front at z={m['frameMaxZ']} does not stand into the room")
    assert m["sillMaxZ"] > m["frameMaxZ"], (
        f"sill front {m['sillMaxZ']} does not project past the frame "
        f"front {m['frameMaxZ']}")
    assert m["forward"] == "+Z", m["forward"]


def test_interior_casement_day_card_is_bright_and_cool(interior):
    """Blown-out sky through glass: cool 0xdfeaff family, intensity >= 1.5 --
    a dim or warm card reads as a lamp, not daylight."""
    m = interior
    assert 1.5 <= m["cardIntensity"] <= 4, m["cardIntensity"]
    e = m["cardEmissive"]
    assert (e & 0xFF) >= (e >> 16 & 0xFF), f"day card 0x{e:06x} is not cool"


def test_interior_casement_night_swaps_to_deep_blue(interior):
    """After dark the opening must still read, at LOW emissive."""
    m = interior
    night = m["nightCard"]
    assert night is not None, "view:'night' lost the Daylight card"
    assert night["emissiveIntensity"] < 1, night
    e = night["emissive"]
    assert (e & 0xFF) > (e >> 16 & 0xFF), f"night card 0x{e:06x} not blue"
    assert e < 0x404060, f"night card 0x{e:06x} is not deep"


# ------------------------------------------------------------- the cottage

@pytest.fixture(scope="module")
def cot() -> dict:
    """One node launch, shared by every cottage test."""
    return _measure("""
const W = 8, D = 7, H = 3.2, PITCH = 0.85 * W / 2;
const c = cottage({ w: W, d: D, h: H, dormers: 2, roof: 'gable',
                    rand: mk(23) });
const { parts, tris } = census(c);
const whole = new THREE.Box3().setFromObject(c);

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
  names: Object.keys(parts).sort(),
  tris,
  baseY: whole.min.y,
  ridgeY: c.userData.ridgeY,
  topY: whole.max.y,
  forward: c.userData.forward,
  gableWindow, eavesWindow, dormerHit,
  gableHigh: gableAt(0.75), gableLow: gableAt(0.15),
  wallsMaxX: parts.Walls.box[3],
  glazingFrontZ: gb.min.z,
  roofColor: parts.Roof.albedo,
  trimIsNotRoof:
      c.getObjectByName('Trim').material.uuid
      !== c.getObjectByName('Roof').material.uuid,
  chimney: !!parts.Chimney,
  chimneyIsNotWall: parts.Chimney
      ? c.getObjectByName('Chimney').material.uuid
        !== c.getObjectByName('Walls').material.uuid : null,
  litNames: Object.keys(census(cottage({ w: W, d: D, h: H, dormers: 2,
      lit: 1, rand: mk(23) })).parts).sort(),
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
    assert m["chimney"] and m["chimneyIsNotWall"], (
        "the stack is limewash, not masonry", m["names"])


def test_cottage_keeps_its_origin_and_silhouette(cot):
    """Rests on y=0, faces -Z, and the roof's high point is the ridge."""
    m = cot
    assert abs(m["baseY"]) < 0.01, m["baseY"]
    assert m["forward"] == "-Z"
    assert abs(m["topY"] - m["ridgeY"]) < 1.4, (m["topY"], m["ridgeY"])


def test_a_village_can_afford_a_cottage(cot):
    """Real openings on four elevations plus two dormers, still cheap enough
    to place by the dozen."""
    m = cot
    assert m["tris"] < 2600, f"{m['tris']} triangles for one cottage"


def test_cottage_lit_rooms_are_their_own_meshes(cot):
    """Its lit panes used to be named `LitGlazing`, which walks straight into
    mirror.js `glazeFacade`'s `/glaz|pane/i` sweep."""
    m = cot
    lit = [n for n in m["litNames"] if n.startswith("LitRooms")]
    assert lit, m["litNames"]
    assert not [n for n in m["litNames"] if "Glaz" in n and n.startswith("Lit")]


# ------------------------------------------------- tower, clutter, district

def test_a_tower_steps_in_as_it_rises():
    """Towers SET BACK; a stack of equal slabs is a layer cake, and each
    tier stays a `block()` so it keeps real openings."""
    out = _measure("""
const t = tower({ w: 22, d: 20, h: 90, tiers: 3, rand: mk(9) });
t.updateMatrixWorld(true);
const tiers = [];
for (let i = 1; i <= 3; i++) {
  const g = t.getObjectByName('Tier' + i);
  const b = new THREE.Box3().setFromObject(g);
  tiers.push({ y: b.min.y, top: b.max.y, w: b.max.x - b.min.x,
               glazed: !!g.getObjectByName('Glazing') });
}
const whole = new THREE.Box3().setFromObject(t);
console.log(JSON.stringify({ tiers, baseY: whole.min.y,
    height: whole.max.y - whole.min.y, crown: !!t.getObjectByName('Crown') }));
""")
    tiers = out["tiers"]
    assert abs(out["baseY"]) < 0.01, out["baseY"]
    for lo, hi in zip(tiers, tiers[1:], strict=False):
        assert hi["w"] < lo["w"] * 0.95, ("a tier did not step in", tiers)
        assert hi["y"] >= lo["top"] - 1.5, ("a tier floats", tiers)
        # Lower tiers are taller: the real setback ratio is roughly halving.
        assert (lo["top"] - lo["y"]) > (hi["top"] - hi["y"]), tiers
    assert all(t["glazed"] for t in tiers), tiers
    assert out["crown"]


def test_roof_clutter_lands_on_the_roof_and_varies():
    """A bare flat roof is the most visible emptiness in an aerial shot after
    the ground plane, and three identical grey boxes on one axis read as a
    copy rather than as plant replaced piecemeal over decades."""
    out = _measure("""
const b = block({ w: 18, d: 16, h: 20, rand: mk(4) });
b.updateMatrixWorld(true);
const roofY = new THREE.Box3().setFromObject(b).max.y;
roofClutter(b, { rand: mk(8), tanks: 1, units: 4 });
b.updateMatrixWorld(true);
const g = b.getObjectByName('RoofClutter');
const box = new THREE.Box3().setFromObject(g);
const yaws = new Set(), colors = new Set();
let shadows = true;
g.traverse((o) => { if (o.isMesh) {
  yaws.add(o.rotation.y.toFixed(3));
  colors.add(o.material.color.getHex());
  if (!o.castShadow) shadows = false; } });
console.log(JSON.stringify({ minY: box.min.y, roofY, yaws: yaws.size,
    colors: colors.size, shadows }));
""")
    assert out["minY"] >= out["roofY"] - 0.01, (out["minY"], out["roofY"])
    assert out["yaws"] >= 3, ("every HVAC unit on one axis", out)
    assert out["colors"] >= 3, ("every unit the same paint", out)
    assert out["shadows"]


def test_a_district_is_a_layout_not_a_grid_of_stamps():
    """Streets are real gaps, blocks subdivide into lots, and the caller gets
    the road centrelines back so traffic and road surfaces can use them."""
    out = _measure("""
const f = cityFabric({ rand: mk(53), width: 460, depth: 300, blockW: 50,
    blockD: 60, streetW: 12, avenueW: 22, density: 0.8,
    skyline: (x, z) => 1 + Math.exp(-(x * x + z * z) / 8000) });
f.group.updateMatrixWorld(true);
const heights = [], widths = new Set();
for (const b of f.group.children) {
  const box = new THREE.Box3().setFromObject(b);
  heights.push(box.max.y - box.min.y);
  widths.add(b.getObjectByName('Walls').material.color.getHex());
}
heights.sort((a, b) => a - b);
console.log(JSON.stringify({
  buildings: f.buildings,
  footprintFrac: f.footprintFrac,
  streetsZ: f.group.userData.streets.alongZ.length,
  streetsX: f.group.userData.streets.alongX.length,
  avenue: Math.max(...f.group.userData.streets.alongZ.map((r) => r.width)),
  street: Math.min(...f.group.userData.streets.alongZ.map((r) => r.width)),
  hMin: heights[0], hMax: heights[heights.length - 1],
  hMed: heights[Math.floor(heights.length / 2)],
  wallTints: widths.size,
}));
""")
    assert out["buildings"] > 20, out
    assert 0.1 < out["footprintFrac"] < 0.8, out
    assert out["streetsZ"] >= 2 and out["streetsX"] >= 2, out
    assert out["avenue"] > out["street"], ("no avenue rhythm", out)
    # A skyline function must actually shape the district.
    assert out["hMax"] > out["hMed"] * 1.5 > out["hMin"], out
    assert out["wallTints"] >= 4, ("one colour for the whole city", out)
