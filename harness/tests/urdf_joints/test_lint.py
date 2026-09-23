"""URDF + model.py lint rules."""

from __future__ import annotations

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.languages.urdf import lint_model_text, lint_urdf_text, lint_workspace
from codeverse3d.workspace import Workspace

GOOD = """<?xml version="1.0"?>
<robot name="cab">
  <link name="body">
    <visual><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/body.glb"/></geometry></visual>
    <collision><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/body.glb"/></geometry></collision>
  </link>
  <link name="door">
    <visual><origin xyz="0.29 0.2 0" rpy="0 0 0"/><geometry><mesh filename="meshes/door.glb"/></geometry></visual>
    <collision><origin xyz="0.29 0.2 0" rpy="0 0 0"/><geometry><mesh filename="meshes/door.glb"/></geometry></collision>
  </link>
  <joint name="hinge" type="revolute">
    <parent link="body"/><child link="door"/>
    <origin xyz="-0.29 -0.2 0" rpy="0 0 0"/><axis xyz="0 0 -1"/>
    <limit lower="0" upper="1.57" effort="10" velocity="1"/>
  </joint>
</robot>
"""
MODEL = 'import bpy\nbpy.ops.mesh.primitive_cube_add()\nbpy.context.active_object.name = "body"\nd = bpy.data.objects.new("door", None)\n'


def _msgs(findings, sev=None):
    return [f.message for f in findings if sev is None or f.severity == sev]


def test_xml_and_structure_errors():
    f, _ = lint_urdf_text("<robot name='x'><link name='a'>")
    assert f[0].severity == Severity.ERROR and "well-formed" in f[0].message and f[0].data.get("line") == 1
    f, _ = lint_urdf_text("<model/>")
    assert "must be <robot>" in f[0].message
    f, links = lint_urdf_text("<robot name='x'></robot>")
    assert "no <link>" in f[0].message and links == []


def test_tree_rules():
    two_roots = GOOD.replace('<joint name="hinge"', '<link name="x"/><joint name="hinge"')
    f, _ = lint_urdf_text(two_roots)
    assert any("exactly one root" in m for m in _msgs(f, Severity.ERROR))
    dup = GOOD.replace("</robot>", '<joint name="j2" type="fixed"><parent link="body"/><child link="door"/></joint></robot>')
    f, _ = lint_urdf_text(dup)
    assert any("child of two joints" in m for m in _msgs(f, Severity.ERROR))
    cyc = GOOD.replace("</robot>", '<link name="c"><visual><geometry><mesh filename="meshes/c.glb"/></geometry></visual></link>'
                       '<link name="d"><visual><geometry><mesh filename="meshes/d.glb"/></geometry></visual></link>'
                       '<joint name="j3" type="fixed"><parent link="c"/><child link="d"/></joint>'
                       '<joint name="j4" type="fixed"><parent link="d"/><child link="c"/></joint></robot>')
    f, _ = lint_urdf_text(cyc)
    assert any("does not reach the root" in m for m in _msgs(f, Severity.ERROR))
    unknown = GOOD.replace('<child link="door"/>', '<child link="dor"/>')
    f, _ = lint_urdf_text(unknown)
    assert any("does not exist" in m for m in _msgs(f, Severity.ERROR))
    same = GOOD.replace('<parent link="body"/>', '<parent link="door"/>')
    f, _ = lint_urdf_text(same)
    assert any("parent == child" in m for m in _msgs(f, Severity.ERROR))


def test_joint_rules():
    f, _ = lint_urdf_text(GOOD.replace('<limit lower="0" upper="1.57" effort="10" velocity="1"/>', ""))
    assert any("need <limit" in m for m in _msgs(f, Severity.ERROR))
    f, _ = lint_urdf_text(GOOD.replace('lower="0" upper="1.57"', 'lower="1" upper="0"'))
    assert any("upper 0.0 < lower 1.0" in m for m in _msgs(f, Severity.ERROR))
    f, _ = lint_urdf_text(GOOD.replace('<axis xyz="0 0 -1"/>', '<axis xyz="0 0 0"/>'))
    assert any("zero axis" in m for m in _msgs(f, Severity.ERROR))
    f, _ = lint_urdf_text(GOOD.replace('<axis xyz="0 0 -1"/>', '<axis xyz="0 0 -2"/>'))
    w = [x for x in f if "not unit" in x.message]
    assert w and w[0].severity == Severity.WARN and 'xyz="0 0 -1"' in w[0].fix_hint
    f, _ = lint_urdf_text(GOOD.replace('type="revolute"', 'type="continuous"'))
    assert any("continuous joints have no lower/upper" in m for m in _msgs(f, Severity.WARN))
    f, _ = lint_urdf_text(GOOD.replace('type="revolute"', 'type="hinge"'))
    assert any("must be one of" in m for m in _msgs(f, Severity.ERROR))
    f, _ = lint_urdf_text(GOOD.replace('<origin xyz="-0.29 -0.2 0" rpy="0 0 0"/>', '<origin xyz="-0.29 -0.2" rpy="0 0 0"/>'))
    assert any("3 finite numbers" in m for m in _msgs(f, Severity.ERROR))
    f, _ = lint_urdf_text(GOOD.replace('<axis xyz="0 0 -1"/>', ""))
    assert any("no <axis>" in m for m in _msgs(f, Severity.WARN))


def test_link_rules():
    f, _ = lint_urdf_text(GOOD.replace('filename="meshes/door.glb"', 'filename="door.glb"'))
    e = [x for x in f if "mesh filename" in x.message]
    assert e and e[0].severity == Severity.ERROR and "meshes/door.glb" in e[0].fix_hint
    f, _ = lint_urdf_text(GOOD.replace('<mesh filename="meshes/door.glb"/>', '<box size="1 1 1"/>'))
    assert any("must be a <mesh>" in m for m in _msgs(f, Severity.ERROR))
    f, _ = lint_urdf_text(GOOD.replace('<collision><origin xyz="0.29 0.2 0" rpy="0 0 0"/><geometry><mesh filename="meshes/door.glb"/></geometry></collision>', ""))
    assert any("no <collision>" in m for m in _msgs(f, Severity.WARN))
    f, _ = lint_urdf_text(GOOD.replace('<collision><origin xyz="0.29 0.2 0"', '<collision><origin xyz="0.3 0.2 0"'))
    assert any("differs from <visual>" in m for m in _msgs(f, Severity.WARN))
    f, _ = lint_urdf_text(GOOD.replace('name="door"', 'name="DoorOpen"').replace('link="door"', 'link="DoorOpen"').replace("meshes/door.glb", "meshes/DoorOpen.glb"))
    msgs = _msgs(f, Severity.WARN)
    assert any("state word" in m for m in msgs)
    # the plan's PascalCase part names are accepted silently (check_contract normalises names) …
    f, _ = lint_urdf_text(GOOD.replace('name="door"', 'name="FrontDoor"').replace('link="door"', 'link="FrontDoor"').replace("meshes/door.glb", "meshes/FrontDoor.glb"))
    assert f == []
    # … but Blender auto-suffixes / spaces are not plain identifiers
    f, _ = lint_urdf_text(GOOD.replace('name="door"', 'name="door.001"').replace('link="door"', 'link="door.001"').replace("meshes/door.glb", "meshes/door.001.glb"))
    assert any("plain identifier" in m for m in _msgs(f, Severity.WARN))
    f, _ = lint_urdf_text(GOOD.replace('<link name="door">', '<link name="door"><visual><geometry><mesh filename="meshes/door.glb"/></geometry></visual>'))
    assert any("2 <visual>" in m for m in _msgs(f, Severity.ERROR))


def test_model_lint():
    f = lint_model_text(MODEL, ["body", "door", "lid"])
    assert [x.severity for x in f] == [Severity.WARN] and "lid" in f[0].message
    f = lint_model_text("import bpy, subprocess\nbpy.ops.wm.save_mainfile()\nbpy.ops.render.render()\nsys.exit()\nbpy.ops.object.camera_add()\n", [])
    msgs = _msgs(f, Severity.ERROR)
    assert any("subprocess" in m for m in msgs) and any("bpy.ops.wm.save_mainfile" in m for m in msgs)
    assert any("render" in m for m in msgs) and any("sys.exit" in m for m in msgs)
    assert any("camera" in m for m in _msgs(f, Severity.WARN))
    f = lint_model_text("x = (1,\n", [])
    assert f[0].severity == Severity.ERROR and "SyntaxError" in f[0].message and f[0].data["line"] == 1
    f = lint_model_text("import math\n", [])
    assert any("never imports bpy" in m for m in _msgs(f, Severity.ERROR))
    f = lint_model_text("import bpy\nfrom codeverse3d.x import y\n", [])
    assert any("harness" in m for m in _msgs(f, Severity.ERROR))


def test_lint_workspace(tmp_path):
    ws = Workspace(tmp_path / "ws").create()
    rep = lint_workspace(ws)
    assert not rep.passed and rep.gate == "lint:urdf" and len(rep.errors) == 2
    (ws.src / "robot.urdf").write_text(GOOD)
    (ws.src / "model.py").write_text(MODEL)
    rep = lint_workspace(ws)
    assert rep.passed and rep.findings == []


def test_reserved_link_name_world():
    # glTF readers use 'world' as the scene-graph base frame: a link of that name cannot become a GLB node
    f, _ = lint_urdf_text(GOOD.replace('name="body"', 'name="world"').replace('link="body"', 'link="world"').replace("meshes/body.glb", "meshes/world.glb"))
    assert any("reserved" in m for m in _msgs(f, Severity.ERROR))
