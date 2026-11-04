"""Real Blender builds (marked ``blender``): the cabinet end to end, and the links wrapper's typed failures."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codeverse3d.config import get_settings
from codeverse3d.languages.urdf import UrdfBlenderRuntime
from codeverse3d.spatial.joints_model import link_world_meshes, load_urdf
from codeverse3d.spatial.joints_sweep import motion_direction_check, sweep_gate
from codeverse3d.workspace import Workspace

pytestmark = pytest.mark.blender
needs_blender = pytest.mark.skipif(not get_settings().resolve_blender(), reason="no Blender binary")


@needs_blender
def test_cabinet_door_end_to_end_then_broken(tmp_path, cabinet_plan):
    ws = Workspace(tmp_path / "cab").create()
    rt = UrdfBlenderRuntime()
    rt.skeleton(ws, cabinet_plan)
    assert rt.lint(ws).passed
    res = rt.build(ws)
    assert res.ok, res.error_message
    r = load_urdf(ws.artifacts / "robot.urdf", ws.artifacts / "meshes")
    assert r.link_order() == ["body", "door", "handle"]
    wm = link_world_meshes(r, {"hinge": 1.57})
    assert wm["door"].centroid[1] < -0.4 and wm["handle"].centroid[1] < -0.4
    assert motion_direction_check(r, "hinge", "front").ok
    gate, _ = sweep_gate(ws)   # the round's joint_sweep gate on this build
    assert gate.passed and not gate.findings, [f.message for f in gate.findings]

    # the same workspace, broken: typed wrapper failures
    m = ws.src / "model.py"
    m.write_text(m.read_text() + "\nraise RuntimeError('boom')\n")
    res = rt.build(ws)
    assert not res.ok and res.error_type == "RuntimeError" and res.error_line == len(m.read_text().splitlines())
    m.write_text(m.read_text().replace("raise RuntimeError('boom')\n", "").replace("'handle'", "'Handle'"))
    res = rt.build(ws)
    assert not res.ok and res.error_type == "MissingLinkObjects" and "Handle" in res.error_message


def _wrapper_build(tmp_path, model: str) -> tuple[dict, Path]:
    """Run the links wrapper on a one-link robot whose model.py is ``model``; return build.json and the out dir."""
    from codeverse3d.languages.urdf import WRAPPER

    (tmp_path / "robot.urdf").write_text('<robot name="r"><link name="base"/></robot>')
    (tmp_path / "model.py").write_text(model)
    out = tmp_path / "art"
    subprocess.run([get_settings().resolve_blender(), "-b", "--factory-startup", "--python", str(WRAPPER), "--",
                    "--script", str(tmp_path / "model.py"), "--urdf", str(tmp_path / "robot.urdf"), "--out", str(out)],
                   capture_output=True, text=True, timeout=180, check=False)
    return json.loads((out / "build.json").read_text()), out


BASE = "bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5)); base = bpy.context.active_object; base.name = 'base'\n"


@needs_blender
def test_a_hidden_boolean_cutter_is_not_link_geometry(tmp_path):
    """Regression (audit 2026-09-24 N7): a parented hidden cutter was exported as a solid rod inside the link."""
    build, out = _wrapper_build(tmp_path, "import bpy\n" + BASE +
        "bpy.ops.mesh.primitive_cylinder_add(radius=0.2, depth=2, location=(0, 0, 0.5)); cut = bpy.context.active_object\n"
        "cut.name = 'HoleCutter'\nm = base.modifiers.new('hole', 'BOOLEAN'); m.object = cut; m.operation = 'DIFFERENCE'\n"
        "cut.parent = base\ncut.hide_set(True); cut.hide_render = True\n")
    assert build["ok"], build
    link = json.loads((out / "census.json").read_text())["links"]["base"]
    assert link["objects"] == ["base"] and link["bbox_max"][2] == pytest.approx(1.0, abs=1e-4)


@needs_blender
def test_a_nan_in_a_link_is_the_scripts_error(tmp_path):
    """D114: a NaN world matrix is never baked into ``meshes/<link>.glb``: the build fails naming the object and channel."""
    build, out = _wrapper_build(tmp_path, "import bpy\n" + BASE + "base.scale.x = float('nan')\n")
    assert not build["ok"] and build["error_type"] == "NonFiniteTransform", build
    assert build["error_message"].startswith("link base: 1 object(s)") and "'base': scale=(nan, 1, 1)" in build["error_message"]
    assert not (out / "meshes" / "base.glb").exists()
