from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh

from codeverse3d.spatial.measure import (
    GlbLoadError,
    instance_groups,
    measure_glb,
)


def test_instance_groups() -> None:
    g = instance_groups(["Leg_3", "Leg_0", "Seat", "Bolt.1", "Bolt.2", "Rail_1"])
    assert g["Leg"] == ["Leg_0", "Leg_3"]
    assert g["Bolt"] == ["Bolt.1", "Bolt.2"]
    assert g["Seat"] == ["Seat"]
    assert g["Rail_1"] == ["Rail_1"]  # singleton keeps its full name
    # a bare base beside indexed siblings joins their group (it once crashed the judge input)
    assert instance_groups(["lens", "lens_2", "lens_1"])["lens"] == ["lens", "lens_1", "lens_2"]


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(GlbLoadError):
        measure_glb(tmp_path / "nope.glb")


def test_degenerate_mesh_is_a_finding(tmp_path: Path) -> None:
    sc = trimesh.Scene()
    flat = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [1, 0, 1]], faces=[[0, 1, 2]])  # zero height, open
    sc.add_geometry(flat, node_name="Sheet", geom_name="Sheet")
    p = tmp_path / "flat.glb"
    sc.export(str(p))
    m = measure_glb(p)
    assert m.tri_count == 1
    assert m.parts[0].watertight is False and m.parts[0].volume_m3 is None
    assert m.extents[1] == 0.0


def test_cyclic_scene_graph_does_not_hang(tmp_path: Path) -> None:
    """A GLB node named like the loader's base frame ('world') closes a cycle in the
    scene graph; measuring must terminate (with the geometry still counted once)."""
    from codeverse3d.spatial.measure import _subtree_nodes, part_meshes

    box = trimesh.creation.box((0.2, 0.2, 0.2))
    scene = trimesh.Scene(base_frame="root")
    scene.graph.update(frame_to="wrapper", frame_from="root")
    scene.add_geometry(box, node_name="world", geom_name="world", parent_node_name="wrapper")
    scene.graph.update(frame_to="root", frame_from="world")  # cycle: root → wrapper → world → root
    assert "world" in _subtree_nodes(scene, "root") and len(_subtree_nodes(scene, "root")) == 3
    assert [k for k, v in part_meshes(scene).items() if v is not None]


def test_cached_parts_identity_and_invalidation(stool_glb: Path) -> None:
    from codeverse3d.spatial.measure import cached_parts

    p1 = cached_parts(stool_glb)
    p2 = cached_parts(stool_glb)
    assert p1 is not p2                               # fresh dict per call
    assert all(p1[k] is p2[k] for k in p1)            # same stat → shared meshes
    # rewriting the file (new mtime_ns/size) invalidates the entry
    import os
    os.utime(stool_glb, ns=(1, 1))
    p3 = cached_parts(stool_glb)
    assert set(p3) == set(p1) and all(p3[k] is not p1[k] for k in p3)


def test_measure_glb_memoized_and_isolated(stool_glb: Path) -> None:
    m1 = measure_glb(stool_glb)
    m2 = measure_glb(stool_glb)
    assert m1 is not m2 and m1 == m2
    m1.extra["findings"].append("mutated")            # a caller's edit never leaks back
    assert measure_glb(stool_glb).extra["findings"] == []


def _root_pivot_scene(dup_names: bool = False) -> trimesh.Scene:
    """Two 0.1 x 0.1 x 0.4 posts authored Z-up under a root 'pivot' carrying the Z-up→Y-up
    rotation — what THREE.GLTFExporter writes for a Z-up scene."""
    rot = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], float)
    sc = trimesh.Scene()
    sc.graph.update(frame_from=sc.graph.base_frame, frame_to="pivot", matrix=rot)
    for i, x in enumerate((0.0, 0.3)):
        box = trimesh.creation.box(extents=(0.1, 0.1, 0.4))
        place = np.eye(4)
        place[:3, 3] = (x, 0, 0.2)   # on the NODE, so the walk has an edge to compose
        sc.add_geometry(box, node_name=f"Post_{i}", geom_name=f"G{i}", parent_node_name="pivot", transform=place)
    return sc


def test_a_root_pivot_glb_measures_upright(tmp_path: Path) -> None:
    """A scene-root rotation reaches every descendant's world frame (an upright lamp once measured lying down)."""
    p = tmp_path / "pivot.glb"
    p.write_bytes(_root_pivot_scene().export(file_type="glb"))
    m = measure_glb(p)
    assert m.bbox_min[1] == pytest.approx(0.0, abs=1e-6) and m.bbox_max[1] == pytest.approx(0.4, abs=1e-6)
    assert m.ground_gap_m == pytest.approx(0.0, abs=1e-6)
    assert not [f for f in m.extra.get("findings", []) if "node" in f]


def test_duplicate_or_unnamed_glTF_nodes_are_named_in_the_findings(tmp_path: Path) -> None:
    """Duplicate or unnamed nodes mis-pose under trimesh: the reader is told the numbers are approximate."""
    import json
    import struct

    from codeverse3d.spatial.measure import gltf_node_names, node_name_findings

    raw = _root_pivot_scene().export(file_type="glb")
    (n,) = struct.unpack("<I", raw[12:16])
    js = json.loads(raw[20:20 + n])
    for node in js["nodes"]:
        if node.get("name", "").startswith("Post_"):
            node["name"] = "Post"          # a duplicate
    js["nodes"][0].pop("name", None)      # and an unnamed one
    body = json.dumps(js, separators=(",", ":")).encode()
    body += b" " * ((4 - len(body) % 4) % 4)
    glb = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(body) + len(raw[20 + n:])) + struct.pack("<I", len(body)) + b"JSON" + body + raw[20 + n:]
    p = tmp_path / "dups.glb"
    p.write_bytes(glb)

    assert gltf_node_names(p).count("Post") == 2
    found = node_name_findings(p)
    assert any("duplicate glTF node names ('Post' x2)" in f for f in found)
    assert any(f"1 of {len(gltf_node_names(p))} glTF nodes are unnamed" in f for f in found)
    assert found == [f for f in measure_glb(p).extra["findings"] if "glTF node" in f]
    clean = tmp_path / "clean.glb"
    clean.write_bytes(raw)
    assert node_name_findings(clean) == []
