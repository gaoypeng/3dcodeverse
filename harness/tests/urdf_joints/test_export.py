"""urdf_to_glb hierarchy/extras and the articulation sheet (built by ``sheet.contact_sheet``)."""

from __future__ import annotations

import json
import struct

import pytest
import trimesh
from PIL import Image

from codeverse3d.contracts.artifacts import RenderSet, RenderView
from codeverse3d.spatial.joints_export import ARTICULATION_SHEET_NAME, render_poses, urdf_to_glb
from codeverse3d.spatial.joints_model import UrdfError, load_urdf
from tests.urdf_joints.conftest import write_mesh_robot


def _gltf_json(path):
    data = path.read_bytes()
    ln = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20:20 + ln])


def test_urdf_to_glb_hierarchy_and_extras(tmp_path):
    urdf, meshes = write_mesh_robot(tmp_path, handle=True)
    r = load_urdf(urdf, meshes)
    glb = urdf_to_glb(r, tmp_path / "object.glb")
    js = _gltf_json(glb)
    names = {n["name"]: n for n in js["nodes"]}
    assert {"cab", "body", "door", "handle"} <= set(names)
    assert "joint" in names["door"]["extras"] and names["door"]["extras"]["joint"]["type"] == "revolute"
    assert js["scenes"][0]["extras"]["links"] == ["body", "door", "handle"]
    idx = {n["name"]: i for i, n in enumerate(js["nodes"])}
    assert idx["door"] in names["body"]["children"] and idx["handle"] in names["door"]["children"]
    # posed export moves the door
    glb2 = urdf_to_glb(r, tmp_path / "open.glb", {"hinge": 1.57})
    s2 = trimesh.load(glb2)
    assert s2.bounds[1][2] > 0.7  # door swung out to +z (front)
    assert _gltf_json(glb2)["scenes"][0]["extras"]["pose"] == {"hinge": 1.57}


def test_joint_sweep_penetration_is_a_verdict_not_an_mcp_error(tmp_path, monkeypatch):
    """A sweep that finds a penetration RAN: ``failed`` stays False (each MCP error bought a retry at ~117k prompt tokens)
    and the FAIL verdict leads the text; the tool and ``sweep_gate`` say the same thing."""
    import codeverse3d.spatial.tools as ts
    from codeverse3d.spatial.joints_sweep import sweep_gate
    from codeverse3d.spatial.registry import ToolContext, get_tool
    from codeverse3d.workspace import Workspace
    from tests.urdf_joints.conftest import write_prims_robot

    ws = Workspace(tmp_path / "ws").create()
    write_prims_robot(ws.artifacts / "robot.urdf", axis_z=+1)   # the door swings into the body
    monkeypatch.setattr(ts, "render_poses", lambda robot, out_dir, poses=None, **kw: [
        Image.new("RGB", (8, 8), "gray").save(out_dir / ARTICULATION_SHEET_NAME)])
    ctx = ToolContext(workspace=ws, language="urdf_blender", track="articulated_object")
    obs = get_tool("joint_sweep").call(ctx, {})
    assert not obs.ok and not obs.failed
    assert obs.text.startswith("joint_sweep: FAIL — 1 error(s)")
    assert "links 'body|door' overlap in 2 of the sampled poses" in obs.text and "fix:" in obs.text
    gate, _ = sweep_gate(ws)
    assert not gate.passed and [f.message for f in gate.errors][0] in obs.text


def test_render_poses_with_fake_renderer_builds_sheet(tmp_path, monkeypatch):
    import codeverse3d.spatial.joints_export as je

    urdf, meshes = write_mesh_robot(tmp_path)
    r = load_urdf(urdf, meshes)
    calls = []

    def fake_renderer(glb, out_dir, *, views, width, height, sheet):
        out_dir.mkdir(parents=True, exist_ok=True)
        calls.append(glb)
        rs = RenderSet(renderer="fake")
        for v in views:
            p = out_dir / f"view_{v.name}.png"
            Image.new("RGB", (32, 32), "gray").save(p)
            rs.views.append(RenderView(name=v.name, path=str(p)))
        return rs

    monkeypatch.setattr(je, "render_glb", fake_renderer)
    out = render_poses(r, tmp_path / "ren")
    assert [label for label, _ in out] == ["rest", "hinge@upper"]
    assert len(calls) == 2 and all(c.exists() for c in calls)
    # the judge's sheet shows these views by name, whatever order the quick set lists
    assert [v.name for v in out[0][1].views] == ["front_right_high", "back_left_high", "front"]
    sheet = tmp_path / "ren" / ARTICULATION_SHEET_NAME
    assert sheet.is_file() and Image.open(sheet).size[0] > 32


def test_robot_named_like_a_link_keeps_frame_and_placement(tmp_path):
    """``<robot name="body">`` with a root link ``body``: scene-graph node names must be
    unique, so the root node gets a ``__root`` suffix instead of aliasing the link node
    (which silently dropped the Z-up→Y-up rotation and the door's joint placement)."""
    from codeverse3d.spatial.measure import measure_glb

    urdf, meshes = write_mesh_robot(tmp_path)
    urdf.write_text(urdf.read_text().replace('<robot name="cab">', '<robot name="body">'))
    r = load_urdf(urdf, meshes)
    glb = urdf_to_glb(r, tmp_path / "object.glb")
    js = _gltf_json(glb)
    names = [n["name"] for n in js["nodes"]]
    assert names.count("body") == 1 and "body__root" in names
    m = measure_glb(glb)
    rows = {p.name: p for p in m.parts}
    assert set(rows) == {"body", "door"} and m.ground_gap_m == pytest.approx(0.0, abs=1e-6)
    assert rows["body"].bbox_max[1] == pytest.approx(0.8, abs=1e-6)  # Y-up: height along y
    assert rows["door"].bbox_min[0] == pytest.approx(-0.29, abs=1e-6) and rows["door"].bbox_max[0] == pytest.approx(0.29, abs=1e-6)
    assert rows["door"].bbox_min[2] > 0.19  # door in front (+z) of the body, hinge placement applied
    # a link named 'world' cannot become a GLB node at all: refused at load
    (meshes / "world.glb").write_bytes((meshes / "body.glb").read_bytes())
    urdf.write_text(urdf.read_text().replace('<robot name="body">', '<robot name="cab">').replace('name="body"', 'name="world"').replace('link="body"', 'link="world"').replace("meshes/body.glb", "meshes/world.glb"))
    with pytest.raises(UrdfError, match="reserved"):
        load_urdf(urdf, meshes)
