"""Fixtures for the spatial tools tests: synthetic GLBs built with trimesh."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from codeverse.spatial.registry import ToolContext
from codeverse.workspace import Workspace

LEG_XZ = [(-0.15, -0.15), (0.15, -0.15), (-0.15, 0.15), (0.15, 0.15)]
FLOAT_GAP_M = 0.005


def build_stool(path: Path, *, floating_leg: int | None = 3, gap_m: float = FLOAT_GAP_M) -> Path:
    """Seat box (0.4×0.04×0.4 at y 0.41..0.45) + 4 leg cylinders (r 0.02, h 0.41).

    ``floating_leg`` is shortened by ``gap_m`` at both ends and shifted up by
    ``gap_m``: it neither touches the ground nor the seat (gap to seat = gap_m).
    """
    sc = trimesh.Scene()
    seat = trimesh.creation.box(extents=(0.4, 0.04, 0.4))
    seat.apply_translation((0, 0.43, 0))
    sc.add_geometry(seat, node_name="Seat", geom_name="Seat")
    for i, (x, z) in enumerate(LEG_XZ):
        h = 0.41 - 2 * gap_m if i == floating_leg else 0.41
        leg = trimesh.creation.cylinder(radius=0.02, height=h, sections=24)
        leg.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, (1, 0, 0)))
        leg.apply_translation((x, h / 2 + (gap_m if i == floating_leg else 0.0), z))
        sc.add_geometry(leg, node_name=f"Leg_{i}", geom_name=f"Leg_{i}")
    sc.export(str(path))
    return path


@pytest.fixture
def stool_glb(tmp_path: Path) -> Path:
    return build_stool(tmp_path / "stool.glb")


@pytest.fixture
def solid_stool_glb(tmp_path: Path) -> Path:
    return build_stool(tmp_path / "stool_ok.glb", floating_leg=None)


@pytest.fixture
def stool_ctx(tmp_ws: Workspace, stool_glb: Path) -> ToolContext:
    """Workspace with the floating-leg stool as artifacts/object.glb and a blender spec."""
    (tmp_ws.artifacts / "object.glb").write_bytes(stool_glb.read_bytes())
    tmp_ws.spec_path.write_text(json.dumps({"id": "t", "track": "static_object", "language": "blender", "prompt": "a stool"}))
    return ToolContext(workspace=tmp_ws, language="blender", track="static_object")
