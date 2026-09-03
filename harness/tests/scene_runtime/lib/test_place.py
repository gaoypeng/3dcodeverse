"""place.js — cameras and placements ship as derived quantities.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_place_lib.py).  The reference scenario is kept whole: a bumpy
ground, a camera requested 1 m UNDER it and 0.4 m from a wall, and a 10x24x8
tower to bbox-fit — the measured failure shape (5 of 8 audited failing scenes
shipped cameras buried under their own terrain or pressed into a facade).  It
needed no retargeting: place.js is arithmetic over three.js objects, with no
renderer contract in it at all.

Everything from ``test_an_interior_camera_stays_under_its_own_ceiling`` down
covers what the port FIXED, each one measured on this host before the change:

* the down-ray that grounds things started far overhead and took its NEAREST
  hit, i.e. the top of the column — so anything with a roof over it flew UP
  onto that roof.  A crate under a 3 m arch seated at y 3.62, and an interior
  camera handed its room group (which is exactly what the catalog tells
  composers to pass indoors) shipped at eye 4.60 in a room whose ceiling tops
  out at 3.00.  Both now resolve the surface UNDER the body, and only fall
  back to the lowest surface above it when there is nothing under it at all —
  which is what still lifts an object authored at y = 0 onto a hillside;
* ``establishingShot`` never checked whether anything stood between the eye
  and the hero: a hero behind a 9 m ridge shipped a frame filled by ridge at
  1.19 m of an 8.92 m shot.  It now slides along the fit sphere — up first,
  then around, re-fitting at each candidate — instead of retreating the way
  ``shot()`` does, because backing away shrinks the hero the shot exists to
  show;
* ``route()`` rolled wheels from ``s``, which only ever grows, so a cart on
  the return leg of a ping-pong path drove backwards with its wheels spinning
  forwards (-0.4 m of travel against +0.8 rad of wheel);
* ``alongPath`` divided an OPEN path by n instead of n-1, so a row stopped a
  whole gap short of its own last waypoint (6 posts over a 12 m quay ended at
  x = 10, and the missing post is visible in the showcase frame).
"""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("place.js",)


def _measure(script: str) -> dict:
    return measure(script, _LIBS)


# --- the reference's own probe, kept whole -------------------------------

_PROBE = """
import * as THREE from 'three';
import { shot, establishingShot } from './lib/place.js';

// A bumpy ground whose surface sits around y = 3.
const geo = new THREE.PlaneGeometry(80, 80, 40, 40);
geo.rotateX(-Math.PI / 2);
const gp = geo.attributes.position;
for (let i = 0; i < gp.count; i++) {
  gp.setY(i, 3 + Math.sin(gp.getX(i) * 0.35) * 1.2
             + Math.cos(gp.getZ(i) * 0.25) * 0.8);
}
geo.computeVertexNormals();
const ground = new THREE.Mesh(geo, new THREE.MeshBasicMaterial());
ground.updateMatrixWorld(true);

const groundAt = (x, z) => {
  const r = new THREE.Raycaster(new THREE.Vector3(x, 200, z),
                                new THREE.Vector3(0, -1, 0));
  return r.intersectObject(ground, true)[0].point.y;
};
const surfaceY = groundAt(0, 0);

// A wall directly between the camera and its subject, 0.4 m away.
const wall = new THREE.Mesh(new THREE.BoxGeometry(30, 40, 0.5),
                            new THREE.MeshBasicMaterial());
wall.position.set(0, surfaceY + 20, -0.65);   // near face at z = -0.4
wall.updateMatrixWorld(true);

// The measured failure shape: requested 1 m BELOW the surface and
// 0.4 m from a wall that fills the frame.
const cam = shot('street', [0, surfaceY - 1, 0], [0, surfaceY + 2, -30],
                 55, [ground, wall]);

// Re-check the shipped entry the way the census would.
const cpos = new THREE.Vector3(...cam.position);
const cdir = new THREE.Vector3(...cam.lookAt).sub(cpos);
const cdist = cdir.length();
const ray = new THREE.Raycaster(cpos, cdir.clone().normalize());
ray.far = cdist;
const hits = ray.intersectObjects([ground, wall], true);

// Hero fit: a 10 x 24 x 8 tower standing on the ground.
const hero = new THREE.Mesh(new THREE.BoxGeometry(10, 24, 8),
                            new THREE.MeshBasicMaterial());
hero.position.set(0, groundAt(0, 0) + 12, 0);
hero.updateMatrixWorld(true);
const est = establishingShot('establishing', hero,
    { azDeg: 40, elDeg: 25, fov: 45, coverage: 0.8, surfaces: ground });
const ecam = new THREE.PerspectiveCamera(est.fov, 16 / 9, 0.1, 1e6);
ecam.position.set(...est.position);
ecam.lookAt(new THREE.Vector3(...est.lookAt));
ecam.updateMatrixWorld(true);
const hbox = new THREE.Box3().setFromObject(hero);
let inside = 0;
let maxExt = 0;
for (const cx of [hbox.min.x, hbox.max.x])
  for (const cy of [hbox.min.y, hbox.max.y])
    for (const cz of [hbox.min.z, hbox.max.z]) {
      const ndc = new THREE.Vector3(cx, cy, cz).project(ecam);
      if (Math.abs(ndc.x) <= 1 && Math.abs(ndc.y) <= 1
          && Math.abs(ndc.z) <= 1) inside++;
      maxExt = Math.max(maxExt, Math.abs(ndc.x), Math.abs(ndc.y));
    }

// A grazing establishing shot of a small hero must still clear the
// ground under the eye.
const small = new THREE.Mesh(new THREE.BoxGeometry(2, 2, 2),
                             new THREE.MeshBasicMaterial());
small.position.set(10, groundAt(10, 0) + 1, 0);
small.updateMatrixWorld(true);
const low = establishingShot('low', small,
    { azDeg: 10, elDeg: 2, fov: 45, coverage: 0.8, surfaces: ground });

console.log(JSON.stringify({
  surfaceY,
  camY: cam.position[1],
  groundAtCam: groundAt(cam.position[0], cam.position[2]),
  firstHit: hits.length ? hits[0].distance : null,
  clearNeeded: Math.max(2, cdist * 0.05),
  inside,
  maxExt,
  estY: est.position[1],
  lowY: low.position[1],
  lowGround: groundAt(low.position[0], low.position[2]),
}));
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    """One node launch of _PROBE, shared by every test that reads it."""
    return _measure(_PROBE)


def test_a_buried_camera_is_lifted_above_the_ground_it_shoots_from(probe):
    """A camera below its own terrain photographs the unlit backside.

    The Santorini failure: 3 of 5 authored cameras below the exported
    h(x, z), including the establishing shot.
    """
    m = probe
    assert m["camY"] > m["surfaceY"] + 1.5, m
    assert m["camY"] > m["groundAtCam"] + 1.5, m


def test_a_wall_pressed_camera_backs_off_to_clear_frame(probe):
    """Nothing may fill the frame from closer than max(2m, 5% dist)."""
    m = probe
    assert (m["firstHit"] is None
            or m["firstHit"] >= m["clearNeeded"] - 1e-6), m


def test_establishing_shot_frames_the_hero(probe):
    """>= 6 of 8 bbox corners must land inside NDC — the fitted dolly."""
    m = probe
    assert m["inside"] >= 6, m
    assert 0.4 < m["maxExt"] <= 1.05, m


def test_a_low_establishing_shot_still_clears_the_ground(probe):
    """The fit dolly can land the eye underground; the seat ray must
    lift it, exactly as shot() does."""
    m = probe
    assert m["lowY"] >= m["lowGround"] + 1.6 - 0.01, m


# --- what the port fixed -------------------------------------------------

_PORT = """
import * as THREE from 'three';
import { shot, establishingShot, seat, alongPath, route, crowdOn }
  from './lib/place.js';

const basic = () => new THREE.MeshBasicMaterial();
const out = {};

// A room with a real ceiling — the catalog tells composers to hand the
// whole room group to shot(), not just the floor.
const room = new THREE.Group();
const floor = new THREE.Mesh(new THREE.BoxGeometry(6, 0.2, 6), basic());
floor.position.y = -0.1;                       // top at y = 0
room.add(floor);
const ceil = new THREE.Mesh(new THREE.BoxGeometry(6, 0.2, 6), basic());
ceil.position.y = 2.9;                          // ceiling slab, top at 3.0
room.add(ceil);
const backWall = new THREE.Mesh(new THREE.BoxGeometry(6, 2.8, 0.2), basic());
backWall.position.set(0, 1.4, -2.9);
room.add(backWall);
room.updateMatrixWorld(true);
const interior = shot('interior', [0, 1.65, 2.0], [0, 1.2, -2.6], 50, [room]);
out.interiorEyeY = interior.position[1];
out.ceilingTop = 3.0;
// ... and a room too LOW for the 1.6 m clearance still keeps the eye in it.
const low = new THREE.Group();
const lowFloor = floor.clone(); low.add(lowFloor);
const lowCeil = new THREE.Mesh(new THREE.BoxGeometry(6, 0.2, 6), basic());
lowCeil.position.y = 1.7;                       // ceiling top at 1.8
low.add(lowCeil);
low.updateMatrixWorld(true);
out.lowRoomEyeY = shot('low', [0, 1.2, 2.0], [0, 1.0, -2.0], 50,
                       [low]).position[1];

// seat(): a crate under an arch stays under the arch.
const gnd = new THREE.Mesh(new THREE.BoxGeometry(400, 0.2, 400), basic());
gnd.position.y = -0.1;                          // top at y = 0
gnd.updateMatrixWorld(true);
const arch = new THREE.Mesh(new THREE.BoxGeometry(4, 0.3, 4), basic());
arch.position.set(0, 3, 0);                     // underside 2.85, top 3.15
arch.updateMatrixWorld(true);
const crate = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), basic());
crate.position.set(0, 0.5, 0);
seat(crate, [gnd, arch]);
out.crateUnderArchY = crate.position.y;
// the quay/seabed rule is unchanged: what is UNDER the object wins.
const quay = new THREE.Mesh(new THREE.BoxGeometry(8, 1, 8), basic());
quay.position.set(0, 1.5, 0);                   // deck top at 2.0
quay.updateMatrixWorld(true);
const seabed = new THREE.Mesh(new THREE.BoxGeometry(40, 0.2, 40), basic());
seabed.position.y = -3.1;
seabed.updateMatrixWorld(true);
const barrel = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.8, 0.8), basic());
barrel.position.set(0, 6, 0);
seat(barrel, [seabed, quay]);
out.barrelOnQuayY = barrel.position.y;
// an object authored at y = 0 on a hillside — nothing under it at all,
// so the LOWEST surface above wins and the hut is lifted onto the hill.
const hill = new THREE.Mesh(new THREE.BoxGeometry(20, 0.4, 20), basic());
hill.position.set(600, 12, 0);                  // top at 12.2, off the plate
hill.updateMatrixWorld(true);
const hut = new THREE.Mesh(new THREE.BoxGeometry(2, 2, 2), basic());
hut.position.set(600, 1, 0);
seat(hut, [gnd, hill]);
out.hutOnHillY = hut.position.y;

// establishingShot(): a ridge between the eye and the hero.
const hero = new THREE.Mesh(new THREE.BoxGeometry(3, 4, 3), basic());
hero.position.set(0, 2, 0);
hero.updateMatrixWorld(true);
const ridge = new THREE.Mesh(new THREE.BoxGeometry(40, 9, 3), basic());
ridge.position.set(0, 4.5, 6);                  // a wall 6 m to +z of it
ridge.updateMatrixWorld(true);
const est = establishingShot('est', hero, { azDeg: 90, elDeg: 14, fov: 45,
  coverage: 0.7, surfaces: [gnd, ridge] });
const ep = new THREE.Vector3(...est.position);
const ec = new THREE.Vector3(...est.lookAt);
const dir = ec.clone().sub(ep);
const dist = dir.length();
const ray = new THREE.Raycaster(ep, dir.clone().normalize());
ray.far = dist;
const rh = ray.intersectObjects([gnd, ridge], true);
out.estBlockedAt = rh.length ? rh[0].distance : null;
out.estClearNeeded = Math.max(2, dist * 0.05);
const ecam = new THREE.PerspectiveCamera(est.fov, 16 / 9, 0.1, 1e6);
ecam.position.copy(ep); ecam.lookAt(ec); ecam.updateMatrixWorld(true);
const hb = new THREE.Box3().setFromObject(hero);
let inside = 0;
for (const cx of [hb.min.x, hb.max.x])
  for (const cy of [hb.min.y, hb.max.y])
    for (const cz of [hb.min.z, hb.max.z]) {
      const n = new THREE.Vector3(cx, cy, cz).project(ecam);
      if (Math.abs(n.x) <= 1 && Math.abs(n.y) <= 1 && Math.abs(n.z) <= 1) {
        inside++;
      }
    }
out.estInside = inside;

// route(): ping-pong wheels roll with the direction of travel.
const axle = new THREE.Object3D();
const cart = new THREE.Object3D();
const pp = route([[0, 0, 0], [10, 0, 0]],
    { closed: false, speed: 2, wheels: [{ node: axle, radius: 0.5 }] });
const at = (t) => { pp.poseAt(t, cart);
                    return { x: cart.position.x, rot: axle.rotation.x }; };
const o1 = at(1.0), o2 = at(1.2);              // outbound (turns at t = 5)
const b1 = at(6.0), b2 = at(6.2);              // return leg
out.outDx = o2.x - o1.x;
out.outDrot = o2.rot - o1.rot;
out.backDx = b2.x - b1.x;
out.backDrot = b2.rot - b1.rot;
out.wheelRadius = 0.5;
// a closed loop must not snap the wheel at the lap boundary.
const axle2 = new THREE.Object3D();
const loop = route([[0, 0, 0], [5, 0, 0], [5, 0, 5], [0, 0, 5]],
    { closed: true, speed: 3, wheels: [{ node: axle2, radius: 0.4 }] });
const dummy = new THREE.Object3D();
loop.poseAt(loop.length / 3 - 0.001, dummy);
const r1 = axle2.rotation.x;
loop.poseAt(loop.length / 3 + 0.001, dummy);
out.loopRotJump = Math.abs(axle2.rotation.x - r1);

// alongPath(): an open path spans end to end, a closed one does not.
const open = alongPath([[0, 0, 0], [12, 0, 0]], 6, { closed: false });
out.openFirstX = open[0].position.x;
out.openLastX = open[open.length - 1].position.x;
out.openN = open.length;
const ring = alongPath([[0, 0, 0], [6, 0, 0], [6, 0, 6], [0, 0, 6]], 8, {});
out.ringN = ring.length;
out.ringFirstToLast = ring[0].position.distanceTo(ring[7].position);
out.ringStep = ring[0].position.distanceTo(ring[1].position);

// crowdOn(): walkers on one route never bunch, at any time.
const stalls = new THREE.Group();
for (const [x, z] of [[3, 1], [-2, 2], [0, -3]]) {
  const s = new THREE.Mesh(new THREE.BoxGeometry(1.2, 1.0, 1.2), basic());
  s.name = 'stall';
  s.position.set(x, 0.5, z);
  stalls.add(s);
}
stalls.updateMatrixWorld(true);
const lane = route([[6, 0, 0], [0, 0, 6], [-6, 0, 0], [0, 0, -6]],
    { speed: 1.2, avoid: stalls, clearance: 0.5 });
const figs = [];
for (let i = 0; i < 7; i++) figs.push(new THREE.Object3D());
const crowd = crowdOn(lane, figs, { spacing: 2, laneStep: 0.7 });
let minGap = Infinity;
for (let k = 0; k < 240; k++) {
  crowd.tick(k * 0.25);
  for (let i = 0; i < figs.length; i++)
    for (let j = i + 1; j < figs.length; j++)
      minGap = Math.min(minGap, figs[i].position.distanceTo(figs[j].position));
}
out.crowdMinGap = minGap;
// the avoided path keeps its distance from the stalls it was routed around
let minStall = Infinity;
for (let k = 0; k < 400; k++) {
  const p = lane.poseAt(k * (lane.length / 400) / 1.2).position;
  for (const s of stalls.children) {
    const b = new THREE.Box3().setFromObject(s);
    const dx = Math.max(b.min.x - p.x, 0, p.x - b.max.x);
    const dz = Math.max(b.min.z - p.z, 0, p.z - b.max.z);
    minStall = Math.min(minStall, Math.hypot(dx, dz));
  }
}
out.minStallGap = minStall;

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def port() -> dict:
    """One node launch of _PORT, shared by the port-fix tests."""
    return _measure(_PORT)


def test_an_interior_camera_stays_under_its_own_ceiling(port):
    """Handed the room group, the eye stayed 1.6 m above the CEILING and
    the shot shipped from on top of the roof (measured 4.60 in a 3.00 m
    room).  It must stand on the floor instead, and a room too low for
    the clearance keeps it inside rather than ejecting it."""
    m = port
    assert m["interiorEyeY"] < m["ceilingTop"] - 0.2, m
    assert m["interiorEyeY"] >= 1.6 - 1e-6, m
    assert 0.3 <= m["lowRoomEyeY"] <= 1.8 - 0.25 + 1e-6, m


def test_seat_puts_a_body_on_what_is_under_it_not_on_the_roof(port):
    """The old down-ray took the top of the column: a crate under a 3 m
    arch seated at 3.62.  The quay-beats-seabed and lift-onto-the-hill
    behaviours both have to survive the fix."""
    m = port
    assert abs(m["crateUnderArchY"] - 0.47) < 0.02, m       # 0.5 - bite
    assert abs(m["barrelOnQuayY"] - (2.0 + 0.4 - 0.03)) < 0.02, m
    assert abs(m["hutOnHillY"] - (12.2 + 1.0 - 0.03)) < 0.02, m


def test_an_establishing_shot_is_not_a_photograph_of_the_ridge(port):
    """A hero behind a 9 m ridge shipped a frame blocked at 1.19 m of an
    8.92 m shot.  Sliding along the fit sphere has to clear the view AND
    keep the hero framed — retreating would only shrink it."""
    m = port
    assert (m["estBlockedAt"] is None
            or m["estBlockedAt"] >= m["estClearNeeded"] - 1e-6), m
    assert m["estInside"] >= 6, m


def test_ping_pong_wheels_roll_the_way_the_cart_is_going(port):
    """`s` only grows, so the return leg drove backwards with the wheels
    spinning forwards.  Roll is the arc distance COVERED — and a closed
    loop must still not snap at the lap boundary."""
    m = port
    assert m["outDx"] > 0 and m["outDrot"] > 0, m
    assert m["backDx"] < 0 and m["backDrot"] < 0, m
    for dx, drot in ((m["outDx"], m["outDrot"]), (m["backDx"], m["backDrot"])):
        assert abs(drot - dx / m["wheelRadius"]) < 2e-3, m   # no slip
    assert m["loopRotJump"] < 0.05, m


def test_an_open_path_reaches_its_last_waypoint(port):
    """6 posts over a 12 m quay ended at x = 10 — a whole gap short.  A
    CLOSED path keeps dividing by n: its last sample belongs one step
    before the start, not on top of it."""
    m = port
    assert m["openN"] == 6, m
    assert abs(m["openFirstX"]) < 1e-6, m
    assert abs(m["openLastX"] - 12) < 1e-6, m
    assert m["ringN"] == 8, m
    assert abs(m["ringFirstToLast"] - m["ringStep"]) < 0.35 * m["ringStep"], m


def test_a_crowd_on_one_route_never_bunches_or_walks_through_a_stall(port):
    """The two guarantees the catalog sells: deterministic lane+phase
    keeps walkers apart forever, and `avoid` re-projects the path clear
    of furniture-scale solids at build time."""
    m = port
    assert m["crowdMinGap"] >= 0.6, m
    assert m["minStallGap"] >= 0.45, m
