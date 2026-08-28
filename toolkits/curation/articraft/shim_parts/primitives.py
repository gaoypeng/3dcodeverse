from articraft_cq import (cq, math, MeshGeometry, _to_shape, _revolve_profile,
    _extrude_profile, _loft_rings, ValidationError, Box, Cylinder, Sphere)

# ===========================================================================
# @section geom-primitives   (Box/Cylinder/Cone/Sphere/Torus/Capsule/Dome)
#
# Clean-room CadQuery B-rep reimplementation of the Articraft sdk primitive
# MeshGeometry classes. Constructor signatures mirror the real sdk exactly;
# the produced shapes are smooth parametric solids (not triangle meshes).
# ===========================================================================


def _coerce_positive_radii3(value, *, name):
    """Verbatim port of the sdk helper: accept a positive scalar or a positive
    3-sequence and return an (rx, ry, rz) tuple."""
    if isinstance(value, (int, float)):
        radius = float(value)
        if radius <= 0.0:
            raise ValidationError("%s must be positive" % name)
        return (radius, radius, radius)
    if len(value) != 3:
        raise ValidationError("%s must be a positive float or a 3-sequence" % name)
    rx = float(value[0])
    ry = float(value[1])
    rz = float(value[2])
    if rx <= 0.0 or ry <= 0.0 or rz <= 0.0:
        raise ValidationError("%s values must be positive" % name)
    return (rx, ry, rz)


class BoxGeometry(MeshGeometry):
    def __init__(self, size):
        super().__init__()
        x, y, z = (float(size[0]), float(size[1]), float(size[2]))
        solid = cq.Solid.makeBox(x, y, z, cq.Vector(-x / 2.0, -y / 2.0, -z / 2.0))
        self._set(solid)


class CylinderGeometry(MeshGeometry):
    def __init__(self, radius, height, *, radial_segments=24, closed=True):
        super().__init__()
        radius = float(radius)
        height = float(height)
        solid = cq.Solid.makeCylinder(
            radius, height, cq.Vector(0, 0, -height / 2.0), cq.Vector(0, 0, 1)
        )
        self._set(solid)


class ConeGeometry(MeshGeometry):
    def __init__(self, radius, height, *, radial_segments=24, closed=True):
        super().__init__()
        radius = float(radius)
        height = float(height)
        # apex at +height/2 (radius 0), base radius `radius` at -height/2
        solid = cq.Solid.makeCone(
            radius, 0.0, height, cq.Vector(0, 0, -height / 2.0), cq.Vector(0, 0, 1)
        )
        self._set(solid)


class SphereGeometry(MeshGeometry):
    def __init__(self, radius, *, width_segments=24, height_segments=16):
        super().__init__()
        radius = float(radius)
        solid = cq.Solid.makeSphere(
            radius, angleDegrees1=-90, angleDegrees2=90, angleDegrees3=360
        )
        self._set(solid)


class TorusGeometry(MeshGeometry):
    def __init__(self, radius, tube, *, radial_segments=16, tubular_segments=32):
        super().__init__()
        radius = float(radius)
        tube = float(tube)
        # ring radius `radius` in XY plane, tube radius `tube`, centered at origin
        solid = cq.Solid.makeTorus(radius, tube, cq.Vector(0, 0, 0), cq.Vector(0, 0, 1))
        self._set(solid)


class CapsuleGeometry(MeshGeometry):
    def __init__(self, radius, length, *, radial_segments=24, height_segments=8):
        super().__init__()
        radius = float(radius)
        length = float(length)
        if radius <= 0.0:
            raise ValidationError("radius must be positive")
        if length < 0.0:
            raise ValidationError("length must be non-negative")

        sphere = cq.Solid.makeSphere(
            radius, angleDegrees1=-90, angleDegrees2=90, angleDegrees3=360
        )
        if length <= 1e-9:
            self._set(sphere)
            return

        half = length / 2.0
        # cylindrical body of height `length` centered, hemispherical caps at +-half
        body = cq.Solid.makeCylinder(
            radius, length, cq.Vector(0, 0, -half), cq.Vector(0, 0, 1)
        )
        top = sphere.translate(cq.Vector(0, 0, half))
        bottom = sphere.translate(cq.Vector(0, 0, -half))
        solid = body.fuse(top, bottom)
        try:
            solid = solid.clean()
        except Exception:
            pass
        self._set(solid)


class DomeGeometry(MeshGeometry):
    def __init__(self, radius, *, radial_segments=24, height_segments=12, closed=True):
        super().__init__()
        rx, ry, rz = _coerce_positive_radii3(radius, name="radius")
        # upper hemisphere (z>=0) of a unit sphere, scaled to the half-ellipsoid
        hemi = cq.Solid.makeSphere(
            1.0, angleDegrees1=0, angleDegrees2=90, angleDegrees3=360
        )
        m = cq.Matrix([[rx, 0, 0, 0], [0, ry, 0, 0], [0, 0, rz, 0]])
        solid = hemi.transformGeometry(m)
        self._set(solid)
