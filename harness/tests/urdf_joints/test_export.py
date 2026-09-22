"""urdf_to_glb hierarchy/extras and the articulation sheet (built by ``sheet.contact_sheet``)."""

from __future__ import annotations

import json
import struct

import pytest
import trimesh
from PIL import Image

from codeverse3d.contracts.artifacts import RenderSet, RenderView
from codeverse3d.spatial.joints import (
    ARTICULATION_SHEET_NAME,
    UrdfError,
    load_urdf,
    render_poses,
    urdf_to_glb,
)
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
    assert "extras" not in names["body"] or "joint" not in names["body"].get("extras", {})
    scene_extras = js["scenes"][0]["extras"]
    assert [j["name"] for j in scene_extras["joints"]] == ["hinge", "handle_mount"]
    assert scene_extras["links"] == ["body", "door", "handle"]
    # hierarchy: cab → body → door → handle
    idx = {n["name"]: i for i, n in enumerate(js["nodes"])}
    assert idx["door"] in names["body"]["children"] and idx["handle"] in names["door"]["children"]
    # canonical frame: Y-up (ground at y=0), door at +z front
    s = trimesh.load(glb)
    assert abs(s.bounds[0][1]) < 1e-6 and s.bounds[1][1] > 0.79
    assert s.bounds[1][2] > 0.2
    # posed export moves the door
    glb2 = urdf_to_glb(r, tmp_path / "open.glb", {"hinge": 1.57})
    s2 = trimesh.load(glb2)
    assert s2.bounds[1][2] > 0.7  # door swung out to +z (front)
    assert _gltf_json(glb2)["scenes"][0]["extras"]["pose"] == {"hinge": 1.57}


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
    sheet = tmp_path / "ren" / ARTICULATION_SHEET_NAME
    assert sheet.is_file() and Image.open(sheet).size[0] > 32


def test_multi_material_link_keeps_materials(tmp_path):
    from tests.urdf_joints.conftest import write_mesh_robot

    urdf, meshes = write_mesh_robot(tmp_path)
    a = trimesh.creation.box((0.1, 0.1, 0.1))
    a.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(name="wood", baseColorFactor=[200, 150, 100, 255]))
    b = trimesh.creation.box((0.05, 0.05, 0.3))
    b.apply_translation((0, 0, 0.3))
    b.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(name="metal", baseColorFactor=[200, 200, 210, 255]))
    (meshes / "body.glb").write_bytes(trimesh.Scene([a, b]).export(file_type="glb"))
    from codeverse3d.spatial.joints import load_urdf as _load

    r = _load(urdf, meshes)
    assert len(r.links["body"].submeshes) == 2 and len(r.links["body"].mesh.split(only_watertight=False)) == 2
    glb = urdf_to_glb(r, tmp_path / "mm.glb")
    s = trimesh.load(glb)
    names = {k: getattr(getattr(g.visual, "material", None), "name", None) for k, g in s.geometry.items()}
    assert {names["body__0"], names["body__1"]} == {"wood", "metal"}
    assert sorted(n for n in [e[1] for e in s.graph.to_edgelist()] if n.startswith("body")) == ["body", "body__0", "body__1"]


def test_joint_sweep_tool_offline(tmp_path, monkeypatch):
    """The ``joint_sweep`` tool body lives in spatial.tools: collision sweep over every
    joint, renders narrowed to ``joints`` (+ rest) through ``render_poses``."""
    import codeverse3d.spatial.tools as ts
    from codeverse3d.spatial.registry import ToolContext, get_tool
    from codeverse3d.workspace import Workspace

    ws = Workspace(tmp_path / "ws").create()
    ctx = ToolContext(workspace=ws, language="urdf_blender", track="articulated_object")
    obs = get_tool("joint_sweep").call(ctx, {})
    assert not obs.ok and "run `build`" in obs.text
    write_mesh_robot(ws.artifacts)
    rendered: list[list[str] | None] = []

    def fake_render_poses(robot, out_dir, poses=None, **kw):
        rendered.append(None if poses is None else [label for label, _ in poses])
        Image.new("RGB", (8, 8), "gray").save(out_dir / ARTICULATION_SHEET_NAME)
        return []

    monkeypatch.setattr(ts, "render_poses", fake_render_poses)
    obs = get_tool("joint_sweep").call(ctx, {"joints": ["hinge"], "n_samples": 5})
    assert obs.ok and obs.numbers["max_penetration_m"] == 0.0
    assert obs.text.startswith("JOINT SWEEP: PASS")
    assert rendered == [["rest", "hinge@upper"]] and obs.images[0].endswith(ARTICULATION_SHEET_NAME)  # lower=0 dedupes into rest
    obs = get_tool("joint_sweep").call(ctx, {})
    assert rendered[-1] is None          # empty joints = the full sheet (render_poses default)


def test_joint_sweep_penetration_is_a_verdict_not_an_mcp_error(tmp_path, monkeypatch):
    """A sweep that finds a penetration RAN: ``failed`` stays False (63% of 1404 recorded
    joint_sweep calls answered FAIL, and each one reported as an MCP error bought a retry
    at ~117k prompt tokens), and the FAIL verdict leads the text."""
    import codeverse3d.spatial.tools as ts
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
    assert obs.text.startswith("JOINT SWEEP: FAIL — penetration ") and "tolerance" in obs.text
    assert "pose sweep:" in obs.text                     # the detail the agent acts on is still there
    assert obs.numbers["max_penetration_m"] > 0.1


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


def test_link_named_world_is_rejected(tmp_path):
    urdf, meshes = write_mesh_robot(tmp_path)
    (meshes / "world.glb").write_bytes((meshes / "body.glb").read_bytes())
    urdf.write_text(urdf.read_text().replace('name="body"', 'name="world"').replace('link="body"', 'link="world"').replace("meshes/body.glb", "meshes/world.glb"))
    with pytest.raises(UrdfError, match="reserved"):
        load_urdf(urdf, meshes)
