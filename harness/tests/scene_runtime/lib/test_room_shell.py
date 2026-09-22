"""An interior plan gets its enclosure from the skeleton (2026-09-07).

Measured over six interior runs of the day (boat workshop x4, clockmaker x2): the plan
described the premise ("inside an enclosed workshop"), the zones dressed their boxes, the
env built ground, sky and sun — and nobody built walls or a roof.  Every one was judged
"not enclosed, a diorama on a flat plane, tool racks floating at a missing wall" (0.0-0.3)
until a refine round built them; a bare one-file scene of the same brief built the room
first and scored 0.82.  `ScenePlan.interior` names the case, `roomShell` builds it on the
bounds' faces, the skeleton's env.js carries it before any session starts.
"""
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
