from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh

from codeverse3d.spatial.measure import measure_glb


def _root_pivot_scene() -> trimesh.Scene:
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
