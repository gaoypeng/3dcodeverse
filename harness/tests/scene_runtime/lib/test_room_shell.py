"""An interior plan gets its enclosure from the skeleton: `ScenePlan.interior` names the case,
`roomShell` builds walls and ceiling on the bounds' faces, env.js carries it."""
from __future__ import annotations

import pytest
from _probe import measure

from codeverse3d.contracts.common import Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.languages.scene_threejs import _env_for_plan
from codeverse3d.tracks.planner import plan_example

_SHELL = """
import * as THREE from 'three';
import { roomShell } from './lib/environment.js';
const g = roomShell({ center: [0, 3, 0], extents: [14, 6, 16], thickness: 0.3,
                      openings: [{ face: 'west', center: [8, 2.5], size: [6, 3] }] });
const names = g.children.map((m) => m.name);
const placement = g.userData.placement;
const box = new THREE.Box3().setFromObject(g);
const west = g.children.filter((m) => m.name.startsWith('Wall_west'));
// the opening: no west panel covers (x = -7.15, y = 2.5, z = 0) — 8 m along the face from z = -8
const covered = west.some((m) => new THREE.Box3().setFromObject(m).containsPoint(new THREE.Vector3(-7.15, 2.5, 0)));
const sill = west.some((m) => new THREE.Box3().setFromObject(m).containsPoint(new THREE.Vector3(-7.15, 0.5, 0)));
console.log(JSON.stringify({ names, placement, min: box.min.toArray(), max: box.max.toArray(), westPanels: west.length, covered, sill,
  shadows: g.children.every((m) => m.castShadow && m.receiveShadow) }));
"""


def test_the_shell_is_walls_and_ceiling_on_the_bounds_faces_with_openings_cut():
    got = measure(_SHELL, ("environment.js", "sky.js"))
    assert "Ceiling" in got["names"] and "Wall_east" in got["names"] and "Wall_north" in got["names"], got["names"]
    assert got["westPanels"] == 4 and got["covered"] is False and got["sill"] is True, got   # span, sill, lintel, span
    # walls are OUTSIDE the bounds (inner face == bounds face), the ceiling sits on top
    assert got["min"][0] == pytest.approx(-7.3, abs=1e-6) and got["max"][0] == pytest.approx(7.3, abs=1e-6)
    assert got["min"][1] == pytest.approx(0.0, abs=1e-6) and got["max"][1] == pytest.approx(6.3, abs=1e-6)
    assert got["shadows"]
    # the enclosure is not a placed thing (loop 22's crypt: "RoomShell floating 39.8 m" over a sunk terrain)
    assert got["placement"] == "free", got


def test_the_skeleton_env_carries_the_shell_only_for_an_interior_plan():
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    assert plan.interior is False
    outdoor = _env_for_plan(plan)
    assert "export const INTERIOR = null;" in outdoor
    inside = _env_for_plan(plan.model_copy(update={"interior": True}))
    c, e = plan.bounds.center, plan.bounds.extents
    assert f"export const INTERIOR = {{ center: [{c[0]}, {c[1]}, {c[2]}], extents: [{e[0]}, {e[1]}, {e[2]}], openings: [] }};" in inside
    assert "if (INTERIOR) group.add(roomShell(INTERIOR));" in inside
    glass = _env_for_plan(plan.model_copy(update={"interior": True, "glazed": True}))
    assert "openings: [], glazed: true };" in glass
    assert "if (!INTERIOR || INTERIOR.glazed) group.add(makeOutskirts(" in glass   # the land outside is built


_GLASS = """
import * as THREE from 'three';
import { roomShell } from './lib/environment.js';
const g = roomShell({ center: [0, 5, 0], extents: [20, 10, 16], glazed: true,
                      openings: [{ face: 'south', center: [10, 1.5], size: [3, 3] }] });
g.updateMatrixWorld(true);
const panes = g.children.filter((m) => !m.isInstancedMesh);
const frame = g.children.find((m) => m.name === 'Frame');
// rays from a floor grid toward a high sun: the share a shadow caster stops
const lit = (shell) => {
  shell.updateMatrixWorld(true);
  const sun = new THREE.Vector3(1, 1.4, 0.5).normalize();
  let blocked = 0, n = 0;
  for (let x = -8; x <= 8; x += 1.7) for (let z = -6; z <= 6; z += 1.3) {
    const rc = new THREE.Raycaster(new THREE.Vector3(x, 0.01, z), sun);
    n += 1;
    if (rc.intersectObjects(shell.children, false).some((h) => h.object.castShadow)) blocked += 1;
  }
  return blocked / n;
};
const shaded = lit(g), opaque = lit(roomShell({ center: [0, 5, 0], extents: [20, 10, 16] }));
console.log(JSON.stringify({
  names: g.children.map((m) => m.name),
  paneShadow: panes.some((m) => m.castShadow),
  seeThrough: panes.every((m) => m.material.transparent && m.material.depthWrite === false),
  bars: frame ? frame.count : 0, frameShadow: !!(frame && frame.castShadow), shaded, opaque,
}));
"""


def test_a_glazed_shell_is_glass_on_a_frame_grid_that_lets_the_sun_in():
    """The conservatory's round 0 (ab_verdict 2026-09-22, live_wave2): an opaque shell around a
    glass house — "a solid white box encloses the dome, blocking the exterior view and lighting"."""
    got = measure(_GLASS, ("environment.js", "sky.js"))
    assert "Roof" in got["names"] and "Ceiling" not in got["names"], got["names"]   # the overview rig keeps a glass roof
    assert "Wall_east" in got["names"] and "Frame" in got["names"], got["names"]
    assert got["seeThrough"] and not got["paneShadow"], got
    assert got["bars"] > 40 and got["frameShadow"], got
    # between the bars the sun reaches the floor; the opaque shell shades all of it
    assert got["opaque"] == 1.0 and got["shaded"] < 0.3, got


_THIN_PANELS = """
import * as THREE from 'three';
import { roomShell } from './lib/environment.js';
// two south windows 0.1 m apart with 0.2 m sills: a strip and sills narrower than the 0.3 m wall
const g = roomShell({ center: [0, 5, 0], extents: [20, 10, 16], glazed: true, thickness: 0.3,
                      openings: [{ face: 'south', center: [5, 2.0], size: [2, 3.6] },
                                 { face: 'south', center: [7.1, 2.0], size: [2, 3.6] }] });
const south = g.children.filter((m) => m.name.startsWith('Wall_south')).map((m) => {
  const s = new THREE.Vector3(); new THREE.Box3().setFromObject(m).getSize(s); return s.toArray(); });
console.log(JSON.stringify({ south }));
"""


def test_glazed_panes_are_thin_along_the_wall_even_when_narrower_than_it():
    """/code-review 2026-09-24: the glass (and its frame) took the SMALLEST size as the thickness axis,
    so a 0.2 m sill became a horizontal plate and a 0.1 m strip a fin through the wall."""
    got = measure(_THIN_PANELS, ("environment.js", "sky.js"))
    assert len(got["south"]) >= 5, got
    assert all(abs(size[2] - 0.02) < 1e-6 for size in got["south"]), got      # z is the south wall's thickness
    assert any(size[0] < 0.3 for size in got["south"]) and any(size[1] < 0.3 for size in got["south"]), got
