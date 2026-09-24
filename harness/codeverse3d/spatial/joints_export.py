"""Export a posed :class:`Robot` to the canonical GLB (node hierarchy mirroring
links, joint extras) and render articulation sheets.

GLB convention: Y-up, +Z front (``codeverse3d.conventions.GLB_FRAME``).  The
URDF world (Z-up, -Y front) is rotated by ``Rx(-90°)`` at the ``<robot name>``
node; every link is a child node (name = link name) whose local matrix is the
joint transform ``T_origin · Motion(q)``; geometry lives in the link frame.
Non-root link nodes carry ``extras.joint`` and the scene carries
``extras.joints / links / pose`` (same keys as the owner's dataset builder).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from codeverse3d.contracts.artifacts import RenderSet
from codeverse3d.conventions import ARTICULATION_VIEWS, ViewPreset
from codeverse3d.spatial._render_common import out_directory
from codeverse3d.spatial.joints_model import Joint, Robot, fk, resolve_q
from codeverse3d.spatial.joints_poses import limit_poses
from codeverse3d.spatial.render import render_glb
from codeverse3d.spatial.sheet import contact_sheet

ARTICULATION_SHEET_NAME = "articulation_sheet.png"
#: URDF (Z-up, -Y front) → glTF (Y-up, +Z front):  (x, y, z) → (x, z, -y)
ZUP_TO_YUP = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=float)
#: scene-graph base frame of the exported GLB (not a glTF node; must not collide with a link name)
SCENE_BASE_FRAME = "__scene__"


def joint_extras(j: Joint) -> dict[str, Any]:
    return {
        "name": j.name, "type": j.type, "axis": [float(v) for v in j.axis],
        "lower": j.lower if j.lower is not None else 0.0, "upper": j.upper if j.upper is not None else 0.0,
        "effort": j.effort, "velocity": j.velocity, "parent": j.parent, "child": j.child,
        "origin_xyz": [float(v) for v in j.origin[:3, 3]],
    }


def robot_node_name(robot: Robot) -> str:
    """Name of the ``<robot>`` root node (carries the Z-up → Y-up rotation).  Scene-graph
    node names must be unique, so a robot named like one of its links (``<robot
    name="body">`` with a link ``body``) gets a ``__root`` suffix instead of silently
    aliasing the link node — which would drop the rotation and the link's placement."""
    name = robot.name or "robot"
    while name in robot.links or name == SCENE_BASE_FRAME:
        name += "__root"
    return name


def robot_scene(robot: Robot, pose: dict[str, float] | None = None) -> trimesh.Scene:
    """Build the trimesh.Scene (hierarchical, Y-up) for ``robot`` at ``pose``."""
    fk(robot, pose or {})  # validates joint names
    # the RESOLVED pose: a <mimic> follower moves with its driver in the export too, not
    # only in fk.  The sheet the judge sees and the sweep's collision check are the same
    # mechanism only if both pose it the same way (review, 2026-09-04: they did not).
    pose = resolve_q(robot, pose or {})
    scene = trimesh.Scene(base_frame=SCENE_BASE_FRAME)  # never a link name (a link 'world' would close a cycle)
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
        else:  # several materials: link node + one geometry child per piece (names stay unique)
            scene.graph.update(frame_to=name, frame_from=parent_node, matrix=local, metadata=extras)
            for i, piece in enumerate(pieces):
                scene.add_geometry(piece.copy(), node_name=f"{name}__{i}", geom_name=f"{name}__{i}", parent_node_name=name,
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
    """Export one GLB per pose, render ``views`` for each (default: ``ARTICULATION_VIEWS``)
    and write ``out_dir/articulation_sheet.png`` (rest / each joint at lower & upper
    by default — the image the judge sees).  Returns ``[(pose_name, RenderSet)]``."""
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
