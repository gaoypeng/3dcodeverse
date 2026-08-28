from articraft_cq import (cq, math, MeshGeometry, _CQMesh, _to_shape,
    mesh_from_geometry, _loft_rings, _profile_points_3d, ValidationError)

from dataclasses import dataclass, field

# ===========================================================================
# @section geom-section-loft   (section_loft / SectionLoftSpec / LoftSection /
# LoftTessellation / resample_side_sections / repair_loft)
#
# Clean-room CadQuery B-rep reimplementation of the Articraft sdk section-loft
# API. Public signatures mirror the real sdk exactly; instead of tessellating
# to a triangle mesh, the loft is returned as a parametric CadQuery B-rep
# wrapped in a MeshGeometry (so `mesh_from_geometry(...)` and the model
# epilogue see an exact shape). The real sdk already lofts with cadquery
# internally (cq.occ_impl.shapes.loft), so this mirrors that construction.
# ===========================================================================


def _cq_shapes():
    """The low-level cadquery shape helpers the real sdk lofts through."""
    from cadquery.occ_impl import shapes as cq_shapes
    return cq_shapes


# ---- validation / coercion helpers (verbatim from the Apache-2.0 sdk) ------
def _as_vec3(values, *, name):
    if len(values) != 3:
        raise ValidationError("%s must have 3 elements" % name)
    return (float(values[0]), float(values[1]), float(values[2]))


def _normalize_section_points(points, *, name):
    raw = [_as_vec3(point, name=name) for point in points]
    if len(raw) < 3:
        raise ValidationError("%s must contain at least 3 points" % name)

    out = []
    for point in raw:
        if not out or point != out[-1]:
            out.append(point)

    if len(out) >= 2 and out[0] == out[-1]:
        out.pop()

    if len(out) < 3:
        raise ValidationError("%s must contain at least 3 distinct points" % name)
    return out


# ===========================================================================
# Spec data types
# ===========================================================================
@dataclass(frozen=True)
class LoftTessellation:
    tolerance: float = 0.001
    angular_tolerance: float = 0.1

    def __post_init__(self):
        if float(self.tolerance) <= 0.0:
            raise ValidationError("tessellation.tolerance must be > 0")
        if float(self.angular_tolerance) <= 0.0:
            raise ValidationError("tessellation.angular_tolerance must be > 0")
        object.__setattr__(self, "tolerance", float(self.tolerance))
        object.__setattr__(self, "angular_tolerance", float(self.angular_tolerance))


@dataclass(frozen=True)
class LoftSection:
    points: tuple

    def __post_init__(self):
        coerced = tuple(_normalize_section_points(self.points, name="section.points"))
        object.__setattr__(self, "points", coerced)


@dataclass(frozen=True)
class SectionLoftSpec:
    sections: tuple
    path: tuple = None
    guide_curves: dict = None
    cap: bool = True
    solid: bool = True
    symmetry: str = None
    ruled: bool = False
    continuity: str = "C2"
    parametrization: str = "uniform"
    degree: int = 3
    compat: bool = True
    smoothing: bool = False
    weights: tuple = (1.0, 1.0, 1.0)
    repair: str = "auto"
    tessellation: LoftTessellation = field(default_factory=LoftTessellation)

    def __post_init__(self):
        sections = tuple(_coerce_loft_section(section) for section in self.sections)
        if len(sections) < 2:
            raise ValidationError("SectionLoftSpec.sections must contain at least two sections")
        object.__setattr__(self, "sections", sections)

        if self.path is not None:
            object.__setattr__(
                self, "path",
                tuple(_as_vec3(point, name="path[]") for point in self.path),
            )
        if self.guide_curves is not None:
            guide_curves = {
                str(name): tuple(_as_vec3(point, name="guide_curves[%r][]" % name) for point in pts)
                for name, pts in self.guide_curves.items()
            }
            object.__setattr__(self, "guide_curves", guide_curves)

        if self.symmetry is not None and self.symmetry != "mirror_yz":
            raise ValidationError("symmetry must be 'mirror_yz' or None")
        if self.degree < 1:
            raise ValidationError("degree must be >= 1")
        if len(self.weights) != 3:
            raise ValidationError("weights must contain exactly 3 values")
        object.__setattr__(self, "degree", int(self.degree))
        object.__setattr__(
            self, "weights",
            (float(self.weights[0]), float(self.weights[1]), float(self.weights[2])),
        )


def _coerce_loft_section(value):
    if isinstance(value, LoftSection):
        return value
    return LoftSection(points=tuple(value))


def _coerce_loft_spec(spec):
    if isinstance(spec, SectionLoftSpec):
        return spec
    return SectionLoftSpec(sections=tuple(_coerce_loft_section(section) for section in spec))


# ===========================================================================
# CadQuery construction (mirrors the real sdk's cadquery internals)
# ===========================================================================
def _build_section_wires(spec):
    cq_shapes = _cq_shapes()
    wires = []
    for idx, section in enumerate(spec.sections):
        points = tuple(section.points)
        try:
            wire = cq_shapes.polygon(*points)
        except Exception as exc:
            raise ValidationError("Failed to build wire for section[%d]" % idx)
        wires.append(wire)
    return wires


def _curve_from_points(points, *, name):
    cq_shapes = _cq_shapes()
    if len(points) < 2:
        raise ValidationError("%s must contain at least 2 points" % name)
    if len(points) == 2:
        return cq_shapes.segment(points[0], points[1])
    try:
        return cq_shapes.spline(*points)
    except Exception:
        try:
            return cq_shapes.polyline(*points)
        except Exception as exc:
            raise ValidationError("Failed to build %s" % name)


def _resolve_path_points(spec):
    if spec.path is not None:
        return spec.path
    if spec.guide_curves is None:
        return None
    return spec.guide_curves.get("spine")


def _resolve_aux_spine_points(spec):
    if spec.guide_curves is None:
        return None
    aux = spec.guide_curves.get("aux_spine")
    if aux is not None:
        return aux
    return spec.guide_curves.get("binormal")


def _validate_guide_curve_names(spec):
    if spec.guide_curves is None:
        return
    supported = {"spine", "aux_spine", "binormal"}
    unsupported = sorted(set(spec.guide_curves) - supported)
    if unsupported:
        raise ValidationError(
            "Unsupported guide_curves keys: "
            + ", ".join(repr(name) for name in unsupported)
            + ". Supported keys are 'spine', 'aux_spine', and 'binormal'."
        )


def _apply_symmetry_shape(shape, *, symmetry):
    if symmetry is None:
        return shape
    if symmetry != "mirror_yz":
        raise ValidationError("Unsupported symmetry mode: %r" % symmetry)
    try:
        mirrored = shape.mirror("YZ")
        if hasattr(shape, "fuse"):
            fused = shape.fuse(mirrored, glue=True)
        else:
            fused = shape
        if hasattr(fused, "fix"):
            fused = fused.fix()
        if hasattr(fused, "clean"):
            fused = fused.clean()
        return fused
    except Exception as exc:
        raise ValidationError("Failed to apply mirror_yz symmetry")


def _heal_shape(shape, *, solid):
    cq_shapes = _cq_shapes()
    if hasattr(shape, "fix"):
        shape = shape.fix()
    if solid:
        try:
            shape = cq_shapes.solid(shape)
        except Exception:
            pass
        if hasattr(shape, "fix"):
            shape = shape.fix()
    return shape


def section_loft(spec, /, **overrides):
    if overrides:
        spec = SectionLoftSpec(**({**_coerce_loft_spec(spec).__dict__, **overrides}))
    else:
        spec = _coerce_loft_spec(spec)

    cq_shapes = _cq_shapes()
    _validate_guide_curve_names(spec)
    wires = _build_section_wires(spec)
    path_points = _resolve_path_points(spec)
    aux_spine_points = _resolve_aux_spine_points(spec)

    if path_points is None:
        shape = cq_shapes.loft(
            wires,
            cap=bool(spec.cap and spec.solid),
            ruled=bool(spec.ruled),
            continuity=spec.continuity,
            parametrization=spec.parametrization,
            degree=int(spec.degree),
            compat=bool(spec.compat),
            smoothing=bool(spec.smoothing),
            weights=spec.weights,
        )
    else:
        path_curve = _curve_from_points(path_points, name="path")
        mode = None
        if aux_spine_points is not None:
            mode = _curve_from_points(aux_spine_points, name="guide_curves['aux_spine']")
        shape = cq_shapes.Solid.sweep_multi(
            wires,
            path_curve,
            makeSolid=bool(spec.solid and spec.cap),
            isFrenet=False,
            mode=mode,
        )

    shape = _apply_symmetry_shape(shape, symmetry=spec.symmetry)

    if spec.repair in {"auto", "kernel"}:
        shape = _heal_shape(shape, solid=bool(spec.solid and spec.cap))

    return MeshGeometry()._set(shape)


def repair_loft(geometry_or_spec, /, *, repair="auto"):
    # B-rep results are already exact/manifold, so mesh-side repair is a no-op
    # copy; spec/raw inputs are rebuilt through section_loft (honoring `repair`).
    if isinstance(geometry_or_spec, MeshGeometry):
        return geometry_or_spec.copy()
    spec = _coerce_loft_spec(geometry_or_spec)
    if repair != "auto":
        spec = SectionLoftSpec(**{**spec.__dict__, "repair": repair})
    return section_loft(spec)


# ===========================================================================
# resample_side_sections  (pure-python helper, verbatim from sdk _mesh/common.py)
# ===========================================================================
def resample_side_sections(sections, *, samples_per_span=2, smooth_passes=0,
                           min_height=1e-4, min_width=1e-4):
    """Densify and optionally smooth side-loft sections.

    Each section is ``(y, z_min, z_max, width)`` with the loft axis along +Y.
    Linearly resamples each span in Y, then applies a light 3-point smoothing
    kernel to ``z_min``, ``z_max`` and ``width`` while keeping ``y`` fixed and
    monotonic."""
    raw = [(float(y), float(z0), float(z1), float(w)) for (y, z0, z1, w) in sections]
    if len(raw) < 2:
        raise ValueError("resample_side_sections requires at least two sections")

    raw.sort(key=lambda row: row[0])

    # Merge duplicate y samples by averaging rails.
    merged = []
    for row in raw:
        if merged and abs(row[0] - merged[-1][0]) <= 1e-9:
            y0, a0, a1, aw = merged[-1]
            merged[-1] = (
                y0,
                0.5 * (a0 + row[1]),
                0.5 * (a1 + row[2]),
                0.5 * (aw + row[3]),
            )
        else:
            merged.append(row)

    if len(merged) < 2:
        y0, z0, z1, w = merged[0]
        height = max(float(min_height), z1 - z0)
        return [(y0, z0, z0 + height, max(float(min_width), w))]

    spans = max(1, int(samples_per_span))

    def lerp(a, b, t):
        return a + (b - a) * t

    dense = []
    for idx in range(len(merged) - 1):
        a = merged[idx]
        b = merged[idx + 1]
        for j in range(spans):
            if idx > 0 and j == 0:
                continue
            t = float(j) / float(spans)
            dense.append(
                (
                    lerp(a[0], b[0], t),
                    lerp(a[1], b[1], t),
                    lerp(a[2], b[2], t),
                    lerp(a[3], b[3], t),
                )
            )
    dense.append(merged[-1])

    passes = max(0, int(smooth_passes))
    if passes > 0 and len(dense) >= 3:
        for _ in range(passes):
            smoothed = [dense[0]]
            for idx in range(1, len(dense) - 1):
                left = dense[idx - 1]
                cur = dense[idx]
                right = dense[idx + 1]
                smoothed.append(
                    (
                        cur[0],
                        0.25 * left[1] + 0.50 * cur[1] + 0.25 * right[1],
                        0.25 * left[2] + 0.50 * cur[2] + 0.25 * right[2],
                        0.25 * left[3] + 0.50 * cur[3] + 0.25 * right[3],
                    )
                )
            smoothed.append(dense[-1])
            dense = smoothed

    min_h = max(1e-9, float(min_height))
    min_w = max(1e-9, float(min_width))
    out = []
    for y, z0, z1, w in dense:
        low = float(min(z0, z1))
        high = float(max(z0, z1))
        if high - low < min_h:
            center = 0.5 * (low + high)
            low = center - 0.5 * min_h
            high = center + 0.5 * min_h
        out.append((float(y), low, high, max(min_w, float(w))))
    return out
