"""Export a posed :class:`Robot` to the canonical GLB (node hierarchy mirroring links, joint extras)
and render articulation sheets."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from codeverse3d.contracts.artifacts import RenderSet
from codeverse3d.conventions import ARTICULATION_VIEWS, LINK_PIECE_SEP, ViewPreset
from codeverse3d.spatial._render_common import out_directory
from codeverse3d.spatial.joints_model import Joint, Robot, fk, resolve_q
from codeverse3d.spatial.joints_poses import limit_poses
from codeverse3d.spatial.render import render_glb
from codeverse3d.spatial.sheet import contact_sheet

ARTICULATION_SHEET_NAME = "articulation_sheet.png"
ZUP_TO_YUP = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=float)
SCENE_BASE_FRAME = "__scene__"


def joint_extras(j: Joint) -> dict[str, Any]:
    return {
        "name": j.name, "type": j.type, "axis": [float(v) for v in j.axis],
        "lower": j.lower if j.lower is not None else 0.0, "upper": j.upper if j.upper is not None else 0.0,
        "effort": j.effort, "velocity": j.velocity, "parent": j.parent, "child": j.child,
        "origin_xyz": [float(v) for v in j.origin[:3, 3]],
    }


def robot_node_name(robot: Robot) -> str:
    """Name of the ``<robot>`` root node (carries the Z-up → Y-up rotation)."""
    name = robot.name or "robot"
    while name in robot.links or name == SCENE_BASE_FRAME:
        name += "__root"
    return name


def robot_scene(robot: Robot, pose: dict[str, float] | None = None) -> trimesh.Scene:
    """Build the trimesh.Scene (hierarchical, Y-up) for ``robot`` at ``pose``."""
    fk(robot, pose or {})  # validates joint names
    pose = resolve_q(robot, pose or {})
    scene = trimesh.Scene(base_frame=SCENE_BASE_FRAME)
    robot_node = robot_node_name(robot)
    meta = {"links": robot.link_order(), "frame": "y_up_pos_z_front", "pose": dict(pose), "units": "meters"}
    meta["joints"] = [joint_extras(j) for j in robot.joints.values()]
    scene.graph.update(frame_to=robot_node, frame_from=scene.graph.base_frame, matrix=ZUP_TO_YUP, metadata=meta)
    for name in robot.link_order():
        link = robot.links[name]
        j = robot.parent_joint(name)
        if j is None:
            parent_node, local = robot_node, np.eye(4)
        else:
            parent_node, local = j.parent, j.origin @ j.motion(float(pose.get(j.name, 0.0)))
        extras = {"joint": joint_extras(j)} if j is not None else None
        pieces = link.submeshes or ([link.mesh] if link.mesh is not None else [])
        if len(pieces) == 1:
            scene.add_geometry(pieces[0].copy(), node_name=name, geom_name=name, parent_node_name=parent_node,
                               transform=local, metadata=extras)
        else:
            scene.graph.update(frame_to=name, frame_from=parent_node, matrix=local, metadata=extras)
            for i, piece in enumerate(pieces):
                scene.add_geometry(piece.copy(), node_name=f"{name}{LINK_PIECE_SEP}{i}", geom_name=f"{name}{LINK_PIECE_SEP}{i}", parent_node_name=name,
                                   transform=np.eye(4))
    scene.metadata.update(meta)
    return scene


def urdf_to_glb(robot: Robot, out_glb: Path | str, pose: dict[str, float] | None = None) -> Path:
    """Write the canonical hierarchical GLB for ``robot`` at ``pose`` (default rest)."""
    out = Path(out_glb)
    out.parent.mkdir(parents=True, exist_ok=True)
    scene = robot_scene(robot, pose)
    out.write_bytes(scene.export(file_type="glb"))
    return out


# ------------------------------------------------------------------ rendering
def render_poses(
    robot: Robot,
    out_dir: Path | str,
    poses: list[tuple[str, dict[str, float]]] | None = None,
    *,
    views: Sequence[ViewPreset] | None = None,
    width: int = 512,
    height: int = 512,
) -> list[tuple[str, RenderSet]]:
    """Export one GLB per pose and render ``views`` of each."""
    out_dir = out_directory(out_dir)
    poses = poses if poses is not None else limit_poses(robot)
    views = tuple(views or ARTICULATION_VIEWS)
    results: list[tuple[str, RenderSet]] = []
    tiles: list[tuple[str, Path]] = []
    for label, q in poses:
        safe = "".join(c if c.isalnum() or c in "-_@." else "_" for c in label)
        glb = urdf_to_glb(robot, out_dir / f"pose_{safe}.glb", q)
        rs = render_glb(glb, out_dir / f"pose_{safe}", views=views, width=width, height=height, sheet=False)
        results.append((label, rs))
        for v in rs.views:
            tiles.append((f"{label} · {v.name}", Path(v.path)))
    contact_sheet(tiles, out_dir / ARTICULATION_SHEET_NAME, cols=len(views))
    return results
