"""Self-contained CadQuery reimplementation of the Articraft `sdk` public API.

This is a clean-room shim: it exposes the SAME names that generated Articraft
model scripts import via `from sdk import (...)`, but every geometry call
produces real parametric CadQuery B-rep instead of triangle meshes. A model
script can run unchanged (minus the sdk import) and then:

    result = object_model.to_cq()        # whole model, REST pose, cq.Assembly

The geometry semantics mirror the original Apache-2.0 Articraft SDK
(github mattzh72/articraft); only cadquery + the stdlib are required.

Modular: each public name is grouped so a generator can inline only the
pieces a given model actually uses (see tools/gen_code.py).
"""
from __future__ import annotations

import math
import cadquery as cq

# ===========================================================================
# @section core-transform   (always required)
# ===========================================================================
def _rpy_R(r, p, y):
    """Rotation matrix Rz(yaw)@Ry(pitch)@Rx(roll) as rows (URDF convention)."""
    cr, sr = math.cos(r), math.sin(r)
    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)
    return (
        (cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
        (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
        (-sp,     cp * sr,                cp * cr),
    )


def _mat_mul(A, B):
    return tuple(
        tuple(sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3))
        for i in range(3)
    )


def _mat_vec(A, v):
    return tuple(sum(A[i][k] * v[k] for k in range(3)) for i in range(3))


class _Tf:
    """Rigid transform: rotation matrix R (rows) + translation t."""
    __slots__ = ("R", "t")
    _I = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

    def __init__(self, R=None, t=(0.0, 0.0, 0.0)):
        self.R = R if R is not None else _Tf._I
        self.t = (float(t[0]), float(t[1]), float(t[2]))

    @staticmethod
    def from_origin(origin):
        if origin is None:
            return _Tf()
        xyz = getattr(origin, "xyz", (0.0, 0.0, 0.0))
        rpy = getattr(origin, "rpy", (0.0, 0.0, 0.0))
        return _Tf(_rpy_R(rpy[0], rpy[1], rpy[2]), xyz)

    def __matmul__(self, o):
        return _Tf(_mat_mul(self.R, o.R),
                   tuple(a + b for a, b in zip(_mat_vec(self.R, o.t), self.t)))

    def location(self):
        R, t = self.R, self.t
        xdir = cq.Vector(R[0][0], R[1][0], R[2][0])
        zdir = cq.Vector(R[0][2], R[1][2], R[2][2])
        return cq.Location(cq.Plane(origin=cq.Vector(*t), xDir=xdir, normal=zdir))


# ===========================================================================
# @section core-errors
# ===========================================================================
class ValidationError(ValueError):
    pass


# ===========================================================================
# @section core-types   (always required: Origin/Material/limits/enums)
# ===========================================================================
class ArticulationType:
    REVOLUTE = "revolute"
    CONTINUOUS = "continuous"
    PRISMATIC = "prismatic"
    FIXED = "fixed"
    FLOATING = "floating"


class Origin:
    __slots__ = ("xyz", "rpy")

    def __init__(self, xyz=(0.0, 0.0, 0.0), rpy=(0.0, 0.0, 0.0)):
        self.xyz = (float(xyz[0]), float(xyz[1]), float(xyz[2]))
        self.rpy = (float(rpy[0]), float(rpy[1]), float(rpy[2]))


class Material:
    def __init__(self, name, rgba=None, texture=None, *, color=None):
        self.name = str(name)
        c = rgba if rgba is not None else color
        if c is not None:
            c = tuple(float(v) for v in c)
            if len(c) == 3:
                c = c + (1.0,)
        self.rgba = c
        self.texture = texture


class MotionLimits:
    def __init__(self, effort=1.0, velocity=1.0, lower=None, upper=None):
        self.effort = float(effort)
        self.velocity = float(velocity)
        self.lower = None if lower is None else float(lower)
        self.upper = None if upper is None else float(upper)


class MotionProperties:
    def __init__(self, damping=None, friction=None):
        self.damping = damping
        self.friction = friction


class Mimic:
    def __init__(self, joint, multiplier=1.0, offset=0.0):
        self.joint = str(joint)
        self.multiplier = float(multiplier)
        self.offset = float(offset)


class Inertia:
    def __init__(self, ixx=0.0, ixy=0.0, ixz=0.0, iyy=0.0, iyz=0.0, izz=0.0):
        self.ixx, self.ixy, self.ixz = ixx, ixy, ixz
        self.iyy, self.iyz, self.izz = iyy, iyz, izz


class Inertial:
    def __init__(self, mass=1.0, inertia=None, origin=None):
        self.mass = float(mass)
        self.inertia = inertia
        self.origin = origin or Origin()

    @staticmethod
    def from_geometry(geometry, mass, *, origin=None):
        return Inertial(mass=mass, inertia=Inertia(), origin=origin or Origin())


# ===========================================================================
# @section core-primitives   (Box / Cylinder / Sphere data + cq realization)
# ===========================================================================
class Box:
    __slots__ = ("size",)

    def __init__(self, size):
        self.size = (float(size[0]), float(size[1]), float(size[2]))

    def _cq(self):
        sx, sy, sz = self.size
        return cq.Solid.makeBox(sx, sy, sz, cq.Vector(-sx / 2, -sy / 2, -sz / 2))


class Cylinder:
    __slots__ = ("radius", "length")

    def __init__(self, radius, length=None, *, height=None):
        self.radius = float(radius)
        self.length = float(height if length is None else length)

    @property
    def height(self):
        return self.length

    def _cq(self):
        return cq.Solid.makeCylinder(
            self.radius, self.length, cq.Vector(0, 0, -self.length / 2), cq.Vector(0, 0, 1)
        )


class Sphere:
    __slots__ = ("radius",)

    def __init__(self, radius):
        self.radius = float(radius)

    def _cq(self):
        return cq.Solid.makeSphere(self.radius, angleDegrees1=-90, angleDegrees2=90,
                                   angleDegrees3=360)


# ===========================================================================
# @section core-mesh   (mesh_from_cadquery / mesh_from_geometry passthrough)
# ===========================================================================
def _to_shape(model):
    """Coerce a cadquery object (Workplane/Shape/Assembly) or a shim geometry
    wrapper into a single cq.Shape."""
    if isinstance(model, _CQMesh):
        return model.shape
    if hasattr(model, "_cq"):
        return model._cq()
    if isinstance(model, cq.Assembly):
        return model.toCompound()
    if isinstance(model, cq.Workplane):
        vals = [v for v in model.vals() if isinstance(v, cq.Shape)]
        if not vals:
            raise ValidationError("cadquery Workplane has no shape values")
        return vals[0] if len(vals) == 1 else cq.Compound.makeCompound(vals)
    if isinstance(model, cq.Shape):
        return model
    raise ValidationError("unsupported cadquery model type: %r" % type(model))


class _CQMesh:
    """Mesh stand-in that simply carries an exact cadquery B-rep shape."""
    __slots__ = ("shape", "name")

    def __init__(self, shape, name=None):
        self.shape = shape
        self.name = name

    def _cq(self):
        return self.shape


# alias name some models expect
Mesh = _CQMesh


def _scale_shape(shape, s):
    """Honor unit_scale (e.g. models authored in mm, exported to meters with
    unit_scale=0.001): uniformly scale the B-rep about the origin."""
    if not s or s == 1.0 or shape is None:
        return shape
    try:
        return shape.transformGeometry(cq.Matrix([[s, 0, 0, 0], [0, s, 0, 0], [0, 0, s, 0]]))
    except Exception:
        return shape


def mesh_from_cadquery(model, name=None, **kwargs):
    return _CQMesh(_scale_shape(_to_shape(model), kwargs.get("unit_scale", 1.0)),
                   name=name if isinstance(name, str) else None)


def mesh_from_geometry(geometry, name=None, **kwargs):
    return _CQMesh(_scale_shape(_to_shape(geometry), kwargs.get("unit_scale", 1.0)),
                   name=name if isinstance(name, str) else None)


def mesh_from_input(name):
    raise ValidationError("mesh_from_input (external asset) is not supported in self-contained mode")


# ===========================================================================
# @section core-model   (Visual / Part / Articulation / ArticulatedObject)
# ===========================================================================
class Visual:
    __slots__ = ("geometry", "origin", "material", "name")

    def __init__(self, geometry, origin=None, material=None, name=None):
        self.geometry = geometry
        self.origin = origin or Origin()
        self.material = material
        self.name = name


class Part:
    def __init__(self, name, visuals=None, inertial=None, meta=None):
        self.name = name
        self.visuals = list(visuals or [])
        self.collisions = []
        self.inertial = inertial
        self.meta = dict(meta or {})

    def visual(self, geometry, *, origin=None, material=None, color=None, name=None):
        if color is not None and material is None:
            material = color if isinstance(color, (str, Material)) else Material("inline", rgba=color)
        v = Visual(geometry, origin=origin, material=material, name=name)
        self.visuals.append(v)
        return v

    def get_visual(self, name):
        for v in self.visuals:
            if v.name == name:
                return v
        raise ValidationError("unknown visual %r" % name)


class Articulation:
    def __init__(self, name, articulation_type, parent, child, origin=None,
                 axis=(0.0, 0.0, 1.0), motion_limits=None, motion_properties=None,
                 mimic=None, meta=None):
        self.name = name
        self.articulation_type = articulation_type
        self.parent = parent if isinstance(parent, str) else parent.name
        self.child = child if isinstance(child, str) else child.name
        self.origin = origin or Origin()
        self.axis = tuple(float(a) for a in axis)
        self.motion_limits = motion_limits
        self.motion_properties = motion_properties
        self.mimic = mimic
        self.meta = dict(meta or {})

    # aliases used by some scripts
    @property
    def joint_type(self):
        return self.articulation_type

    @property
    def limit(self):
        return self.motion_limits


class ArticulatedObject:
    def __init__(self, name, parts=None, articulations=None, materials=None, meta=None, assets=None):
        self.name = name
        self.parts = list(parts or [])
        self.articulations = list(articulations or [])
        self.materials = list(materials or [])
        self.meta = dict(meta or {})
        self.assets = assets

    # ---- builders ----
    def part(self, name, *, visuals=None, inertial=None, meta=None):
        p = Part(name, visuals=visuals, inertial=inertial, meta=meta)
        self.parts.append(p)
        return p

    link = part

    def articulation(self, name, articulation_type, parent, child, *, origin=None,
                     axis=None, motion_limits=None, motion_properties=None, mimic=None, meta=None):
        a = Articulation(name, articulation_type, parent, child, origin=origin,
                         axis=axis or (0.0, 0.0, 1.0), motion_limits=motion_limits,
                         motion_properties=motion_properties, mimic=mimic, meta=meta)
        self.articulations.append(a)
        return a

    def joint(self, name, joint_type, parent, child, *, origin=None, axis=None,
              limit=None, dynamics=None, mimic=None, meta=None):
        return self.articulation(name, joint_type, parent, child, origin=origin, axis=axis,
                                 motion_limits=limit, motion_properties=dynamics, mimic=mimic, meta=meta)

    def material(self, name, *, rgba=None, color=None, texture=None):
        m = Material(name, rgba=rgba, color=color, texture=texture)
        self.materials.append(m)
        return m

    def set_assets(self, assets):
        self.assets = assets
        return assets

    # ---- queries ----
    def get_part(self, name):
        key = name if isinstance(name, str) else name.name
        for p in self.parts:
            if p.name == key:
                return p
        raise ValidationError("unknown part %r" % name)

    get_link = get_part

    def get_articulation(self, name):
        key = name if isinstance(name, str) else getattr(name, "name", name)
        for a in self.articulations:
            if a.name == key:
                return a
        raise ValidationError("unknown articulation %r" % name)

    get_joint = get_articulation

    def root_parts(self):
        children = {a.child for a in self.articulations}
        return [p for p in self.parts if p.name not in children]

    root_links = root_parts

    def validate(self, *a, **k):
        return None

    def to_urdf(self, *a, **k):
        raise ValidationError("URDF export is not available in self-contained CadQuery mode")

    # ---- rest-pose world transform per part (URDF forward kinematics, q=0) ----
    def _world_transforms(self):
        parent_of, joint_tf = {}, {}
        for a in self.articulations:
            parent_of[a.child] = a.parent
            joint_tf[a.child] = _Tf.from_origin(a.origin)
        cache = {}

        def world(name):
            if name in cache:
                return cache[name]
            if name in parent_of:
                tf = world(parent_of[name]) @ joint_tf[name]
            else:
                tf = _Tf()
            cache[name] = tf
            return tf

        return {p.name: world(p.name) for p in self.parts}

    # ---- assemble to a CadQuery Assembly in rest pose ----
    def to_cq(self):
        worlds = self._world_transforms()
        asm = cq.Assembly(name=self.name or "model")
        idx = 0
        for part in self.parts:
            wt = worlds.get(part.name, _Tf())
            for vis in part.visuals:
                try:
                    shape = _to_shape(vis.geometry)
                except Exception as exc:
                    raise ValidationError(
                        "part %r visual %r: %s" % (part.name, vis.name, exc)
                    )
                loc = (wt @ _Tf.from_origin(vis.origin)).location()
                rgba = None
                mat = vis.material
                if isinstance(mat, str):
                    mat = next((m for m in self.materials if m.name == mat), None)
                if isinstance(mat, Material) and mat.rgba:
                    rgba = mat.rgba
                kw = {"name": "%s_%d" % (part.name, idx)}
                if rgba:
                    kw["color"] = cq.Color(*rgba)
                asm.add(shape, loc=loc, **kw)
                idx += 1
        return asm

    # convenience aliases a few scripts might call
    def to_assembly(self):
        return self.to_cq()

    def compound(self):
        return self.to_cq().toCompound()


# ===========================================================================
# @section core-testing-stubs   (TestContext/TestReport: never executed for
# geometry, but must be importable; run_tests() is not called by the epilogue)
# ===========================================================================
class _Nullctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestReport:
    def __init__(self, *a, **k):
        self.checks = []
        self.passed = True


class TestContext:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, _name):
        def _noop(*a, **k):
            return None
        return _noop

    def pose(self, *a, **k):
        return _Nullctx()

    def report(self, *a, **k):
        return TestReport()


class ValidationReport(TestReport):
    pass


# ===========================================================================
# @section core-assets-stubs   (AssetContext etc.: not needed for geometry)
# ===========================================================================
class _NoOp:
    """Universal no-op sentinel that is callable AND Path-like, so asset/IO
    expressions in model scripts (e.g. ASSETS.mesh_dir.mkdir(); dir / 'x.obj')
    are harmless in self-contained mode."""
    def __call__(self, *a, **k):
        return self

    def __getattr__(self, _n):
        return self

    def __truediv__(self, _o):
        return self

    def __rtruediv__(self, _o):
        return self

    def __fspath__(self):
        return "/tmp/_articraft_noop"

    def mkdir(self, *a, **k):
        return None


_NOOP = _NoOp()


class _NoopMeta(type):
    def __getattr__(cls, _name):
        def _noop(*a, **k):
            return cls()
        return _noop


class AssetContext(metaclass=_NoopMeta):
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, _n):
        return _NOOP


class AssetSession(AssetContext):
    pass


def coerce_asset_context(*a, **k):
    return None


def resolve_asset_context(*a, **k):
    return None


class AssetContextLike(AssetContext):
    pass


# ===========================================================================
# @section geom-base   (MeshGeometry base shared by all geometry families)
# ===========================================================================
def _solid_from_triangles(vertices, faces):
    """Fallback: wrap a raw triangle soup as a B-rep solid (for MeshGeometry
    instances that are authored vertex/face directly)."""
    tri_faces = []
    for (a, b, c) in faces:
        va, vb, vc = vertices[a], vertices[b], vertices[c]
        if va == vb or vb == vc or vc == va:
            continue
        w = cq.Wire.makePolygon([cq.Vector(*va), cq.Vector(*vb), cq.Vector(*vc)], close=True)
        try:
            tri_faces.append(cq.Face.makeFromWires(w))
        except Exception:
            continue
    if not tri_faces:
        raise ValidationError("MeshGeometry: no valid triangles to build a solid")
    shell = cq.Shell.makeShell(tri_faces)
    try:
        return cq.Solid.makeSolid(shell)
    except Exception:
        return cq.Compound.makeCompound(tri_faces)


class MeshGeometry:
    """Base for all geometry families. Subclasses build parametric CadQuery
    B-rep and call ``self._set(shape)``. Supports the mesh-style manipulation
    API (translate/rotate/scale/merge/copy) used by Articraft model scripts,
    applied to the underlying CadQuery shape."""

    def __init__(self, vertices=None, faces=None):
        self._raw_verts = [tuple(v) for v in (vertices or [])]
        self._raw_faces = [tuple(f) for f in (faces or [])]
        self._shape = None
        self._tess = None

    # subclasses call this with their cadquery shape
    def _set(self, shape):
        self._shape = _to_shape(shape) if not isinstance(shape, cq.Shape) else shape
        self._tess = None
        return self

    def _tessellate(self):
        """Triangle mesh (vertices, faces) of the B-rep — some model scripts read
        .vertices/.faces to manipulate raw geometry (e.g. axis swaps)."""
        if self._tess is None:
            if self._shape is None:
                self._tess = (list(self._raw_verts), list(self._raw_faces))
            else:
                try:
                    vs, ts = self._shape.tessellate(0.0005)
                    self._tess = ([(v.x, v.y, v.z) for v in vs],
                                  [tuple(int(i) for i in t) for t in ts])
                except Exception:
                    self._tess = (list(self._raw_verts), list(self._raw_faces))
        return self._tess

    @property
    def vertices(self):
        return self._raw_verts if self._raw_verts else self._tessellate()[0]

    @vertices.setter
    def vertices(self, v):
        # capture the B-rep's faces first so a vertices-ONLY reassignment
        # (read mesh -> transform verts -> assign back) doesn't lose the faces
        if self._shape is not None and not self._raw_faces:
            self._raw_faces = list(self._tessellate()[1])
        self._raw_verts = [tuple(x) for x in (v or [])]
        self._shape = None; self._tess = None

    @property
    def faces(self):
        return self._raw_faces if self._raw_faces else self._tessellate()[1]

    @faces.setter
    def faces(self, f):
        if self._shape is not None and not self._raw_verts:
            self._raw_verts = list(self._tessellate()[0])
        self._raw_faces = [tuple(x) for x in (f or [])]
        self._shape = None; self._tess = None

    def _cq(self):
        if self._shape is not None:
            return self._shape
        return _solid_from_triangles(self._raw_verts, self._raw_faces)

    # ---- mesh-style authoring (raw triangle fallback path) ----
    def add_vertex(self, x, y, z):
        self._raw_verts.append((float(x), float(y), float(z)))
        self._shape = None; self._tess = None
        return len(self._raw_verts) - 1

    def add_face(self, a, b, c):
        self._raw_faces.append((int(a), int(b), int(c)))
        self._shape = None; self._tess = None

    def copy(self):
        g = MeshGeometry(self._raw_verts, self._raw_faces)
        g._shape = self._shape
        return g

    clone = copy

    # ---- rigid / affine transforms (operate on the cadquery shape) ----
    def translate(self, dx, dy=None, dz=None):
        if dy is None:
            dx, dy, dz = dx
        self._shape = self._cq().translate(cq.Vector(float(dx), float(dy), float(dz)))
        return self

    def rotate(self, axis, angle, *, origin=(0.0, 0.0, 0.0)):
        # Articraft angles are RADIANS; cadquery rotate takes degrees about a line.
        o = cq.Vector(float(origin[0]), float(origin[1]), float(origin[2]))
        a = cq.Vector(float(axis[0]), float(axis[1]), float(axis[2]))
        self._shape = self._cq().rotate(o, o + a, math.degrees(float(angle)))
        return self

    def scale(self, sx, sy=None, sz=None):
        if sy is None:
            sy = sz = sx
        if sz is None:
            sz = sx
        m = cq.Matrix([[float(sx), 0, 0, 0], [0, float(sy), 0, 0], [0, 0, float(sz), 0]])
        self._shape = self._cq().transformGeometry(m)
        return self

    def rotate_x(self, angle):
        return self.rotate((1.0, 0.0, 0.0), angle)

    def rotate_y(self, angle):
        return self.rotate((0.0, 1.0, 0.0), angle)

    def rotate_z(self, angle):
        return self.rotate((0.0, 0.0, 1.0), angle)

    def merge(self, other):
        o = _to_shape(other)
        if self._shape is None and not self._raw_verts:
            self._shape = o          # empty accumulator: adopt other's shape
        else:
            a = self._cq()
            try:
                fused = a.fuse(o)
                if fused is None or fused.wrapped is None or fused.wrapped.IsNull():
                    raise ValueError("null fuse result")
                self._shape = fused
            except Exception:
                # OCC fuse can fail/return null on many thin/degenerate parts
                # (e.g. a balloon whisk's wire loops) — fall back to a non-boolean
                # Compound, which is equivalent for surface sampling / rendering.
                self._shape = cq.Compound.makeCompound([a, o])
        self._tess = None
        return self

    union = merge

    def cut(self, other):
        self._shape = self._cq().cut(_to_shape(other))
        return self

    def difference(self, other):
        return self.cut(other)

    def intersect(self, other):
        self._shape = self._cq().intersect(_to_shape(other))
        return self


# boolean free-functions used by some scripts
def boolean_difference(a, b, *more):
    s = _to_shape(a).cut(_to_shape(b))
    for m in more:
        s = s.cut(_to_shape(m))
    return MeshGeometry()._set(s)


def boolean_union(a, *more):
    s = _to_shape(a)
    for m in more:
        s = s.fuse(_to_shape(m))
    return MeshGeometry()._set(s)


def boolean_intersection(a, b, *more):
    s = _to_shape(a).intersect(_to_shape(b))
    for m in more:
        s = s.intersect(_to_shape(m))
    return MeshGeometry()._set(s)


def repair_loft(geometry, *a, **k):
    return geometry


# ===========================================================================
# @section geom-profiles   (pure-python 2D profile generators, verbatim from
# the Apache-2.0 Articraft SDK _mesh/common.py)
# ===========================================================================
_EPS = 1e-9


def _points_match_2d(a, b, tol=1e-9):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _polygon_area(points):
    area = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return area * 0.5


def _ensure_ccw(points):
    area = _polygon_area(points)
    if abs(area) <= 1e-9:
        raise ValidationError("Profile area must be non-zero")
    return list(reversed(points)) if area < 0 else points


def _profile_points_2d(profile):
    points = [(float(x), float(y)) for (x, y) in profile]
    cleaned = []
    for pt in points:
        if not cleaned or not _points_match_2d(cleaned[-1], pt):
            cleaned.append(pt)
    if len(cleaned) >= 2 and _points_match_2d(cleaned[0], cleaned[-1]):
        cleaned = cleaned[:-1]
    if len(cleaned) < 3:
        raise ValidationError("Profile must have at least 3 unique points")
    return cleaned


def _polyline_points_2d(profile):
    points = [(float(x), float(y)) for (x, y) in profile]
    cleaned = []
    for pt in points:
        if not cleaned or not _points_match_2d(cleaned[-1], pt):
            cleaned.append(pt)
    if len(cleaned) < 2:
        raise ValidationError("Profile must have at least 2 unique points")
    return cleaned


def _profile_points_3d(profile):
    points = [(float(x), float(y), float(z)) for (x, y, z) in profile]
    cleaned = []
    for pt in points:
        if not cleaned or not (abs(cleaned[-1][0] - pt[0]) <= 1e-9 and abs(cleaned[-1][1] - pt[1]) <= 1e-9 and abs(cleaned[-1][2] - pt[2]) <= 1e-9):
            cleaned.append(pt)
    if len(cleaned) >= 2 and abs(cleaned[0][0] - cleaned[-1][0]) <= 1e-9 and abs(cleaned[0][1] - cleaned[-1][1]) <= 1e-9 and abs(cleaned[0][2] - cleaned[-1][2]) <= 1e-9:
        cleaned = cleaned[:-1]
    if len(cleaned) < 2:
        raise ValidationError("Profile must have at least 2 unique points")
    return cleaned


def _polygon_centroid(points):
    a = _polygon_area(points)
    if abs(a) <= _EPS:
        n = len(points)
        return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)
    cx = cy = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        cr = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    return (cx / (6.0 * a), cy / (6.0 * a))


def _point_in_polygon(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-30) + xi):
            inside = not inside
        j = i
    return inside


def superellipse_profile(width, height, exponent=2.6, *, segments=48):
    width = float(width); height = float(height); exponent = float(exponent)
    segments = max(12, int(segments))
    if width <= 0 or height <= 0:
        raise ValidationError("width/height must be positive")
    if exponent <= 0:
        raise ValidationError("exponent must be positive")
    a = width * 0.5; b = height * 0.5; power = 2.0 / exponent
    pts = []
    for i in range(segments):
        t = 2.0 * math.pi * i / segments
        ct = math.cos(t); st = math.sin(t)
        x = a * (abs(ct) ** power); y = b * (abs(st) ** power)
        if ct < 0:
            x = -x
        if st < 0:
            y = -y
        pts.append((x, y))
    return pts


def rounded_rect_profile(width, height, radius, *, corner_segments=6):
    width = float(width); height = float(height); radius = float(radius)
    if width <= 0 or height <= 0:
        raise ValidationError("width/height must be positive")
    corner_segments = max(1, int(corner_segments))
    max_r = 0.5 * min(width, height)
    if radius < 0 or radius > max_r:
        raise ValidationError("radius must be within [0, %s]" % max_r)
    hw = width * 0.5; hh = height * 0.5
    if radius <= 1e-12:
        return [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    r = radius

    def arc(cx, cy, a0, a1):
        out = []
        for i in range(corner_segments + 1):
            t = i / corner_segments
            a = a0 + (a1 - a0) * t
            out.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        return out

    pts = []
    pts.extend(arc(hw - r, -hh + r, -math.pi / 2.0, 0.0))
    pts.extend(arc(hw - r, hh - r, 0.0, math.pi / 2.0)[1:])
    pts.extend(arc(-hw + r, hh - r, math.pi / 2.0, math.pi)[1:])
    pts.extend(arc(-hw + r, -hh + r, math.pi, 3.0 * math.pi / 2.0)[1:])
    return pts


# ---- cadquery profile/solid builders shared by geometry families ----
def _wire_2d(points, plane="XY", close=True):
    """Build a cq.Wire from a list of 2D points placed on the given plane."""
    pts = [(float(x), float(y)) for (x, y) in points]
    wp = cq.Workplane(plane).polyline(pts)
    if close:
        wp = wp.close()
    return wp.wires().val()


def _extrude_profile(points, height, *, center=True, z0=None):
    """Extrude a closed 2D profile (XY) by height along +Z. Returns cq.Solid."""
    pts = _ensure_ccw(_profile_points_2d(points))
    face = cq.Face.makeFromWires(cq.Wire.makePolygon([cq.Vector(x, y, 0) for (x, y) in pts], close=True))
    solid = cq.Solid.extrudeLinear(face, cq.Vector(0, 0, float(height)))
    dz = (-height / 2.0 if center else 0.0) if z0 is None else z0
    if dz:
        solid = solid.translate(cq.Vector(0, 0, dz))
    return solid


def _revolve_profile(profile_rz, *, angle=360.0):
    """Revolve a closed (r,z) profile about the Z axis. r=radius>=0, z=height.
    Matches Articraft LatheGeometry (profile point (r,z) -> circle of radius r
    at height z)."""
    pts = [(float(r), float(z)) for (r, z) in profile_rz]
    wire = cq.Wire.makePolygon([cq.Vector(r, 0.0, z) for (r, z) in pts], close=True)
    return cq.Solid.revolve(wire, [], float(angle), cq.Vector(0, 0, 0), cq.Vector(0, 0, 1))


def _loft_rings(rings, *, cap=True):
    """Loft through a list of rings; each ring is a list of 3D points (closed
    loop). Returns cq.Solid (capped) or shell."""
    wires = [cq.Wire.makePolygon([cq.Vector(*p) for p in ring], close=True) for ring in rings]
    return cq.Solid.makeLoft(wires, ruled=False)
