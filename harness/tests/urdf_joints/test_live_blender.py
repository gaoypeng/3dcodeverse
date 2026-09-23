"""Real Blender builds (marked ``blender``): cabinet+door, drawer, bad pivot, yourdfpy."""

from __future__ import annotations

import numpy as np
import pytest

from codeverse3d.config import get_settings
from codeverse3d.languages.urdf import UrdfBlenderRuntime
from codeverse3d.spatial.joints_model import fk, link_world_meshes, load_urdf
from codeverse3d.spatial.joints_sweep import motion_direction_check, sweep_gate
from codeverse3d.workspace import Workspace

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
    gate, _ = sweep_gate(ws)   # the round's joint_sweep gate on this build
    assert gate.passed and not gate.findings, [f.message for f in gate.findings]
    # loads in yourdfpy with the same FK
    yourdfpy = pytest.importorskip("yourdfpy")
    u = yourdfpy.URDF.load(str(ws.artifacts / "robot.urdf"), load_meshes=True)
    u.update_cfg({"hinge": 1.0})
    assert np.allclose(u.get_transform("door", "body"), fk(r, {"hinge": 1.0})["door"], atol=1e-6)


@needs_blender
def test_rest_shifted_skeleton_builds_clean(tmp_path, drawer_plan):
    """Plan with rest ≠ 0 and the bbox authored AT that rest (drawer 0.1 m out): the
    skeleton's shifted limits (-0.1 .. 0.2) put q=0 at the authored pose and @lower at the
    closed position — no penetration anywhere in the sweep."""
    plan = drawer_plan.model_copy(deep=True)
    plan.parts[1].bbox.center = (0, -0.31, 0.45)       # front panel 0.1 m out of the carcass
    plan.joints[0].pivot = (0, -0.31, 0.45)
    plan.joints[0].rest = 0.1
    ws = Workspace(tmp_path / "drawer_rest").create()
    rt = UrdfBlenderRuntime()
    rt.skeleton(ws, plan)
    assert 'lower="-0.1" upper="0.2"' in (ws.src / "robot.urdf").read_text()
    res = rt.build(ws)
    assert res.ok, res.error_message
    r = load_urdf(ws.artifacts / "robot.urdf", ws.artifacts / "meshes")
    closed = link_world_meshes(r, {"slide": -0.1})["drawer"].bounds
    assert np.isclose(closed[1][1], -0.20, atol=1e-6)  # back face of the panel flush with the carcass front
    assert not [f for f in sweep_gate(ws)[0].findings if f.data.get("kind") == "penetration"]


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
    assert not [f for f in sweep_gate(ws)[0].findings if f.data.get("kind") == "penetration"]


@needs_blender
def test_bad_pivot_is_caught(tmp_path, cabinet_plan):
    ws = Workspace(tmp_path / "bad").create()
    rt = UrdfBlenderRuntime()
    rt.skeleton(ws, cabinet_plan)
    u = ws.src / "robot.urdf"
    # hinge moved to the door's middle: the door must sweep into the body
    u.write_text(u.read_text().replace('<origin xyz="-0.29 -0.2 0" rpy="0 0 0"/>  <!-- pivot', '<origin xyz="0 -0.2 0" rpy="0 0 0"/>  <!-- pivot')
                 .replace('xyz="0.29 0.2 0"', 'xyz="0 0.2 0"').replace('xyz="0.49 -0.04 0.4"', 'xyz="0.2 -0.04 0.4"'))
    res = rt.build(ws)
    assert res.ok  # rest pose is still fine; the round's joint_sweep gate reports the defect
    gate, _ = sweep_gate(ws)
    pen = next(f for f in gate.errors if f.target == "body|door")
    assert pen.data["max_depth_m"] > 0.05 and {"hinge": 1.57} in pen.data["poses"]


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


@needs_blender
def test_wrapper_unsafe_link_name_is_a_build_error(tmp_path):
    """End-to-end: a traversal-shaped URDF link name fails the build before the agent
    script runs; meshes/ is never created, nothing lands outside it."""
    import json as _json
    import subprocess

    from codeverse3d.languages.urdf import WRAPPER

    (tmp_path / "robot.urdf").write_text('<robot name="r"><link name="../evil"/></robot>')
    (tmp_path / "model.py").write_text("import bpy\n")
    out = tmp_path / "art"
    blender = get_settings().resolve_blender()
    subprocess.run(
        [blender, "-b", "--factory-startup", "--python", str(WRAPPER), "--",
         "--script", str(tmp_path / "model.py"), "--urdf", str(tmp_path / "robot.urdf"),
         "--out", str(out)],
        capture_output=True, text=True, timeout=180, check=False)
    build = _json.loads((out / "build.json").read_text())
    assert not build["ok"] and build["error_type"] == "UnsafeLinkName"
    assert "../evil" in build["error_message"]
    assert not (out / "meshes").exists() and not (tmp_path / "evil.glb").exists()


@needs_blender
def test_links_script_that_calls_sys_exit_0_still_builds(tmp_path):
    """``sys.exit(0)`` ends the script, not the build: the objects it made are the links
    (the blender language's rule; this wrapper used to report it as a SystemExit error).
    The run is seeded like the blender language's, so ``random`` draws the same numbers."""
    import json as _json
    import subprocess

    from codeverse3d.languages.urdf import WRAPPER

    (tmp_path / "robot.urdf").write_text('<robot name="r"><link name="base"/></robot>')
    (tmp_path / "model.py").write_text(
        "import random, sys\nimport bpy\n"
        "bpy.ops.mesh.primitive_cube_add(size=0.2 + random.random())\n"
        "bpy.context.active_object.name = 'base'\n"
        "sys.exit(0)\n"
        "raise RuntimeError('never reached')\n")
    out = tmp_path / "art"
    subprocess.run(
        [get_settings().resolve_blender(), "-b", "--factory-startup", "--python", str(WRAPPER), "--",
         "--script", str(tmp_path / "model.py"), "--urdf", str(tmp_path / "robot.urdf"), "--out", str(out)],
        capture_output=True, text=True, timeout=180, check=False)
    build = _json.loads((out / "build.json").read_text())
    assert build["ok"] and build["error_type"] == "", build
    census = _json.loads((out / "census.json").read_text())
    import random

    random.seed(0)
    assert census["links"]["base"]["bbox_max"][0] == pytest.approx((0.2 + random.random()) / 2, abs=1e-5)
    assert (out / "meshes" / "base.glb").is_file()
    assert any("has no material" in w for w in census["warnings"])  # the blender language's census
