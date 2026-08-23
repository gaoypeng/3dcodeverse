"""Real Blender builds (marked ``blender``): cabinet+door, drawer, bad pivot, yourdfpy, sheet."""

from __future__ import annotations

import numpy as np
import pytest

from codeverse.config import get_settings
from codeverse.languages.urdf.runtime import UrdfBlenderRuntime
from codeverse.spatial.joints import (
    ARTICULATION_SHEET_NAME,
    blender_render_glb,
    fk,
    link_world_meshes,
    load_urdf,
    motion_direction_check,
    render_poses,
)
from codeverse.workspace import Workspace

pytestmark = pytest.mark.blender
needs_blender = pytest.mark.skipif(not get_settings().resolve_blender(), reason="no Blender binary")


@needs_blender
def test_cabinet_door_end_to_end(tmp_path, cabinet_plan):
    ws = Workspace(tmp_path / "cab").create()
    rt = UrdfBlenderRuntime()
    rt.skeleton(ws, cabinet_plan)
    assert rt.lint(ws).passed
    res = rt.build(ws)
    assert res.ok, res.error_message
    assert res.duration_ms < 30_000
    r = load_urdf(ws.artifacts / "robot.urdf", ws.artifacts / "meshes")
    assert r.link_order() == ["body", "door", "handle"]
    # door + handle swing to the front-left at the upper limit
    wm = link_world_meshes(r, {"hinge": 1.57})
    assert wm["door"].centroid[1] < -0.4 and wm["handle"].centroid[1] < -0.4
    assert motion_direction_check(r, "hinge", "front").ok
    art = res.census["articulation"]["summary"]
    assert art["max_penetration_m"] == 0.0 and art["floating_links"] == []
    # loads in yourdfpy with the same FK
    yourdfpy = pytest.importorskip("yourdfpy")
    u = yourdfpy.URDF.load(str(ws.artifacts / "robot.urdf"), load_meshes=True)
    u.update_cfg({"hinge": 1.0})
    assert np.allclose(u.get_transform("door", "body"), fk(r, {"hinge": 1.0})["door"], atol=1e-6)


@needs_blender
def test_drawer_prismatic(tmp_path, drawer_plan):
    ws = Workspace(tmp_path / "drawer").create()
    rt = UrdfBlenderRuntime()
    rt.skeleton(ws, drawer_plan)
    res = rt.build(ws)
    assert res.ok, res.error_message
    r = load_urdf(ws.artifacts / "robot.urdf", ws.artifacts / "meshes")
    c0 = link_world_meshes(r)["drawer"].centroid
    c1 = link_world_meshes(r, {"slide": 0.3})["drawer"].centroid
    assert np.allclose(c1 - c0, [0, -0.3, 0], atol=1e-6)
    assert res.census["articulation"]["summary"]["max_penetration_m"] == 0.0


@needs_blender
def test_bad_pivot_is_caught_and_sheet_renders(tmp_path, cabinet_plan):
    ws = Workspace(tmp_path / "bad").create()
    rt = UrdfBlenderRuntime()
    rt.skeleton(ws, cabinet_plan)
    u = ws.src / "robot.urdf"
    # hinge moved to the door's middle: the door must sweep into the body
    u.write_text(u.read_text().replace('<origin xyz="-0.29 -0.2 0" rpy="0 0 0"/>  <!-- pivot', '<origin xyz="0 -0.2 0" rpy="0 0 0"/>  <!-- pivot')
                 .replace('xyz="0.29 0.2 0"', 'xyz="0 0.2 0"').replace('xyz="0.49 -0.04 0.4"', 'xyz="0.2 -0.04 0.4"'))
    res = rt.build(ws)
    assert res.ok  # rest pose is still fine; the sweep reports the defect for the gate layer
    art = res.census["articulation"]
    assert art["summary"]["max_penetration_m"] > 0.05 and "hinge@upper" in art["summary"]["overlapping_poses"]
    assert any(f["severity"] == "error" and "body|door" in f["target"] for f in art["findings"])
    r = load_urdf(ws.artifacts / "robot.urdf", ws.artifacts / "meshes")
    out = render_poses(r, ws.artifacts / "renders" / "articulation", renderer=blender_render_glb)
    assert [label for label, _ in out] == ["rest", "hinge@upper"]
    assert (ws.artifacts / "renders" / "articulation" / ARTICULATION_SHEET_NAME).is_file()


@needs_blender
def test_script_error_and_missing_object(tmp_path, cabinet_plan):
    ws = Workspace(tmp_path / "err").create()
    rt = UrdfBlenderRuntime()
    rt.skeleton(ws, cabinet_plan)
    m = ws.src / "model.py"
    m.write_text(m.read_text() + "\nraise RuntimeError('boom')\n")
    res = rt.build(ws)
    assert not res.ok and res.error_type == "RuntimeError" and res.error_line == len(m.read_text().splitlines())
    m.write_text(m.read_text().replace("raise RuntimeError('boom')\n", "").replace("'handle'", "'Handle'"))
    res = rt.build(ws)
    assert not res.ok and res.error_type == "MissingLinkObjects" and "Handle" in res.error_message
