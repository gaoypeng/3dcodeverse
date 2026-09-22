from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh

from codeverse3d.spatial.measure import (
    GlbLoadError,
    instance_groups,
    measure_glb,
    measure_summary_table,
)


def test_measure_stool(stool_glb: Path) -> None:
    m = measure_glb(stool_glb)
    assert {p.name for p in m.parts} == {"Seat", "Leg_0", "Leg_1", "Leg_2", "Leg_3"}
    assert m.extents == pytest.approx((0.4, 0.45, 0.4), abs=1e-6)
    assert m.ground_gap_m == pytest.approx(0.0, abs=1e-6)
    assert m.footprint_offset_m == pytest.approx(0.0, abs=1e-6)
    assert m.n_islands == 5 and m.n_meshes == 5
    seat = next(p for p in m.parts if p.name == "Seat")
    assert seat.tri_count == 12 and seat.watertight and seat.volume_m3 == pytest.approx(0.4 * 0.04 * 0.4, rel=1e-6)
    assert m.frame == "y_up_pos_z_front"
    assert m.extra["findings"] == []


def test_summary_table_groups_instances(stool_glb: Path) -> None:
    table = measure_summary_table(measure_glb(stool_glb))
    assert "Leg ×4 (Leg_0..3)" in table
    assert "| Seat |" in table
    assert "40.0×45.0×40.0" in table
    assert table.count("\n") < 40


def test_instance_groups() -> None:
    g = instance_groups(["Leg_3", "Leg_0", "Seat", "Bolt.1", "Bolt.2", "Rail_1"])
    assert g["Leg"] == ["Leg_0", "Leg_3"]
    assert g["Bolt"] == ["Bolt.1", "Bolt.2"]
    assert g["Seat"] == ["Seat"]
    assert g["Rail_1"] == ["Rail_1"]  # singleton keeps its full name


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


def test_unnamed_single_mesh(tmp_path: Path) -> None:
    p = tmp_path / "box.glb"
    trimesh.creation.box(extents=(1, 1, 1)).export(str(p))
    m = measure_glb(p)
    assert len(m.parts) == 1 and m.tri_count == 12


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


# --------------------------------------------------------------------- parse memo (F24)
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


def test_load_scene_is_never_cached(stool_glb: Path) -> None:
    """Texturing mutates scenes in place — load_scene must hand out fresh objects."""
    from codeverse3d.spatial.measure import load_scene

    s1 = load_scene(stool_glb)
    s2 = load_scene(stool_glb)
    assert s1 is not s2
    g1 = s1.geometry[next(iter(s1.geometry))]
    assert g1 is not s2.geometry[next(iter(s2.geometry))]


def test_instance_groups_bare_base_beside_indexed_siblings() -> None:
    """`lens` next to `lens_1` lands in the same group (the bare name is the regex
    non-match) — this crashed the judge input assembly with AttributeError on the
    None match until 2026-08-28 (scope_tj_ss r1/r2 lost their judgments to it)."""
    g = instance_groups(["lens", "lens_2", "lens_1", "tripod"])
    assert g["lens"] == ["lens", "lens_1", "lens_2"]
    assert g["tripod"] == ["tripod"]


# ------------------------------------------------- 2026-08-30: world transforms and node names
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


def test_world_transform_composes_every_edge_up_to_the_root() -> None:
    """``scene.graph.get`` left a scene-root matrix out of its descendants' world frames on
    the brilliana desk lamp (an upright lamp measured lying down, 2026-08-30); the edge
    walk is what the render rig agrees with.  Here: the rotation on world→pivot must reach
    a grandchild, composed in the right order."""
    from codeverse3d.spatial.measure import world_transform

    sc = _root_pivot_scene()
    t = world_transform(sc, "Post_1")
    # Post_1 sits at x=0.3, z=0.2 in the Z-up frame → world x=0.3, y=0.2
    assert np.allclose(t[:3, 3], (0.3, 0.2, 0.0), atol=1e-9)
    assert np.allclose(t[:3, :3], [[1, 0, 0], [0, 0, 1], [0, -1, 0]], atol=1e-9)
    assert np.allclose(world_transform(sc, sc.graph.base_frame), np.eye(4))


def test_a_root_pivot_glb_measures_upright(tmp_path: Path) -> None:
    p = tmp_path / "pivot.glb"
    p.write_bytes(_root_pivot_scene().export(file_type="glb"))
    m = measure_glb(p)
    assert m.bbox_min[1] == pytest.approx(0.0, abs=1e-6) and m.bbox_max[1] == pytest.approx(0.4, abs=1e-6)
    assert m.ground_gap_m == pytest.approx(0.0, abs=1e-6)
    assert not [f for f in m.extra.get("findings", []) if "node" in f]


def test_duplicate_or_unnamed_glTF_nodes_are_named_in_the_findings(tmp_path: Path) -> None:
    """The harness's own exporters name every node uniquely; a foreign THREE export with
    'Arm' x2 and 21 unnamed nodes mis-posed under trimesh — the reader must be told the
    numbers are approximate rather than trust them (desk-lamp-q2, 2026-08-30)."""
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
