"""urdf_to_glb hierarchy/extras and the articulation sheet composer."""

from __future__ import annotations

import json
import struct

import trimesh
from PIL import Image

from codeverse.contracts.artifacts import RenderSet, RenderView
from codeverse.spatial.joints import ARTICULATION_SHEET_NAME, load_urdf, render_poses, urdf_to_glb
from codeverse.spatial.joints_export import make_sheet
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


def test_make_sheet_and_render_poses_with_fake_renderer(tmp_path):
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

    out = render_poses(r, tmp_path / "ren", renderer=fake_renderer)
    assert [label for label, _ in out] == ["rest", "hinge@upper"]
    assert len(calls) == 2 and all(c.exists() for c in calls)
    sheet = tmp_path / "ren" / ARTICULATION_SHEET_NAME
    assert sheet.is_file() and Image.open(sheet).size[0] > 32


def test_make_sheet_labels(tmp_path):
    imgs = []
    for i in range(3):
        p = tmp_path / f"{i}.png"
        Image.new("RGB", (64, 48), (i * 50, 0, 0)).save(p)
        imgs.append((f"img{i}", p))
    out = make_sheet(imgs, tmp_path / "s.png", cols=2, tile=64)
    im = Image.open(out)
    assert im.size == (128, 2 * (64 + 22))


def test_multi_material_link_keeps_materials(tmp_path):
    from tests.urdf_joints.conftest import write_mesh_robot

    urdf, meshes = write_mesh_robot(tmp_path)
    a = trimesh.creation.box((0.1, 0.1, 0.1))
    a.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(name="wood", baseColorFactor=[200, 150, 100, 255]))
    b = trimesh.creation.box((0.05, 0.05, 0.3))
    b.apply_translation((0, 0, 0.3))
    b.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(name="metal", baseColorFactor=[200, 200, 210, 255]))
    (meshes / "body.glb").write_bytes(trimesh.Scene([a, b]).export(file_type="glb"))
    from codeverse.spatial.joints import load_urdf as _load

    r = _load(urdf, meshes)
    assert len(r.links["body"].submeshes) == 2 and len(r.links["body"].mesh.split(only_watertight=False)) == 2
    glb = urdf_to_glb(r, tmp_path / "mm.glb")
    s = trimesh.load(glb)
    names = {k: getattr(getattr(g.visual, "material", None), "name", None) for k, g in s.geometry.items()}
    assert {names["body__0"], names["body__1"]} == {"wood", "metal"}
    assert sorted(n for n in [e[1] for e in s.graph.to_edgelist()] if n.startswith("body")) == ["body", "body__0", "body__1"]


def test_joint_sweep_observation_offline(tmp_path):
    from codeverse.spatial.joints import joint_sweep_observation
    from codeverse.workspace import Workspace
    from tests.urdf_joints.conftest import write_mesh_robot

    ws = Workspace(tmp_path / "ws").create()
    obs = joint_sweep_observation(ws, render=False)
    assert not obs.ok and "run `build`" in obs.text
    write_mesh_robot(ws.artifacts)
    obs = joint_sweep_observation(ws, render=False, joint="hinge", expected_direction="front")
    assert obs.ok and obs.numbers["max_penetration_m"] == 0.0 and obs.numbers["motion_check"]["ok"]
