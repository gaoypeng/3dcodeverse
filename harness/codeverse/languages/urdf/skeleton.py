"""Starter files for ``urdf_blender`` from an :class:`ArticulatedPlan`.

Writes a RUNNABLE ``src/model.py`` (one placeholder box per link at the plan
bbox, named exactly like the link) and a CORRECT ``src/robot.urdf`` whose joint
origins / visual origins follow the enforced convention (see CONTRACT.md):

* root link frame = world origin;
* every other link frame = its parent joint's pivot (world), no rotation;
* joint ``<origin xyz>`` = pivot_world(child) − link_frame_world(parent);
* visual ``<origin xyz>`` = −link_frame_world(link)  (meshes are exported in
  world coordinates at the authored rest pose);
* URDF ``q = 0`` is the authored rest pose (the pose the plan's bboxes / pivots
  describe), so plan limits are shifted by ``rest``: ``lower-rest .. upper-rest``
  (identical to the plan's when ``rest == 0``, the usual case).  The rendered URDF
  says so in a comment next to every shifted ``<limit>`` so the agent who reads the
  plan table (unshifted values) and the skeleton sees one consistent rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codeverse.contracts.plan import ArticulatedPlan, JointPlan, PartPlan
from codeverse.conventions import to_snake
from codeverse.workspace import Workspace

DEFAULT_EFFORT = 10.0
DEFAULT_VELOCITY = 1.0


@dataclass(frozen=True)
class LinkFrame:
    """World placement of one link frame at rest + its plan bbox."""

    name: str
    frame_xyz: tuple[float, float, float]
    bbox_center: tuple[float, float, float]
    bbox_extents: tuple[float, float, float]
    parent_joint: str | None


@dataclass(frozen=True)
class JointRow:
    name: str
    type: str
    parent: str
    child: str
    origin_xyz: tuple[float, float, float]
    axis: tuple[float, float, float]
    lower: float | None
    upper: float | None
    rest: float = 0.0  # plan rest value the limits were shifted by (0 → limits == plan limits)


@dataclass
class UrdfFrames:
    robot_name: str
    root: str
    links: dict[str, LinkFrame]
    joints: list[JointRow]


def _fmt(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _vec(v: tuple[float, float, float]) -> str:
    return " ".join(_fmt(x) for x in v)


def _expand_parts(plan: ArticulatedPlan) -> list[tuple[str, PartPlan, tuple[float, float, float]]]:
    """(link_name, part, bbox_center) — parts with ``instances > 1`` become ``<name>_1..n``
    spread along +x so the baseline has no overlaps (the agent repositions them)."""
    out = []
    for p in plan.parts:
        base = to_snake(p.name)
        if p.instances <= 1:
            out.append((base, p, tuple(p.bbox.center)))
            continue
        cx, cy, cz = p.bbox.center
        step = p.bbox.extents[0] * 1.5
        for i in range(p.instances):
            out.append((f"{base}_{i + 1}", p, (cx + i * step, cy, cz)))
    return out


def compute_urdf_frames(plan: ArticulatedPlan) -> UrdfFrames:
    """Pure function: plan → link frames + joint rows in the enforced convention."""
    parts = _expand_parts(plan)
    base_names = {to_snake(p.name) for p in plan.parts}
    instance_links = {n for n, p, _ in parts if p.instances > 1}
    root = to_snake(plan.root_link)
    links: dict[str, LinkFrame] = {}
    pivot_of: dict[str, tuple[float, float, float]] = {}
    parent_of: dict[str, str] = {}
    joints: list[JointRow] = []

    offsets = {n: tuple(c - o for c, o in zip(center, p.bbox.center, strict=True)) for n, p, center in parts}

    def add_joint(j: JointPlan, child: str, parent: str, suffix: str = "") -> None:
        pivot_of[child] = tuple(float(v) + o for v, o in zip(j.pivot, offsets[child], strict=True))
        parent_of[child] = parent
        lower = upper = None
        if j.type in ("revolute", "prismatic"):
            lower, upper = j.lower - j.rest, j.upper - j.rest
        joints.append(JointRow(name=to_snake(j.name) + suffix, type=j.type, parent=parent, child=child,
                               origin_xyz=(0.0, 0.0, 0.0), axis=tuple(float(v) for v in j.axis), lower=lower, upper=upper,
                               rest=float(j.rest) if lower is not None else 0.0))

    for j in plan.joints:
        parent, child = to_snake(j.parent), to_snake(j.child)
        if parent in base_names and parent not in {n for n, _, _ in parts}:
            parent = f"{parent}_1"  # joint to an instanced parent → first instance
        children = [n for n, p, _ in parts if n == child or (n in instance_links and n.rsplit("_", 1)[0] == child)]
        for k, c in enumerate(children):
            add_joint(j, c, parent, "" if len(children) == 1 else f"_{k + 1}")

    # link frames: root at origin, others at their pivot
    for name, p, center in parts:
        frame = (0.0, 0.0, 0.0) if name == root else pivot_of.get(name, (0.0, 0.0, 0.0))
        pj = next((jr.name for jr in joints if jr.child == name), None)
        links[name] = LinkFrame(name=name, frame_xyz=frame, bbox_center=center,
                                bbox_extents=tuple(float(v) for v in p.bbox.extents), parent_joint=pj)
    # joint origins relative to the parent link frame
    fixed: list[JointRow] = []
    for jr in joints:
        pf = links[jr.parent].frame_xyz
        cf = links[jr.child].frame_xyz
        fixed.append(JointRow(name=jr.name, type=jr.type, parent=jr.parent, child=jr.child,
                              origin_xyz=tuple(c - p for c, p in zip(cf, pf, strict=True)), axis=jr.axis,
                              lower=jr.lower, upper=jr.upper, rest=jr.rest))
    return UrdfFrames(robot_name=to_snake(plan.object_name), root=root, links=links, joints=fixed)


# ------------------------------------------------------------------ text renderers
def _limit_note(jr: JointRow) -> str:
    """Comment explaining a rest-shifted limit (empty when the plan's rest is 0)."""
    if jr.lower is None or jr.upper is None or abs(jr.rest) < 1e-12:
        return ""
    return (f"  <!-- plan lower={_fmt(jr.lower + jr.rest)} upper={_fmt(jr.upper + jr.rest)} rest={_fmt(jr.rest)}: "
            f"the mesh is authored at rest, so q=0 = plan {_fmt(jr.rest)} and the limits are shifted by -rest -->")


def render_urdf(frames: UrdfFrames) -> str:
    lines = ['<?xml version="1.0"?>', f'<robot name="{frames.robot_name}">']
    lines.append("  <!-- link frames: root at the world origin; every other link frame sits at its joint pivot (world, rest pose). -->")
    lines.append("  <!-- visual/collision origin = -(link frame world) because meshes/<link>.glb hold WORLD coordinates at rest. -->")
    lines.append("  <!-- q=0 is the authored pose (the plan's rest pose); limits are the plan's lower-rest .. upper-rest. -->")
    for name, lf in frames.links.items():
        vis = tuple(-v for v in lf.frame_xyz)
        lines.append(f'  <link name="{name}">  <!-- frame at world {_vec(lf.frame_xyz)} -->')
        for tag in ("visual", "collision"):
            lines.append(f'    <{tag}><origin xyz="{_vec(vis)}" rpy="0 0 0"/><geometry><mesh filename="meshes/{name}.glb"/></geometry></{tag}>')
        lines.append("  </link>")
    for jr in frames.joints:
        lines.append(f'  <joint name="{jr.name}" type="{jr.type}">')
        lines.append(f'    <parent link="{jr.parent}"/>')
        lines.append(f'    <child link="{jr.child}"/>')
        lines.append(f'    <origin xyz="{_vec(jr.origin_xyz)}" rpy="0 0 0"/>  <!-- pivot_world(child) - frame_world(parent) -->')
        if jr.type != "fixed":
            lines.append(f'    <axis xyz="{_vec(jr.axis)}"/>')
            lim = f'effort="{_fmt(DEFAULT_EFFORT)}" velocity="{_fmt(DEFAULT_VELOCITY)}"'
            if jr.lower is not None and jr.upper is not None:
                lim = f'lower="{_fmt(jr.lower)}" upper="{_fmt(jr.upper)}" ' + lim
            lines.append(f"    <limit {lim}/>" + _limit_note(jr))
        lines.append("  </joint>")
    lines.append("</robot>")
    return "\n".join(lines) + "\n"


def render_model_py(plan: ArticulatedPlan, frames: UrdfFrames) -> str:
    parts_by_link = {n: p for n, p, _ in _expand_parts(plan)}
    head = f'''"""{plan.object_name} — link meshes for robot.urdf (pure bpy, Z-up, -Y front, meters).

CONTRACT: build ONE mesh object per URDF link, named EXACTLY like the link, placed
at its REST-POSE WORLD position (= URDF q=0, the pose the plan's bboxes describe).
Extra helper objects must be parented under a link object (they are joined into
it).  No cameras / lights / render / export calls.
Replace every placeholder box below with real, detailed geometry; keep the names.
"""
import bpy
import bmesh  # noqa: F401  (handy for detail)
import math   # noqa: F401
from mathutils import Vector  # noqa: F401


def box(name, center, size):
    """Axis-aligned box helper: center/size in world meters; returns the object."""
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=center)
    ob = bpy.context.active_object
    ob.name = name
    ob.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return ob


'''
    body = []
    for name, lf in frames.links.items():
        p = parts_by_link[name]
        role = (p.role or "").replace("\n", " ")
        desc = (p.description or "").replace("\n", " ")
        body.append(f"# link '{name}': {role}\n#   {desc}")
        if lf.parent_joint:
            jr = next(j for j in frames.joints if j.name == lf.parent_joint)
            note = f"  (plan rest={_fmt(jr.rest)} → this pose is URDF q=0)" if abs(jr.rest) > 1e-12 else ""
            body.append(f"#   joint '{lf.parent_joint}' pivot (world) = {lf.frame_xyz}{note}")
        body.append(f"{name} = box({name!r}, center={tuple(round(v, 4) for v in lf.bbox_center)}, "
                    f"size={tuple(round(v, 4) for v in lf.bbox_extents)})\n")
    tail = f"\n# Sanity: every link object exists\nfor _n in {list(frames.links)!r}:\n    assert _n in bpy.data.objects, _n\n"
    return head + "\n".join(body) + tail


def write_skeleton(ws: Workspace, plan: ArticulatedPlan) -> list[Path]:
    """Write src/model.py + src/robot.urdf; returns the written paths."""
    frames = compute_urdf_frames(plan)
    ws.src.mkdir(parents=True, exist_ok=True)
    model = ws.src / "model.py"
    urdf = ws.src / "robot.urdf"
    model.write_text(render_model_py(plan, frames))
    urdf.write_text(render_urdf(frames))
    return [model, urdf]
