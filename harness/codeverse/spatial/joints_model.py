"""URDF data model + loader + forward kinematics (numpy, trimesh).

Conventions (see ``codeverse/languages/urdf/CONTRACT.md``):

* URDF frames are Z-up, -Y front, meters.  ``<origin xyz rpy>`` uses the URDF
  rotation order ``R = Rz(yaw) · Ry(pitch) · Rx(roll)``.
* A joint's ``<origin>`` places the joint frame in the PARENT link frame; the
  child link frame coincides with the joint frame at ``q = 0``; ``<axis>`` is
  expressed in the joint frame.  ``T_child = T_parent · T_origin · Motion(q)``.
* Mesh files referenced by ``<mesh filename>`` are taken in RAW coordinates
  (standard URDF semantics).  Our wrapper writes ``meshes/<link>.glb`` with
  vertices in URDF (Z-up) coordinates (``export_yup=False``), so no axis
  conversion happens on load.  ``Link.mesh`` is stored in the LINK frame (the
  visual origin is already applied).
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh

MOVABLE_TYPES = ("revolute", "prismatic", "continuous")
JOINT_TYPES = MOVABLE_TYPES + ("fixed",)


class UrdfError(ValueError):
    """Raised when a URDF cannot be turned into a kinematic tree (use lint for details)."""


# ------------------------------------------------------------------ small math
def parse_floats(text: str | None, n: int, what: str) -> tuple[float, ...]:
    """Parse ``"a b c"`` → floats; raises ``UrdfError`` with ``what`` on bad input."""
    parts = (text or "").split()
    if len(parts) != n:
        raise UrdfError(f"{what}: expected {n} numbers, got {text!r}")
    try:
        return tuple(float(p) for p in parts)
    except ValueError as e:
        raise UrdfError(f"{what}: not numeric: {text!r}") from e


def rpy_to_matrix(rpy: tuple[float, float, float] | np.ndarray) -> np.ndarray:
    """URDF fixed-axis roll/pitch/yaw → 3x3 rotation (Rz·Ry·Rx)."""
    r, p, y = (float(v) for v in rpy)
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return rz @ ry @ rx


def matrix_to_rpy(R: np.ndarray) -> tuple[float, float, float]:
    """Inverse of :func:`rpy_to_matrix` (pitch in (-pi/2, pi/2) branch)."""
    sy = -R[2, 0]
    sy = max(-1.0, min(1.0, float(sy)))
    pitch = math.asin(sy)
    if abs(math.cos(pitch)) < 1e-9:  # gimbal lock: fold yaw into roll
        roll = math.atan2(-R[1, 2], R[1, 1])
        yaw = 0.0
    else:
        roll = math.atan2(R[2, 1], R[2, 2])
        yaw = math.atan2(R[1, 0], R[0, 0])
    return (roll, pitch, yaw)


def make_transform(xyz=(0.0, 0.0, 0.0), rpy=(0.0, 0.0, 0.0)) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = rpy_to_matrix(rpy)
    T[:3, 3] = np.asarray(xyz, dtype=float)
    return T


def invert_transform(T: np.ndarray) -> np.ndarray:
    R = T[:3, :3]
    out = np.eye(4)
    out[:3, :3] = R.T
    out[:3, 3] = -R.T @ T[:3, 3]
    return out


def rotation_about_axis(axis: np.ndarray, angle: float) -> np.ndarray:
    """Rodrigues rotation matrix (axis must be unit)."""
    x, y, z = (float(v) for v in axis)
    c, s, C = math.cos(angle), math.sin(angle), 1.0 - math.cos(angle)
    return np.array(
        [
            [c + x * x * C, x * y * C - z * s, x * z * C + y * s],
            [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
            [z * x * C - y * s, z * y * C + x * s, c + z * z * C],
        ]
    )


def parse_origin(elem: ET.Element | None, what: str) -> np.ndarray:
    """``<origin xyz rpy>`` child of ``elem`` → 4x4 (identity when absent)."""
    if elem is None:
        return np.eye(4)
    o = elem.find("origin")
    if o is None:
        return np.eye(4)
    xyz = parse_floats(o.get("xyz"), 3, f"{what} origin xyz") if o.get("xyz") is not None else (0.0, 0.0, 0.0)
    rpy = parse_floats(o.get("rpy"), 3, f"{what} origin rpy") if o.get("rpy") is not None else (0.0, 0.0, 0.0)
    return make_transform(xyz, rpy)


# ------------------------------------------------------------------ model
@dataclass
class Joint:
    name: str
    type: str
    parent: str
    child: str
    origin: np.ndarray = field(default_factory=lambda: np.eye(4))  # joint frame in parent frame
    axis: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.0, 0.0]))  # unit, joint frame
    lower: float | None = None
    upper: float | None = None
    effort: float = 0.0
    velocity: float = 0.0

    @property
    def movable(self) -> bool:
        return self.type in MOVABLE_TYPES

    def motion(self, q: float) -> np.ndarray:
        """Local motion transform for joint value ``q`` (identity for fixed)."""
        T = np.eye(4)
        if self.type in ("revolute", "continuous"):
            T[:3, :3] = rotation_about_axis(self.axis, q)
        elif self.type == "prismatic":
            T[:3, 3] = self.axis * q
        return T

    def clamp(self, q: float) -> float:
        if self.type in ("revolute", "prismatic") and self.lower is not None and self.upper is not None:
            return min(max(q, self.lower), self.upper)
        return q


@dataclass
class Link:
    name: str
    mesh: trimesh.Trimesh | None = None  # merged, in LINK frame (visual origin applied) — used for QC
    submeshes: list[trimesh.Trimesh] = field(default_factory=list)  # per-material pieces, LINK frame — used for export
    visual_origin: np.ndarray = field(default_factory=lambda: np.eye(4))
    mesh_file: str | None = None
    has_collision: bool = False

    @property
    def tri_count(self) -> int:
        return 0 if self.mesh is None else int(len(self.mesh.faces))


@dataclass
class Robot:
    name: str
    links: dict[str, Link]
    joints: dict[str, Joint]
    root: str
    urdf_path: str = ""

    # -------------------------------------------------------------- topology
    def parent_joint(self, link: str) -> Joint | None:
        for j in self.joints.values():
            if j.child == link:
                return j
        return None

    def child_joints(self, link: str) -> list[Joint]:
        return [j for j in self.joints.values() if j.parent == link]

    def movable_joints(self) -> list[Joint]:
        return [j for j in self.joints.values() if j.movable]

    def link_order(self) -> list[str]:
        """Links in parent-before-child order (DFS from root)."""
        out: list[str] = []
        stack = [self.root]
        while stack:
            cur = stack.pop()
            out.append(cur)
            stack.extend(j.child for j in reversed(self.child_joints(cur)))
        return out

    def chain(self, link: str) -> list[Joint]:
        """Joints from the root down to ``link`` (empty for the root)."""
        chain: list[Joint] = []
        cur = link
        while cur != self.root:
            j = self.parent_joint(cur)
            if j is None:
                raise UrdfError(f"link {link!r} is not connected to root {self.root!r}")
            chain.append(j)
            cur = j.parent
        return list(reversed(chain))

    def meshed_links(self) -> list[str]:
        return [n for n, link in self.links.items() if link.mesh is not None]


# ------------------------------------------------------------------ parsing
def _primitive_mesh(geom: ET.Element, what: str) -> trimesh.Trimesh:
    if (b := geom.find("box")) is not None:
        return trimesh.creation.box(parse_floats(b.get("size"), 3, f"{what} box size"))
    if (c := geom.find("cylinder")) is not None:
        return trimesh.creation.cylinder(radius=float(c.get("radius", 0)), height=float(c.get("length", 0)))
    if (s := geom.find("sphere")) is not None:
        return trimesh.creation.icosphere(subdivisions=3, radius=float(s.get("radius", 0)))
    raise UrdfError(f"{what}: unsupported geometry {[e.tag for e in geom]}")


def _load_mesh_file(filename: str, urdf_dir: Path, meshes_dir: Path | None, scale: tuple[float, ...] | None) -> list[trimesh.Trimesh]:
    """Load a mesh file as a list of per-material sub-meshes (transforms baked, raw coords)."""
    candidates = []
    fn = filename.replace("package://", "").replace("file://", "")
    if meshes_dir is not None:
        candidates.append(meshes_dir / Path(fn).name)
        candidates.append(meshes_dir.parent / fn)
    candidates.append(urdf_dir / fn)
    candidates.append(Path(fn))
    for p in candidates:
        if p.is_file():
            loaded = trimesh.load(p)
            subs = loaded.dump() if isinstance(loaded, trimesh.Scene) else [loaded]
            subs = [m for m in subs if isinstance(m, trimesh.Trimesh) and len(m.faces) > 0]
            if not subs:
                raise UrdfError(f"mesh {p} has no triangles")
            for m in subs:
                if scale is not None and any(abs(s - 1.0) > 1e-12 for s in scale):
                    m.apply_scale(scale)
            return subs
    raise UrdfError(f"mesh file not found: {filename!r} (tried {[str(c) for c in candidates]})")


def _link_from_xml(el: ET.Element, urdf_dir: Path, meshes_dir: Path | None, *, load_meshes: bool) -> Link:
    name = el.get("name", "")
    link = Link(name=name, has_collision=el.find("collision") is not None)
    parts: list[trimesh.Trimesh] = []
    first_origin: np.ndarray | None = None
    for vis in el.findall("visual"):
        T_vis = parse_origin(vis, f"link {name} visual")
        geom = vis.find("geometry")
        if geom is None:
            raise UrdfError(f"link {name}: visual without <geometry>")
        if first_origin is None:
            first_origin = T_vis
        mesh_el = geom.find("mesh")
        if mesh_el is not None:
            link.mesh_file = mesh_el.get("filename", "")
            if not load_meshes:
                continue
            scale = parse_floats(mesh_el.get("scale"), 3, f"link {name} mesh scale") if mesh_el.get("scale") else None
            pieces = _load_mesh_file(link.mesh_file, urdf_dir, meshes_dir, scale)
        else:
            pieces = [_primitive_mesh(geom, f"link {name}")]
        for m in pieces:
            m = m.copy()
            m.apply_transform(T_vis)
            parts.append(m)
    if first_origin is not None:
        link.visual_origin = first_origin
    if parts:
        link.submeshes = parts
        merged = parts[0].copy() if len(parts) == 1 else trimesh.util.concatenate(parts)
        # exporters split vertices per normal/uv; merge them back so topology (watertight, islands) is real
        merged.merge_vertices(merge_tex=True, merge_norm=True)
        link.mesh = merged
    return link


def _joint_from_xml(el: ET.Element) -> Joint:
    name = el.get("name", "")
    jtype = el.get("type", "")
    if jtype not in JOINT_TYPES:
        raise UrdfError(f"joint {name}: unsupported type {jtype!r}")
    parent = el.find("parent")
    child = el.find("child")
    if parent is None or child is None or not parent.get("link") or not child.get("link"):
        raise UrdfError(f"joint {name}: needs <parent link> and <child link>")
    j = Joint(name=name, type=jtype, parent=parent.get("link", ""), child=child.get("link", ""))
    j.origin = parse_origin(el, f"joint {name}")
    ax = el.find("axis")
    if ax is not None and ax.get("xyz") is not None:
        a = np.asarray(parse_floats(ax.get("xyz"), 3, f"joint {name} axis"), dtype=float)
        n = float(np.linalg.norm(a))
        if n < 1e-9:
            raise UrdfError(f"joint {name}: zero axis")
        j.axis = a / n
    lim = el.find("limit")
    if lim is not None:
        j.effort = float(lim.get("effort", 0) or 0)
        j.velocity = float(lim.get("velocity", 0) or 0)
        if lim.get("lower") is not None:
            j.lower = float(lim.get("lower"))
        if lim.get("upper") is not None:
            j.upper = float(lim.get("upper"))
    if jtype in ("revolute", "prismatic"):
        if j.lower is None or j.upper is None:
            raise UrdfError(f"joint {name}: {jtype} joints need <limit lower upper>")
        if j.upper < j.lower:
            raise UrdfError(f"joint {name}: upper {j.upper} < lower {j.lower}")
    return j


def load_urdf(urdf_path: Path | str, meshes_dir: Path | str | None = None, *, load_meshes: bool = True) -> Robot:
    """Parse a URDF file into a :class:`Robot` (strict: raises ``UrdfError``).

    ``meshes_dir`` overrides where ``<mesh filename>`` basenames are looked up
    (default: relative to the URDF file).  Mesh coordinates are taken raw.
    """
    urdf_path = Path(urdf_path)
    try:
        root_el = ET.parse(urdf_path).getroot()
    except ET.ParseError as e:
        raise UrdfError(f"{urdf_path.name}: XML parse error: {e}") from e
    if root_el.tag != "robot":
        raise UrdfError(f"{urdf_path.name}: root element must be <robot>, got <{root_el.tag}>")
    meshes = Path(meshes_dir) if meshes_dir is not None else None
    links = {}
    for el in root_el.findall("link"):
        link = _link_from_xml(el, urdf_path.parent, meshes, load_meshes=load_meshes)
        if link.name in links:
            raise UrdfError(f"duplicate link name {link.name!r}")
        links[link.name] = link
    joints = {}
    for el in root_el.findall("joint"):
        j = _joint_from_xml(el)
        if j.name in joints:
            raise UrdfError(f"duplicate joint name {j.name!r}")
        if j.parent not in links or j.child not in links:
            raise UrdfError(f"joint {j.name}: unknown parent/child link ({j.parent!r}/{j.child!r})")
        joints[j.name] = j
    if not links:
        raise UrdfError("URDF has no links")
    children = [j.child for j in joints.values()]
    if len(set(children)) != len(children):
        dup = sorted({c for c in children if children.count(c) > 1})
        raise UrdfError(f"links with more than one parent joint: {dup}")
    roots = [n for n in links if n not in children]
    if len(roots) != 1:
        raise UrdfError(f"expected exactly one root link, found {roots}")
    robot = Robot(name=root_el.get("name", "robot"), links=links, joints=joints, root=roots[0], urdf_path=str(urdf_path))
    if len(robot.link_order()) != len(links):
        raise UrdfError("joint graph contains a cycle or unreachable links")
    return robot


# ------------------------------------------------------------------ FK
def fk(robot: Robot, q: dict[str, float] | None = None, *, clamp: bool = False) -> dict[str, np.ndarray]:
    """World transform of every link frame for joint values ``q`` (missing → 0)."""
    q = q or {}
    unknown = set(q) - set(robot.joints)
    if unknown:
        raise UrdfError(f"fk: unknown joints {sorted(unknown)}; known: {sorted(robot.joints)}")
    out: dict[str, np.ndarray] = {robot.root: np.eye(4)}
    for name in robot.link_order():
        if name == robot.root:
            continue
        j = robot.parent_joint(name)
        assert j is not None
        val = float(q.get(j.name, 0.0))
        if clamp:
            val = j.clamp(val)
        out[name] = out[j.parent] @ j.origin @ j.motion(val)
    return out


def link_world_meshes(robot: Robot, q: dict[str, float] | None = None) -> dict[str, trimesh.Trimesh]:
    """Posed copies of every link mesh in world coordinates."""
    T = fk(robot, q)
    out = {}
    for name, link in robot.links.items():
        if link.mesh is None:
            continue
        m = link.mesh.copy()
        m.apply_transform(T[name])
        out[name] = m
    return out


def world_bboxes(robot: Robot, q: dict[str, float] | None = None) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Per-link world AABB (min, max) at pose ``q``."""
    T = fk(robot, q)
    out = {}
    for name, link in robot.links.items():
        if link.mesh is None:
            continue
        pts = trimesh.transform_points(link.mesh.vertices, T[name])
        out[name] = (pts.min(axis=0), pts.max(axis=0))
    return out
