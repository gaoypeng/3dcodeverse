from __future__ import annotations

from pathlib import Path

import pytest
import trimesh

from codeverse.spatial.measure import (
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
    from codeverse.spatial.measure import _subtree_nodes, part_meshes

    box = trimesh.creation.box((0.2, 0.2, 0.2))
    scene = trimesh.Scene(base_frame="root")
    scene.graph.update(frame_to="wrapper", frame_from="root")
    scene.add_geometry(box, node_name="world", geom_name="world", parent_node_name="wrapper")
    scene.graph.update(frame_to="root", frame_from="world")  # cycle: root → wrapper → world → root
    assert "world" in _subtree_nodes(scene, "root") and len(_subtree_nodes(scene, "root")) == 3
    assert [k for k, v in part_meshes(scene).items() if v is not None]
