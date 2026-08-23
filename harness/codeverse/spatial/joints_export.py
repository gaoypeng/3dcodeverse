"""Export a posed :class:`Robot` to the canonical GLB (node hierarchy mirroring
links, joint extras) and render articulation sheets.

GLB convention: Y-up, +Z front (``codeverse.conventions.GLB_FRAME``).  The
URDF world (Z-up, -Y front) is rotated by ``Rx(-90°)`` at the ``<robot name>``
node; every link is a child node (name = link name) whose local matrix is the
joint transform ``T_origin · Motion(q)``; geometry lives in the link frame.
Non-root link nodes carry ``extras.joint`` and the scene carries
``extras.joints / links / pose`` (same keys as the owner's dataset builder).
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from codeverse.contracts.artifacts import RenderSet, RenderView
from codeverse.conventions import OBJECT_VIEWS_QUICK, ViewPreset
from codeverse.spatial.joints_model import Joint, Robot, UrdfError, fk
from codeverse.spatial.joints_poses import limit_poses

ARTICULATION_SHEET_NAME = "articulation_sheet.png"
#: URDF (Z-up, -Y front) → glTF (Y-up, +Z front):  (x, y, z) → (x, z, -y)
ZUP_TO_YUP = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=float)
YUP_TO_ZUP = ZUP_TO_YUP.T.copy()
#: scene-graph base frame of the exported GLB (not a glTF node; must not collide with a link name)
SCENE_BASE_FRAME = "__scene__"

Renderer = Callable[..., RenderSet]


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


def robot_scene(robot: Robot, pose: dict[str, float] | None = None, *, joint_extras_on: bool = True) -> trimesh.Scene:
    """Build the trimesh.Scene (hierarchical, Y-up) for ``robot`` at ``pose``."""
    pose = pose or {}
    T = fk(robot, pose)  # validates joint names
    scene = trimesh.Scene(base_frame=SCENE_BASE_FRAME)  # never a link name (a link 'world' would close a cycle)
    robot_node = robot_node_name(robot)
    meta = {"links": robot.link_order(), "frame": "y_up_pos_z_front", "pose": dict(pose), "units": "meters"}
    if joint_extras_on:
        meta["joints"] = [joint_extras(j) for j in robot.joints.values()]
    scene.graph.update(frame_to=robot_node, frame_from=scene.graph.base_frame, matrix=ZUP_TO_YUP,
                       metadata=meta if joint_extras_on else None)
    for name in robot.link_order():
        link = robot.links[name]
        j = robot.parent_joint(name)
        if j is None:
            parent_node, local = robot_node, np.eye(4)
        else:
            parent_node, local = j.parent, j.origin @ j.motion(float(pose.get(j.name, 0.0)))
        extras = {"joint": joint_extras(j)} if (j is not None and joint_extras_on) else None
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
    del T
    return scene


def urdf_to_glb(robot: Robot, out_glb: Path | str, pose: dict[str, float] | None = None, *, joint_extras: bool = True) -> Path:
    """Write the canonical hierarchical GLB for ``robot`` at ``pose`` (default rest)."""
    out = Path(out_glb)
    out.parent.mkdir(parents=True, exist_ok=True)
    scene = robot_scene(robot, pose, joint_extras_on=joint_extras)
    out.write_bytes(scene.export(file_type="glb"))
    return out


# ------------------------------------------------------------------ rendering
def _default_renderer() -> Renderer:
    """``codeverse.spatial.render.render_glb`` when available, else the Blender fallback."""
    try:
        from codeverse.spatial.render import render_glb  # package C1

        return render_glb
    except Exception:
        return blender_render_glb


def blender_render_glb(glb: Path, out_dir: Path, *, views: Sequence[ViewPreset] | None = None, width: int = 512,
                       height: int = 512, sheet: bool = False, timeout_s: int = 180, **_: Any) -> RenderSet:
    """Minimal headless-Blender (Workbench) renderer used when the GPU renderer
    (package C1) is not importable.  Same signature subset as ``render_glb``."""
    from codeverse.config import get_settings

    blender = get_settings().resolve_blender()
    if not blender:
        raise RuntimeError("no Blender binary found for the fallback renderer")
    views = tuple(views or OBJECT_VIEWS_QUICK)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve().parent.parent / "languages" / "urdf" / "wrappers" / "render_glb_bpy.py"
    spec = {"glb": str(glb), "out_dir": str(out_dir), "width": width, "height": height,
            "views": [{"name": v.name, "az": v.azimuth_deg, "el": v.elevation_deg} for v in views]}
    spec_path = out_dir / "_render_spec.json"
    spec_path.write_text(json.dumps(spec))
    t0 = time.time()
    cmd = [blender, "-b", "--factory-startup", "--python", str(script), "--", str(spec_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, start_new_session=True)
    if proc.returncode != 0:
        raise RuntimeError(f"blender fallback render failed: {proc.stderr[-2000:] or proc.stdout[-2000:]}")
    rs = RenderSet(renderer="blender-workbench", duration_ms=int((time.time() - t0) * 1000))
    for v in views:
        p = out_dir / f"view_{v.name}.png"
        if not p.is_file():
            raise RuntimeError(f"blender fallback render produced no {p.name}; stdout tail: {proc.stdout[-800:]}")
        rs.views.append(RenderView(name=v.name, path=str(p), width=width, height=height))
    if sheet:
        rs.contact_sheet = str(make_sheet([(v.name, Path(v.path)) for v in rs.views], out_dir / "sheet.png"))
    return rs


def make_sheet(images: list[tuple[str, Path]], out: Path, *, cols: int = 4, tile: int = 384) -> Path:
    """Labelled grid of PNGs (PIL only; stand-in for ``codeverse.spatial.sheet.contact_sheet``)."""
    from PIL import Image, ImageDraw

    if not images:
        raise ValueError("make_sheet: no images")
    cols = max(1, min(cols, len(images)))
    rows = math.ceil(len(images) / cols)
    label_h = 22
    sheet = Image.new("RGB", (cols * tile, rows * (tile + label_h)), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (label, path) in enumerate(images):
        im = Image.open(path).convert("RGB")
        im.thumbnail((tile, tile))
        x, y = (i % cols) * tile, (i // cols) * (tile + label_h)
        sheet.paste(im, (x + (tile - im.width) // 2, y + label_h + (tile - im.height) // 2))
        draw.rectangle([x, y, x + tile - 1, y + label_h - 1], fill=(30, 30, 30))
        draw.text((x + 6, y + 4), label[:60], fill="white")
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def render_poses(
    robot: Robot,
    out_dir: Path | str,
    poses: list[tuple[str, dict[str, float]]] | None = None,
    *,
    renderer: Renderer | None = None,
    views: Sequence[ViewPreset] | None = None,
    width: int = 512,
    height: int = 512,
    sheet_view: str | None = None,
) -> list[tuple[str, RenderSet]]:
    """Export one GLB per pose, render ``views`` for each (default: 3 quick views)
    and write ``out_dir/articulation_sheet.png`` (rest / each joint at lower & upper
    by default — the image the judge sees).  Returns ``[(pose_name, RenderSet)]``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    poses = poses if poses is not None else limit_poses(robot)
    views = tuple(views or OBJECT_VIEWS_QUICK[:3])
    render = renderer or _default_renderer()
    results: list[tuple[str, RenderSet]] = []
    tiles: list[tuple[str, Path]] = []
    for label, q in poses:
        safe = "".join(c if c.isalnum() or c in "-_@." else "_" for c in label)
        glb = urdf_to_glb(robot, out_dir / f"pose_{safe}.glb", q)
        rs = render(glb, out_dir / f"pose_{safe}", views=views, width=width, height=height, sheet=False)
        results.append((label, rs))
        for v in rs.views:
            if sheet_view is None or v.name == sheet_view:
                tiles.append((f"{label} · {v.name}", Path(v.path)))
    make_sheet(tiles, out_dir / ARTICULATION_SHEET_NAME, cols=len(views) if sheet_view is None else 4)
    return results


# ------------------------------------------------------------------ tool-shaped entry point
def joint_sweep_observation(ws, *, n_random: int = 8, seed: int = 0, render: bool = True, out_dir: Path | None = None,
                            joint: str | None = None, expected_direction: str | None = None):
    """Run the pose sweep on ``ws.artifacts/robot.urdf`` (+ ``meshes/``) and return an
    ``Observation`` (package F wraps this as the ``joint_sweep`` tool)."""
    from codeverse.spatial.joints_model import load_urdf
    from codeverse.spatial.joints_poses import pose_samples
    from codeverse.spatial.joints_sweep import (
        motion_direction_check,
        report_numbers,
        summary_text,
        sweep_collisions,
    )
    from codeverse.spatial.registry import Observation

    urdf = Path(ws.artifacts) / "robot.urdf"
    if not urdf.is_file():
        return Observation.error("joint_sweep: artifacts/robot.urdf not found — run `build` first")
    try:
        robot = load_urdf(urdf, Path(ws.artifacts) / "meshes")
        report = sweep_collisions(robot, pose_samples(robot, n_random=n_random, seed=seed))
    except UrdfError as e:
        return Observation.error(f"joint_sweep: {e}")
    text = summary_text(report)
    numbers = report_numbers(report)
    if joint and expected_direction:
        mc = motion_direction_check(robot, joint, expected_direction)
        text += "\n" + mc.message
        numbers["motion_check"] = mc.model_dump(mode="json")
    images: list[str] = []
    if render:
        d = Path(out_dir) if out_dir else Path(ws.artifacts) / "tool_scratch" / "joint_sweep"
        render_poses(robot, d)
        images.append(str(d / ARTICULATION_SHEET_NAME))
    ok = report.summary.max_penetration_m <= report.tol_m and not report.summary.floating_links
    return Observation(ok=ok, text=text, numbers=numbers, images=images)


if __name__ == "__main__":  # tiny manual CLI: python -m codeverse.spatial.joints_export robot.urdf out.glb
    from codeverse.spatial.joints_model import load_urdf

    urdf_to_glb(load_urdf(sys.argv[1]), sys.argv[2])
