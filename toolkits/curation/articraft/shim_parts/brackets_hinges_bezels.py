from __future__ import annotations

from articraft_cq import (cq, math, MeshGeometry, _CQMesh, _to_shape, mesh_from_geometry,
    _revolve_profile, _extrude_profile, _loft_rings, rounded_rect_profile,
    _profile_points_2d, _ensure_ccw, boolean_difference, boolean_union, boolean_intersection,
    Box, Cylinder, Sphere, ValidationError,
    superellipse_profile, Origin, Part, Visual)

# ===========================================================================
# @section brackets_hinges_bezels
#
# Clean-room CadQuery B-rep reimplementation of the Articraft sdk
# brackets / yokes / forks / bezels / piano-hinge family plus the placement
# helpers (place_on_*, surface_frame, wrap_*, cut_opening_on_face, ...).
# Constructor signatures mirror the real sdk EXACTLY; the produced shapes are
# parametric solids (the sdk builds these with cadquery then tessellates --
# here we keep the B-rep). Placement helpers reproduce the sdk's analytic
# geometry math so model scripts get identical Origins / frames.
# ===========================================================================
from contextlib import suppress
from dataclasses import dataclass
from typing import Iterable, Literal, Optional, Sequence, Tuple, Union

_EPS = 1e-9


# ---------------------------------------------------------------------------
# shared finalize helpers (center=False -> rest the part's min-z on z=0)
# ---------------------------------------------------------------------------
def _brackets_hinges_bezels_shift_to_z0(shape):
    bb = shape.BoundingBox()
    return shape.translate(cq.Vector(0.0, 0.0, -bb.zmin))


def _brackets_hinges_bezels_finish(target, model, center):
    shape = _to_shape(model)
    if not center:
        shape = _brackets_hinges_bezels_shift_to_z0(shape)
    target._set(shape)


# ===========================================================================
# @section brackets  (ClevisBracket / PivotFork / TrunnionYoke)
# ===========================================================================
class ClevisBracketGeometry(MeshGeometry):
    """Build a U-shaped clevis bracket with a bottom base and a transverse pin bore."""

    def __init__(
        self,
        overall_size: Sequence[float],
        *,
        gap_width: float,
        bore_diameter: float,
        bore_center_z: float,
        base_thickness: float,
        corner_radius: float = 0.0,
        center: bool = True,
    ):
        super().__init__()
        width = float(overall_size[0])
        depth = float(overall_size[1])
        height = float(overall_size[2])
        gap_width = float(gap_width)
        bore_diameter = float(bore_diameter)
        bore_center_z = float(bore_center_z)
        base_thickness = float(base_thickness)
        corner_radius = max(0.0, float(corner_radius))

        if width <= 0.0 or depth <= 0.0 or height <= 0.0:
            raise ValueError("overall_size values must be positive")
        if gap_width <= 0.0 or gap_width >= width:
            raise ValueError("gap_width must be positive and less than overall_size[0]")
        cheek_thickness = 0.5 * (width - gap_width)
        if cheek_thickness <= 1e-6:
            raise ValueError("gap_width leaves no side wall material")
        if bore_diameter <= 0.0 or bore_diameter >= min(cheek_thickness * 2.0, depth, height):
            raise ValueError("bore_diameter is too large for the clevis envelope")
        if base_thickness <= 0.0 or base_thickness >= height:
            raise ValueError("base_thickness must be positive and less than overall_size[2]")
        bore_radius = bore_diameter * 0.5
        if bore_center_z - bore_radius <= base_thickness or bore_center_z + bore_radius >= height:
            raise ValueError(
                "bore_center_z must leave material above the base and below the top edge"
            )

        shape = cq.Workplane("XY").box(width, depth, height)
        slot_cut = (
            cq.Workplane("XY")
            .box(gap_width, depth + 0.004, height - base_thickness)
            .translate((0.0, 0.0, base_thickness * 0.5))
        )
        shape = shape.cut(slot_cut)
        if corner_radius > 0.0:
            shape = shape.edges("|Z").fillet(
                min(corner_radius, cheek_thickness * 0.6, depth * 0.25, height * 0.25)
            )

        bore_z = -height * 0.5 + bore_center_z
        bore = (
            cq.Workplane("YZ")
            .circle(bore_radius)
            .extrude(width + 0.01, both=True)
            .translate((0.0, 0.0, bore_z))
        )
        shape = shape.cut(bore)

        _brackets_hinges_bezels_finish(self, shape, center)


class PivotForkGeometry(MeshGeometry):
    """Build an open-front pivot fork with a rear bridge and a transverse pin bore."""

    def __init__(
        self,
        overall_size: Sequence[float],
        *,
        gap_width: float,
        bore_diameter: float,
        bore_center_z: float,
        bridge_thickness: float,
        corner_radius: float = 0.0,
        center: bool = True,
    ):
        super().__init__()
        width = float(overall_size[0])
        depth = float(overall_size[1])
        height = float(overall_size[2])
        gap_width = float(gap_width)
        bore_diameter = float(bore_diameter)
        bore_center_z = float(bore_center_z)
        bridge_thickness = float(bridge_thickness)
        corner_radius = max(0.0, float(corner_radius))

        if width <= 0.0 or depth <= 0.0 or height <= 0.0:
            raise ValueError("overall_size values must be positive")
        if gap_width <= 0.0 or gap_width >= width:
            raise ValueError("gap_width must be positive and less than overall_size[0]")
        cheek_thickness = 0.5 * (width - gap_width)
        if cheek_thickness <= 1e-6:
            raise ValueError("gap_width leaves no side wall material")
        if bridge_thickness <= 0.0 or bridge_thickness >= depth:
            raise ValueError("bridge_thickness must be positive and less than overall_size[1]")
        if bore_diameter <= 0.0 or bore_diameter >= min(cheek_thickness * 2.0, depth, height):
            raise ValueError("bore_diameter is too large for the pivot fork envelope")
        bore_radius = bore_diameter * 0.5
        if bore_center_z - bore_radius <= 0.0 or bore_center_z + bore_radius >= height:
            raise ValueError("bore_center_z must keep the bore inside the fork cheeks")

        tine_depth = depth
        left_tine = (
            cq.Workplane("XY")
            .box(cheek_thickness, tine_depth, height)
            .translate((-(gap_width * 0.5 + cheek_thickness * 0.5), 0.0, 0.0))
        )
        right_tine = (
            cq.Workplane("XY")
            .box(cheek_thickness, tine_depth, height)
            .translate(((gap_width * 0.5 + cheek_thickness * 0.5), 0.0, 0.0))
        )
        rear_bridge = (
            cq.Workplane("XY")
            .box(width, bridge_thickness, height)
            .translate((0.0, -depth * 0.5 + bridge_thickness * 0.5, 0.0))
        )
        shape = left_tine.union(right_tine).union(rear_bridge)
        if corner_radius > 0.0:
            shape = shape.edges("|Z").fillet(
                min(corner_radius, cheek_thickness * 0.6, bridge_thickness * 0.6, height * 0.25)
            )

        bore_z = -height * 0.5 + bore_center_z
        bore = (
            cq.Workplane("YZ")
            .circle(bore_radius)
            .extrude(width + 0.01, both=True)
            .translate((0.0, 0.0, bore_z))
        )
        shape = shape.cut(bore)

        _brackets_hinges_bezels_finish(self, shape, center)


class TrunnionYokeGeometry(MeshGeometry):
    """Build a trunnion support yoke with a bottom base and cheek-mounted trunnion bores."""

    def __init__(
        self,
        overall_size: Sequence[float],
        *,
        span_width: float,
        trunnion_diameter: float,
        trunnion_center_z: float,
        base_thickness: float,
        corner_radius: float = 0.0,
        center: bool = True,
    ):
        super().__init__()
        width = float(overall_size[0])
        depth = float(overall_size[1])
        height = float(overall_size[2])
        span_width = float(span_width)
        trunnion_diameter = float(trunnion_diameter)
        trunnion_center_z = float(trunnion_center_z)
        base_thickness = float(base_thickness)
        corner_radius = max(0.0, float(corner_radius))

        if width <= 0.0 or depth <= 0.0 or height <= 0.0:
            raise ValueError("overall_size values must be positive")
        if span_width <= 0.0 or span_width >= width:
            raise ValueError("span_width must be positive and less than overall_size[0]")
        cheek_thickness = 0.5 * (width - span_width)
        if cheek_thickness <= 1e-6:
            raise ValueError("span_width leaves no side wall material")
        if base_thickness <= 0.0 or base_thickness >= height:
            raise ValueError("base_thickness must be positive and less than overall_size[2]")
        if trunnion_diameter <= 0.0 or trunnion_diameter >= min(
            cheek_thickness * 2.0, depth, height
        ):
            raise ValueError("trunnion_diameter is too large for the yoke envelope")
        trunnion_radius = trunnion_diameter * 0.5
        if (
            trunnion_center_z - trunnion_radius <= base_thickness
            or trunnion_center_z + trunnion_radius >= height
        ):
            raise ValueError(
                "trunnion_center_z must leave material above the base and below the top edge"
            )

        base = (
            cq.Workplane("XY")
            .box(width, depth, base_thickness)
            .translate((0.0, 0.0, -height * 0.5 + base_thickness * 0.5))
        )
        cheek_height = height - base_thickness
        cheek_z = -height * 0.5 + base_thickness + cheek_height * 0.5
        left_cheek = (
            cq.Workplane("XY")
            .box(cheek_thickness, depth, cheek_height)
            .translate((-(span_width * 0.5 + cheek_thickness * 0.5), 0.0, cheek_z))
        )
        right_cheek = (
            cq.Workplane("XY")
            .box(cheek_thickness, depth, cheek_height)
            .translate(((span_width * 0.5 + cheek_thickness * 0.5), 0.0, cheek_z))
        )
        boss_radius = max(trunnion_radius * 1.4, cheek_thickness * 0.55)
        boss_length = min(cheek_thickness * 0.75, depth * 0.35)
        boss_z = -height * 0.5 + trunnion_center_z
        left_boss = (
            cq.Workplane("YZ")
            .circle(boss_radius)
            .extrude(boss_length, both=False)
            .translate((-(span_width * 0.5 + cheek_thickness), 0.0, boss_z))
        )
        right_boss = (
            cq.Workplane("YZ")
            .circle(boss_radius)
            .extrude(-boss_length, both=False)
            .translate(((span_width * 0.5 + cheek_thickness), 0.0, boss_z))
        )
        shape = base.union(left_cheek).union(right_cheek).union(left_boss).union(right_boss)
        if corner_radius > 0.0:
            shape = shape.edges("|Z").fillet(
                min(corner_radius, cheek_thickness * 0.5, depth * 0.2, height * 0.2)
            )

        trunnion_bore = (
            cq.Workplane("YZ")
            .circle(trunnion_radius)
            .extrude(
                width + boss_length * 2.0 + 0.01,
                both=True,
            )
            .translate((0.0, 0.0, boss_z))
        )
        shape = shape.cut(trunnion_bore)

        _brackets_hinges_bezels_finish(self, shape, center)


# ===========================================================================
# @section bezels  (BezelGeometry + sub-feature specs)
# ===========================================================================
Vec2 = Tuple[float, float]


@dataclass(frozen=True)
class BezelFace:
    style: Literal["flat", "rounded", "chamfered", "radiused_step"] = "flat"
    front_lip: float = 0.0
    chamfer: float = 0.0
    fillet: float = 0.0


@dataclass(frozen=True)
class BezelRecess:
    depth: float
    inset: float
    floor_radius: float = 0.0
    wall_draft_deg: float = 0.0


@dataclass(frozen=True)
class BezelVisor:
    top_extension: float = 0.0
    side_extension: float = 0.0
    thickness: float = 0.0
    draft_deg: float = 0.0


@dataclass(frozen=True)
class BezelFlange:
    width: float = 0.0
    thickness: float = 0.0
    offset: float = 0.0


@dataclass(frozen=True)
class BezelMounts:
    style: Literal["none", "bosses", "tabs", "rear_flange"] = "none"
    hole_count: int = 0
    hole_diameter: Optional[float] = None
    boss_diameter: Optional[float] = None
    setback: float = 0.0


@dataclass(frozen=True)
class BezelCutout:
    edge: Literal["top", "bottom", "left", "right"]
    width: float
    depth: float
    offset: float = 0.0


@dataclass(frozen=True)
class BezelEdgeFeature:
    style: Literal["bead", "step", "notch"] = "bead"
    edge: Literal["top", "bottom", "left", "right"] = "top"
    size: float = 0.0
    offset: float = 0.0
    extent: float = 0.0


# ---- profile helpers copied from the sdk cadquery_helpers --------------------
def _sample_ellipse_profile(width: float, height: float, *, segments: int = 64):
    rx = float(width) * 0.5
    ry = float(height) * 0.5
    return [
        (rx * math.cos(2.0 * math.pi * index / float(segments)),
         ry * math.sin(2.0 * math.pi * index / float(segments)))
        for index in range(segments)
    ]


def _sample_rect_profile(width: float, height: float):
    hw = float(width) * 0.5
    hh = float(height) * 0.5
    return [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]


def _shape_profile_points_2d(shape, size, *, corner_radius: float = 0.0, segments: int = 64):
    width = float(size[0])
    height = float(size[1])
    if width <= 0.0 or height <= 0.0:
        raise ValueError("shape size values must be positive")
    if shape == "rect":
        return _sample_rect_profile(width, height)
    if shape == "rounded_rect":
        return rounded_rect_profile(
            width, height, max(0.0, corner_radius), corner_segments=max(4, segments // 16)
        )
    if shape == "circle":
        diameter = min(width, height)
        return _sample_ellipse_profile(diameter, diameter, segments=segments)
    if shape == "ellipse":
        return _sample_ellipse_profile(width, height, segments=segments)
    if shape == "superellipse":
        return superellipse_profile(width, height, exponent=2.8, segments=segments)
    raise ValueError(f"Unsupported shape {shape!r}")


def _shape_size_from_wall(inner_size, wall):
    inner_w = float(inner_size[0])
    inner_h = float(inner_size[1])
    if isinstance(wall, (int, float)):
        wall_left = wall_right = wall_bottom = wall_top = float(wall)
    else:
        if len(wall) != 4:
            raise ValueError("wall must be a float or a 4-sequence")
        wall_left, wall_right, wall_bottom, wall_top = (float(value) for value in wall)
    if min(wall_left, wall_right, wall_bottom, wall_top) < 0.0:
        raise ValueError("wall values must be non-negative")
    return (inner_w + wall_left + wall_right, inner_h + wall_bottom + wall_top)


def _cq_polyline_wire(cq_module, points, plane: str = "XY"):
    return cq_module.Workplane(plane).polyline(points).close()


def _cq_ring_solid(cq_module, outer_points, inner_points, depth: float, *, center: bool = True):
    shape = _cq_polyline_wire(cq_module, outer_points).extrude(float(depth) * 0.5, both=center)
    inner_cut = _cq_polyline_wire(cq_module, inner_points).extrude(
        float(depth) + max(0.002, float(depth) * 0.5),
        both=center,
    )
    return shape.cut(inner_cut)


def _brackets_hinges_bezels_centered_pattern_positions(count: int, spacing: float):
    if count <= 0:
        return []
    if count == 1:
        return [0.0]
    origin = -0.5 * float(spacing) * float(count - 1)
    return [origin + float(index) * float(spacing) for index in range(count)]


class BezelGeometry(MeshGeometry):
    """Build a framed opening with optional recess, visor, flange, and rear mounts."""

    def __init__(
        self,
        opening_size: Sequence[float],
        outer_size: Sequence[float],
        depth: float,
        *,
        opening_shape: Literal[
            "rect", "rounded_rect", "circle", "ellipse", "superellipse"
        ] = "rounded_rect",
        outer_shape: Literal[
            "rect", "rounded_rect", "circle", "ellipse", "superellipse"
        ] = "rounded_rect",
        opening_corner_radius: float = 0.0,
        outer_corner_radius: float = 0.0,
        wall: Union[float, tuple[float, float, float, float], None] = None,
        face: Optional[BezelFace] = None,
        recess: Optional[BezelRecess] = None,
        visor: Optional[BezelVisor] = None,
        flange: Optional[BezelFlange] = None,
        mounts: Optional[BezelMounts] = None,
        cutouts: Sequence[BezelCutout] = (),
        edge_features: Sequence[BezelEdgeFeature] = (),
        center: bool = True,
    ):
        super().__init__()
        opening_w = float(opening_size[0])
        opening_h = float(opening_size[1])
        outer_w = float(outer_size[0])
        outer_h = float(outer_size[1])
        depth = float(depth)
        opening_corner_radius = max(0.0, float(opening_corner_radius))
        outer_corner_radius = max(0.0, float(outer_corner_radius))
        face = face or BezelFace()
        visor = visor or BezelVisor()
        flange = flange or BezelFlange()
        mounts = mounts or BezelMounts()

        if min(opening_w, opening_h, outer_w, outer_h, depth) <= 0.0:
            raise ValueError("opening_size, outer_size, and depth must be positive")
        if opening_w >= outer_w or opening_h >= outer_h:
            raise ValueError("opening_size must be smaller than outer_size on both axes")
        if recess is not None and (recess.depth <= 0.0 or recess.inset < 0.0):
            raise ValueError("BezelRecess depth must be positive and inset must be non-negative")

        outer_points = _shape_profile_points_2d(
            outer_shape, (outer_w, outer_h), corner_radius=outer_corner_radius
        )
        opening_points = _shape_profile_points_2d(
            opening_shape, (opening_w, opening_h), corner_radius=opening_corner_radius
        )
        shape = _cq_ring_solid(cq, outer_points, opening_points, depth, center=True)

        if face.style in {"rounded", "radiused_step"} and face.fillet > 1e-6:
            with suppress(Exception):
                shape = shape.edges("|Z").fillet(
                    min(face.fillet, depth * 0.25, min(outer_w, outer_h) * 0.1)
                )
        if face.style == "chamfered" and face.chamfer > 1e-6:
            with suppress(Exception):
                shape = shape.edges("|Z").chamfer(
                    min(face.chamfer, depth * 0.25, min(outer_w, outer_h) * 0.1)
                )

        derived_wall = (
            (outer_w - opening_w) * 0.5,
            (outer_w - opening_w) * 0.5,
            (outer_h - opening_h) * 0.5,
            (outer_h - opening_h) * 0.5,
        )
        if face.front_lip > 1e-6 or face.style == "radiused_step":
            lip_thickness = max(face.front_lip, depth * 0.08, 0.0015)
            if wall is not None:
                lip_size = _shape_size_from_wall(opening_size, wall)
            else:
                lip_size = (
                    min(
                        opening_w + max(face.front_lip * 2.0, derived_wall[0] * 1.05),
                        outer_w - 0.001,
                    ),
                    min(
                        opening_h + max(face.front_lip * 2.0, derived_wall[2] * 1.05),
                        outer_h - 0.001,
                    ),
                )
            if lip_size[0] < outer_w and lip_size[1] < outer_h:
                lip_outer = _shape_profile_points_2d(
                    opening_shape if outer_shape != "circle" else outer_shape,
                    lip_size,
                    corner_radius=min(
                        opening_corner_radius + face.front_lip,
                        lip_size[0] * 0.25,
                        lip_size[1] * 0.25,
                    ),
                )
                lip = _cq_ring_solid(
                    cq, lip_outer, opening_points, lip_thickness, center=True
                ).translate((0.0, 0.0, depth * 0.5))
                shape = shape.union(lip)

        if recess is not None:
            requested_recess_size = (opening_w + recess.inset * 2.0, opening_h + recess.inset * 2.0)
            if wall is None and (
                requested_recess_size[0] >= outer_w or requested_recess_size[1] >= outer_h
            ):
                raise ValueError("recess wall leaves no outer frame material")
            recess_size = (
                _shape_size_from_wall(opening_size, wall)
                if wall is not None
                else (
                    min(requested_recess_size[0], outer_w - 0.001),
                    min(requested_recess_size[1], outer_h - 0.001),
                )
            )
            if recess_size[0] >= outer_w or recess_size[1] >= outer_h:
                raise ValueError("recess wall leaves no outer frame material")
            recess_points = _shape_profile_points_2d(
                opening_shape if outer_shape != "circle" else outer_shape,
                recess_size,
                corner_radius=min(
                    opening_corner_radius + recess.inset,
                    recess_size[0] * 0.25,
                    recess_size[1] * 0.25,
                ),
            )
            recess_cut = _cq_ring_solid(
                cq, recess_points, opening_points, recess.depth, center=True
            ).translate((0.0, 0.0, depth * 0.5 - recess.depth * 0.5))
            shape = shape.cut(recess_cut)

        if visor.thickness > 1e-6 and (visor.top_extension > 1e-6 or visor.side_extension > 1e-6):
            top_visor = (
                cq.Workplane("XY")
                .box(
                    outer_w + visor.side_extension * 2.0,
                    max(visor.top_extension, visor.thickness),
                    visor.thickness,
                )
                .translate((0.0, outer_h * 0.5 + visor.top_extension * 0.5, depth * 0.5))
            )
            shape = shape.union(top_visor)
            if visor.side_extension > 1e-6:
                cheek_y = max(visor.top_extension, outer_h * 0.5) * 0.5
                cheek = cq.Workplane("XY").box(
                    visor.side_extension,
                    max(visor.top_extension, outer_h * 0.45),
                    visor.thickness,
                )
                shape = shape.union(
                    cheek.translate(
                        (outer_w * 0.5 + visor.side_extension * 0.5, cheek_y, depth * 0.5)
                    )
                )
                shape = shape.union(
                    cheek.translate(
                        (-(outer_w * 0.5 + visor.side_extension * 0.5), cheek_y, depth * 0.5)
                    )
                )

        if flange.width > 1e-6 and flange.thickness > 1e-6:
            flange_outer = _shape_profile_points_2d(
                outer_shape,
                (outer_w + flange.width * 2.0, outer_h + flange.width * 2.0),
                corner_radius=outer_corner_radius + flange.width,
            )
            flange_shape = _cq_ring_solid(
                cq, flange_outer, outer_points, flange.thickness, center=True
            ).translate((0.0, 0.0, -depth * 0.5 - flange.offset))
            shape = shape.union(flange_shape)

        if mounts.style == "bosses" and mounts.hole_count > 0:
            boss_radius = (
                max(float(mounts.boss_diameter) * 0.5, 0.0015)
                if mounts.boss_diameter is not None
                else max(min(outer_w, outer_h) * 0.05, 0.003)
            )
            hole_radius = (
                max(float(mounts.hole_diameter) * 0.5, 0.0008)
                if mounts.hole_diameter is not None
                else boss_radius * 0.38
            )
            boss_thickness = max(depth * 0.18, boss_radius * 0.8)
            margin_x = outer_w * 0.5 - boss_radius - max(mounts.setback, 0.001)
            margin_y = outer_h * 0.5 - boss_radius - max(mounts.setback, 0.001)
            boss_points = [
                (-margin_x, -margin_y),
                (margin_x, -margin_y),
                (margin_x, margin_y),
                (-margin_x, margin_y),
            ][: mounts.hole_count]
            for bx, by in boss_points:
                boss = (
                    cq.Workplane("XY")
                    .circle(boss_radius)
                    .extrude(boss_thickness)
                    .translate((bx, by, -depth * 0.5 - boss_thickness))
                )
                hole = (
                    cq.Workplane("XY")
                    .circle(hole_radius)
                    .extrude(boss_thickness + depth + 0.01)
                    .translate((bx, by, -depth * 0.5 - boss_thickness))
                )
                shape = shape.union(boss).cut(hole)
        elif mounts.style == "tabs" and mounts.hole_count > 0:
            tab_width = max(outer_w * 0.16, 0.008)
            tab_depth = max(depth * 0.14, 0.002)
            hole_radius = (
                max(float(mounts.hole_diameter) * 0.5, 0.0008)
                if mounts.hole_diameter is not None
                else tab_width * 0.14
            )
            tab_positions = _brackets_hinges_bezels_centered_pattern_positions(
                mounts.hole_count, outer_w / max(mounts.hole_count, 1)
            )
            for px in tab_positions:
                tab = (
                    cq.Workplane("XY")
                    .box(tab_width, tab_width * 0.6, tab_depth)
                    .translate(
                        (px, -(outer_h * 0.5 + tab_width * 0.3), -depth * 0.5 - tab_depth * 0.5)
                    )
                )
                hole = (
                    cq.Workplane("XY")
                    .circle(hole_radius)
                    .extrude(tab_depth + depth + 0.01)
                    .translate((px, -(outer_h * 0.5 + tab_width * 0.3), -depth * 0.5 - tab_depth))
                )
                shape = shape.union(tab).cut(hole)
        elif (
            mounts.style == "rear_flange"
            and mounts.hole_count > 0
            and mounts.hole_diameter is not None
        ):
            flange_width = max(max(mounts.setback, 0.003), min(outer_w, outer_h) * 0.06)
            rear_flange_outer = _shape_profile_points_2d(
                outer_shape,
                (outer_w + flange_width * 2.0, outer_h + flange_width * 2.0),
                corner_radius=outer_corner_radius + flange_width,
            )
            rear_flange = _cq_ring_solid(
                cq, rear_flange_outer, outer_points, max(depth * 0.12, 0.002), center=True
            ).translate((0.0, 0.0, -depth * 0.5 - max(depth * 0.12, 0.002)))
            shape = shape.union(rear_flange)

        for cutout in cutouts:
            if cutout.width <= 0.0 or cutout.depth <= 0.0:
                raise ValueError("BezelCutout width and depth must be positive")
            cut_height = cutout.width
            cut_depth = cutout.depth
            if cutout.edge in {"top", "bottom"}:
                cutter = cq.Workplane("XY").box(
                    cutout.width, cut_depth, depth + visor.thickness + 0.02
                )
                y = (
                    outer_h * 0.5 - cut_depth * 0.5
                    if cutout.edge == "top"
                    else -outer_h * 0.5 + cut_depth * 0.5
                )
                cutter = cutter.translate((cutout.offset, y, 0.0))
            else:
                cutter = cq.Workplane("XY").box(
                    cut_depth, cut_height, depth + visor.thickness + 0.02
                )
                x = (
                    outer_w * 0.5 - cut_depth * 0.5
                    if cutout.edge == "right"
                    else -outer_w * 0.5 + cut_depth * 0.5
                )
                cutter = cutter.translate((x, cutout.offset, 0.0))
            shape = shape.cut(cutter)

        for feature in edge_features:
            if feature.size <= 0.0:
                continue
            extent = (
                feature.extent
                if feature.extent > 0.0
                else (outer_w if feature.edge in {"top", "bottom"} else outer_h)
            )
            if feature.style == "notch":
                if feature.edge in {"top", "bottom"}:
                    cutter = cq.Workplane("XY").box(
                        extent, feature.size, depth + visor.thickness + 0.02
                    )
                    y = (
                        outer_h * 0.5 - feature.size * 0.5
                        if feature.edge == "top"
                        else -outer_h * 0.5 + feature.size * 0.5
                    )
                    cutter = cutter.translate((feature.offset, y, 0.0))
                else:
                    cutter = cq.Workplane("XY").box(
                        feature.size, extent, depth + visor.thickness + 0.02
                    )
                    x = (
                        outer_w * 0.5 - feature.size * 0.5
                        if feature.edge == "right"
                        else -outer_w * 0.5 + feature.size * 0.5
                    )
                    cutter = cutter.translate((x, feature.offset, 0.0))
                shape = shape.cut(cutter)
                continue
            if feature.edge in {"top", "bottom"}:
                solid = cq.Workplane("XY").box(extent, feature.size, max(depth * 0.10, 0.0015))
                y = (
                    outer_h * 0.5 + feature.size * 0.5
                    if feature.edge == "top"
                    else -(outer_h * 0.5 + feature.size * 0.5)
                )
                solid = solid.translate((feature.offset, y, depth * 0.5))
            else:
                solid = cq.Workplane("XY").box(feature.size, extent, max(depth * 0.10, 0.0015))
                x = (
                    outer_w * 0.5 + feature.size * 0.5
                    if feature.edge == "right"
                    else -(outer_w * 0.5 + feature.size * 0.5)
                )
                solid = solid.translate((x, feature.offset, depth * 0.5))
            shape = (
                shape.union(solid)
                if feature.style == "bead"
                else shape.cut(solid.translate((0.0, 0.0, -max(depth * 0.04, 0.0008))))
            )

        _brackets_hinges_bezels_finish(self, shape, center)


# ===========================================================================
# @section hinges  (PianoHingeGeometry + HingeHolePattern / HingePinStyle specs)
# ===========================================================================
@dataclass(frozen=True)
class HingeHolePattern:
    style: Literal["none", "round", "countersunk", "slotted"] = "none"
    count: int = 0
    diameter: Optional[float] = None
    slot_size: Optional[tuple[float, float]] = None
    edge_margin: float = 0.0
    pitch: Optional[float] = None


@dataclass(frozen=True)
class HingePinStyle:
    head_style: Literal["plain", "button", "flat", "peened"] = "plain"
    head_height: float = 0.0
    head_diameter: Optional[float] = None
    exposed_end: float = 0.0


def _brackets_hinges_bezels_rounded_slot_profile(length: float, width: float, *, segments: int = 10):
    radius = width * 0.5
    straight = max(length - width, 0.0)
    half = straight * 0.5
    points = []
    for index in range(segments + 1):
        theta = -math.pi * 0.5 + math.pi * (index / float(segments))
        points.append((half + radius * math.cos(theta), radius * math.sin(theta)))
    for index in range(segments + 1):
        theta = math.pi * 0.5 + math.pi * (index / float(segments))
        points.append((-half + radius * math.cos(theta), radius * math.sin(theta)))
    return points


class _BarrelHingeGeometry(MeshGeometry):
    """Two-leaf knuckle hinge around a local Z pin axis (sdk BarrelHingeGeometry).

    Kept private; PianoHingeGeometry builds on it (matches the sdk module
    structure where PianoHinge delegates to BarrelHinge)."""

    def __init__(
        self,
        length: float,
        *,
        leaf_width_a: float,
        leaf_width_b: Optional[float] = None,
        leaf_thickness: float,
        pin_diameter: float,
        knuckle_outer_diameter: Optional[float] = None,
        knuckle_count: int = 5,
        clearance: float = 0.0005,
        open_angle_deg: float = 180.0,
        holes_a: Optional[HingeHolePattern] = None,
        holes_b: Optional[HingeHolePattern] = None,
        pin: Optional[HingePinStyle] = None,
        center: bool = True,
    ):
        super().__init__()
        length = float(length)
        leaf_width_a = float(leaf_width_a)
        leaf_width_b = float(leaf_width_b) if leaf_width_b is not None else leaf_width_a
        leaf_thickness = float(leaf_thickness)
        pin_diameter = float(pin_diameter)
        knuckle_outer_diameter = (
            float(knuckle_outer_diameter)
            if knuckle_outer_diameter is not None
            else pin_diameter * 1.75
        )
        clearance = float(clearance)
        open_angle_deg = float(open_angle_deg)
        holes_a = holes_a or HingeHolePattern()
        holes_b = holes_b or HingeHolePattern()
        pin = pin or HingePinStyle()

        if (
            min(length, leaf_width_a, leaf_width_b, leaf_thickness, pin_diameter,
                knuckle_outer_diameter)
            <= 0.0
        ):
            raise ValueError(
                "length, leaf widths, thickness, pin_diameter, and knuckle size must be positive"
            )
        if knuckle_count < 3:
            raise ValueError("knuckle_count must be at least 3")
        if pin_diameter >= knuckle_outer_diameter:
            raise ValueError("pin_diameter must be less than knuckle_outer_diameter")
        segment_length = (length - clearance * float(knuckle_count - 1)) / float(knuckle_count)
        if segment_length <= 0.0:
            raise ValueError("clearance/knuckle_count leave no knuckle length")

        leaf_overlap = min(leaf_thickness * 0.75, knuckle_outer_diameter * 0.12)
        leaf_a = (
            cq.Workplane("XY")
            .box(leaf_width_a, leaf_thickness, length)
            .translate(
                (-(knuckle_outer_diameter * 0.5 + leaf_width_a * 0.5 - leaf_overlap), 0.0, 0.0)
            )
        )
        leaf_b = (
            cq.Workplane("XY")
            .box(leaf_width_b, leaf_thickness, length)
            .translate(
                ((knuckle_outer_diameter * 0.5 + leaf_width_b * 0.5 - leaf_overlap), 0.0, 0.0)
            )
        )
        leaf_b = leaf_b.rotate((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 180.0 - open_angle_deg)
        shape = leaf_a.union(leaf_b)

        z_start = -length * 0.5
        for index in range(knuckle_count):
            center_z = z_start + segment_length * 0.5 + index * (segment_length + clearance)
            knuckle = (
                cq.Workplane("XY")
                .circle(knuckle_outer_diameter * 0.5)
                .extrude(segment_length)
                .translate((0.0, 0.0, center_z - segment_length * 0.5))
            )
            shape = shape.union(knuckle)

        pin_len = length + pin.exposed_end * 2.0
        pin_bottom = -length * 0.5 - pin.exposed_end
        pin_shape = (
            cq.Workplane("XY")
            .circle(pin_diameter * 0.5)
            .extrude(pin_len)
            .translate((0.0, 0.0, pin_bottom))
        )
        if pin.head_style != "plain" and pin.head_height > 1e-6:
            head_d = pin.head_diameter or pin_diameter * 1.6
            head = cq.Workplane("XY").circle(head_d * 0.5).extrude(pin.head_height)
            pin_shape = pin_shape.union(head.translate((0.0, 0.0, pin_bottom - pin.head_height)))
            if pin.head_style != "peened":
                pin_shape = pin_shape.union(head.translate((0.0, 0.0, pin_bottom + pin_len)))
        shape = shape.union(pin_shape)

        def _apply_holes(base_shape, pattern, side):
            if pattern.style == "none" or pattern.count <= 0:
                return base_shape
            if pattern.diameter is None and pattern.slot_size is None:
                raise ValueError("HingeHolePattern requires diameter or slot_size")
            count = pattern.count
            z_positions = _brackets_hinges_bezels_centered_pattern_positions(
                count,
                pattern.pitch
                if pattern.pitch is not None
                else (length - pattern.edge_margin * 2.0) / max(count - 1, 1),
            )
            x_center = side * (
                knuckle_outer_diameter * 0.5 + (leaf_width_a if side < 0.0 else leaf_width_b) * 0.5
            )
            for z_pos in z_positions:
                if pattern.style == "slotted":
                    if pattern.slot_size is None:
                        raise ValueError("Slotted hinge holes require slot_size")
                    slot_profile = _brackets_hinges_bezels_rounded_slot_profile(pattern.slot_size[0], pattern.slot_size[1])
                    cutter = (
                        cq.Workplane("XZ")
                        .polyline(
                            [(x_center + point[0], z_pos + point[1]) for point in slot_profile]
                        )
                        .close()
                        .extrude(leaf_thickness + 0.01, both=True)
                    )
                else:
                    hole_r = (pattern.diameter or 0.0) * 0.5
                    cutter = (
                        cq.Workplane("XZ")
                        .circle(hole_r)
                        .extrude(leaf_thickness + 0.01, both=True)
                        .translate((x_center, 0.0, z_pos))
                    )
                base_shape = base_shape.cut(cutter)
            return base_shape

        shape = _apply_holes(shape, holes_a, -1.0)
        shape = _apply_holes(shape, holes_b, 1.0)

        _brackets_hinges_bezels_finish(self, shape, center)


class PianoHingeGeometry(MeshGeometry):
    """Build a continuous piano hinge strip around a local Z pin axis."""

    def __init__(
        self,
        length: float,
        *,
        leaf_width_a: float,
        leaf_width_b: Optional[float] = None,
        leaf_thickness: float,
        pin_diameter: float,
        knuckle_pitch: float,
        clearance: float = 0.0005,
        open_angle_deg: float = 180.0,
        holes_a: Optional[HingeHolePattern] = None,
        holes_b: Optional[HingeHolePattern] = None,
        pin: Optional[HingePinStyle] = None,
        center: bool = True,
    ):
        super().__init__()
        knuckle_pitch = float(knuckle_pitch)
        if knuckle_pitch <= 0.0:
            raise ValueError("knuckle_pitch must be positive")
        knuckle_count = max(3, int(length / knuckle_pitch))
        if knuckle_count % 2 == 0:
            knuckle_count += 1
        knuckle_outer_diameter = pin_diameter * 1.55
        base = _BarrelHingeGeometry(
            length,
            leaf_width_a=leaf_width_a,
            leaf_width_b=leaf_width_b,
            leaf_thickness=leaf_thickness,
            pin_diameter=pin_diameter,
            knuckle_outer_diameter=knuckle_outer_diameter,
            knuckle_count=knuckle_count,
            clearance=clearance,
            open_angle_deg=open_angle_deg,
            holes_a=holes_a,
            holes_b=holes_b,
            pin=pin,
            center=center,
        )
        self._set(base._cq())


# ===========================================================================
# @section placement-math   (Vec/Mat helpers ported from sdk geometry_qc /
# placement -- pure analytic, no triangle meshes)
# ===========================================================================
def _dot(a, b):
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(v):
    return math.sqrt(_dot(v, v))


def _normalize_vec(v, *, name="vector"):
    n = _norm(v)
    if n <= _EPS:
        raise ValidationError(f"{name} must be non-zero")
    return (v[0] / n, v[1] / n, v[2] / n)


def _brackets_hinges_bezels_clamp(value, lo, hi):
    return min(max(float(value), float(lo)), float(hi))


def _identity4():
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _rpy_matrix(roll, pitch, yaw):
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return (
        (cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
        (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
        (-sp, cp * sr, cp * cr),
    )


def _origin_to_mat4(origin):
    if origin is None:
        return _identity4()
    ox, oy, oz = origin.xyz
    rr, rp, ry = origin.rpy
    r = _rpy_matrix(float(rr), float(rp), float(ry))
    return (
        (r[0][0], r[0][1], r[0][2], float(ox)),
        (r[1][0], r[1][1], r[1][2], float(oy)),
        (r[2][0], r[2][1], r[2][2], float(oz)),
        (0.0, 0.0, 0.0, 1.0),
    )


def _mat4_mul(a, b):
    out = [[0.0] * 4 for _ in range(4)]
    for i in range(4):
        for j in range(4):
            out[i][j] = (
                a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j] + a[i][3] * b[3][j]
            )
    return tuple(tuple(row) for row in out)


def _mat4_vec3(mat, vec):
    x, y, z = vec
    return (
        mat[0][0] * x + mat[0][1] * y + mat[0][2] * z + mat[0][3],
        mat[1][0] * x + mat[1][1] * y + mat[1][2] * z + mat[1][3],
        mat[2][0] * x + mat[2][1] * y + mat[2][2] * z + mat[2][3],
    )


def _mat3_from_mat4(mat):
    return (
        (mat[0][0], mat[0][1], mat[0][2]),
        (mat[1][0], mat[1][1], mat[1][2]),
        (mat[2][0], mat[2][1], mat[2][2]),
    )


def _mat3_transpose(mat):
    return (
        (mat[0][0], mat[1][0], mat[2][0]),
        (mat[0][1], mat[1][1], mat[2][1]),
        (mat[0][2], mat[1][2], mat[2][2]),
    )


def _mat3_vec3(mat, vec):
    return (
        mat[0][0] * vec[0] + mat[0][1] * vec[1] + mat[0][2] * vec[2],
        mat[1][0] * vec[0] + mat[1][1] * vec[1] + mat[1][2] * vec[2],
        mat[2][0] * vec[0] + mat[2][1] * vec[1] + mat[2][2] * vec[2],
    )


def _mat3_mul(a, b):
    rows = []
    for i in range(3):
        rows.append(tuple(
            a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j] for j in range(3)
        ))
    return (rows[0], rows[1], rows[2])


def _mat3_from_columns(a, b, c):
    return ((a[0], b[0], c[0]), (a[1], b[1], c[1]), (a[2], b[2], c[2]))


def _mat3_to_rpy(mat):
    pitch = math.asin(_brackets_hinges_bezels_clamp(-mat[2][0], -1.0, 1.0))
    cp = math.cos(pitch)
    if abs(cp) > 1e-8:
        roll = math.atan2(mat[2][1], mat[2][2])
        yaw = math.atan2(mat[1][0], mat[0][0])
        return (roll, pitch, yaw)
    roll = 0.0
    if pitch >= 0.0:
        yaw = math.atan2(-mat[0][1], mat[1][1])
    else:
        yaw = math.atan2(mat[0][1], mat[1][1])
    return (roll, pitch, yaw)


def _mat4_inverse_rigid(tf):
    rot = _mat3_from_mat4(tf)
    rot_t = _mat3_transpose(rot)
    trans = (float(tf[0][3]), float(tf[1][3]), float(tf[2][3]))
    inv_trans = _mat3_vec3(rot_t, (-trans[0], -trans[1], -trans[2]))
    return (
        (rot_t[0][0], rot_t[0][1], rot_t[0][2], inv_trans[0]),
        (rot_t[1][0], rot_t[1][1], rot_t[1][2], inv_trans[1]),
        (rot_t[2][0], rot_t[2][1], rot_t[2][2], inv_trans[2]),
        (0.0, 0.0, 0.0, 1.0),
    )


def _mat4_transform_direction(tf, vec):
    return _mat3_vec3(_mat3_from_mat4(tf), vec)


def _axis_angle_matrix(axis, angle):
    ax = _normalize_vec(axis, name="axis")
    x, y, z = ax
    c = math.cos(angle)
    s = math.sin(angle)
    t = 1.0 - c
    return (
        (t * x * x + c, t * x * y - s * z, t * x * z + s * y),
        (t * x * y + s * z, t * y * y + c, t * y * z - s * x),
        (t * x * z - s * y, t * y * z + s * x, t * z * z + c),
    )


def _transform_aabb(aabb, tf):
    (min_x, min_y, min_z), (max_x, max_y, max_z) = aabb
    corners = [(x, y, z) for x in (min_x, max_x) for y in (min_y, max_y) for z in (min_z, max_z)]
    tx0 = ty0 = tz0 = float("inf")
    tx1 = ty1 = tz1 = float("-inf")
    for c in corners:
        x, y, z = _mat4_vec3(tf, c)
        tx0, ty0, tz0 = min(tx0, x), min(ty0, y), min(tz0, z)
        tx1, ty1, tz1 = max(tx1, x), max(ty1, y), max(tz1, z)
    return (tx0, ty0, tz0), (tx1, ty1, tz1)


def _aabb_union(items):
    if not items:
        raise ValidationError("Cannot union empty AABB list")
    mins = [float("inf")] * 3
    maxs = [float("-inf")] * 3
    for mn, mx in items:
        for i in range(3):
            mins[i] = min(mins[i], mn[i])
            maxs[i] = max(maxs[i], mx[i])
    return (mins[0], mins[1], mins[2]), (maxs[0], maxs[1], maxs[2])


def _aabb_center(aabb):
    (mn, mx) = aabb
    return ((mn[0] + mx[0]) * 0.5, (mn[1] + mx[1]) * 0.5, (mn[2] + mx[2]) * 0.5)


def _aabb_size(aabb):
    (mn, mx) = aabb
    return (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])


# ---------------------------------------------------------------------------
# subject iteration / local AABB (Box/Cylinder/Sphere analytic; any other
# geometry falls back to its cadquery B-rep bounding box)
# ---------------------------------------------------------------------------
def _geometry_local_aabb(geometry):
    if isinstance(geometry, Box):
        sx, sy, sz = geometry.size
        return ((-sx / 2.0, -sy / 2.0, -sz / 2.0), (sx / 2.0, sy / 2.0, sz / 2.0))
    if isinstance(geometry, Cylinder):
        r = float(geometry.radius)
        length = float(geometry.length)
        return ((-r, -r, -length / 2.0), (r, r, length / 2.0))
    if isinstance(geometry, Sphere):
        r = float(geometry.radius)
        return ((-r, -r, -r), (r, r, r))
    shape = _to_shape(geometry)
    bb = shape.BoundingBox()
    return ((bb.xmin, bb.ymin, bb.zmin), (bb.xmax, bb.ymax, bb.zmax))


def _iter_subject_items(subject, *, prefer_collisions):
    if isinstance(subject, Part):
        collisions = list(getattr(subject, "collisions", []) or [])
        if prefer_collisions and collisions:
            items = collisions
        elif subject.visuals:
            items = list(subject.visuals)
        else:
            items = collisions
        return [
            (item.geometry, _origin_to_mat4(getattr(item, "origin", None)))
            for item in items
            if getattr(item, "geometry", None) is not None
        ]
    if isinstance(subject, Visual):
        return [(subject.geometry, _origin_to_mat4(subject.origin))]
    return [(subject, _identity4())]


def link_local_aabbs(link, *, asset_root=None, prefer_collisions=True):
    items = _iter_subject_items(link, prefer_collisions=prefer_collisions)
    return [_transform_aabb(_geometry_local_aabb(g), tf) for g, tf in items]


def link_local_aabb(link, *, asset_root=None, prefer_collisions=True):
    aabbs = link_local_aabbs(link, asset_root=asset_root, prefer_collisions=prefer_collisions)
    if not aabbs:
        return None
    return _aabb_union(aabbs)


def _subject_world_aabb(subject, *, asset_root=None, prefer_collisions):
    aabbs = []
    for geometry, tf in _iter_subject_items(subject, prefer_collisions=prefer_collisions):
        aabbs.append(_transform_aabb(_geometry_local_aabb(geometry), tf))
    if not aabbs:
        raise ValidationError("subject has no geometry to compute AABB")
    return _aabb_union(aabbs)


# ===========================================================================
# @section placement-surface-query  (closest point on box/cylinder/sphere)
# ===========================================================================
@dataclass(frozen=True)
class SurfaceFrame:
    point: Tuple[float, float, float]
    normal: Tuple[float, float, float]
    tangent_u: Tuple[float, float, float]
    tangent_v: Tuple[float, float, float]


@dataclass(frozen=True)
class _SurfaceHit:
    point: Tuple[float, float, float]
    normal: Tuple[float, float, float]
    distance: float


def _closest_point_on_box(point, size):
    hx, hy, hz = float(size[0]) * 0.5, float(size[1]) * 0.5, float(size[2]) * 0.5
    x, y, z = point
    clamped = (_brackets_hinges_bezels_clamp(x, -hx, hx), _brackets_hinges_bezels_clamp(y, -hy, hy), _brackets_hinges_bezels_clamp(z, -hz, hz))
    inside = abs(x) <= hx and abs(y) <= hy and abs(z) <= hz
    if inside:
        face_dist = (hx - abs(x), hy - abs(y), hz - abs(z))
        axis = min(range(3), key=lambda idx: face_dist[idx])
        sign = 1.0 if point[axis] >= 0.0 else -1.0
        nearest = [x, y, z]
        nearest[axis] = (hx, hy, hz)[axis] * sign
        normal = [0.0, 0.0, 0.0]
        normal[axis] = sign
        return (nearest[0], nearest[1], nearest[2]), (normal[0], normal[1], normal[2])
    dx = x - clamped[0]
    dy = y - clamped[1]
    dz = z - clamped[2]
    axis = max(range(3), key=lambda idx: abs((dx, dy, dz)[idx]))
    delta = (dx, dy, dz)[axis]
    sign = 1.0 if delta >= 0.0 else -1.0
    normal = [0.0, 0.0, 0.0]
    normal[axis] = sign
    return clamped, (normal[0], normal[1], normal[2])


def _closest_point_on_cylinder(point, radius, length):
    x, y, z = point
    half = float(length) * 0.5
    radial = math.hypot(x, y)
    if radial <= _EPS:
        rx, ry = 1.0, 0.0
    else:
        rx, ry = x / radial, y / radial
    side = (rx * radius, ry * radius, _brackets_hinges_bezels_clamp(z, -half, half))
    side_d2 = (x - side[0]) ** 2 + (y - side[1]) ** 2 + (z - side[2]) ** 2

    def cap_candidate(sign):
        cap_z = sign * half
        if radial <= radius:
            cap_xy = (x, y)
        else:
            cap_xy = (rx * radius, ry * radius)
        cap = (cap_xy[0], cap_xy[1], cap_z)
        d2 = (x - cap[0]) ** 2 + (y - cap[1]) ** 2 + (z - cap[2]) ** 2
        return cap, d2

    top, top_d2 = cap_candidate(+1.0)
    bottom, bottom_d2 = cap_candidate(-1.0)
    if side_d2 <= top_d2 and side_d2 <= bottom_d2:
        return side, (rx, ry, 0.0)
    if top_d2 <= bottom_d2:
        return top, (0.0, 0.0, 1.0)
    return bottom, (0.0, 0.0, -1.0)


def _query_surface_on_element(geometry, tf, *, point_hint):
    inv_tf = _mat4_inverse_rigid(tf)
    point_local = _mat4_vec3(inv_tf, point_hint)

    if isinstance(geometry, Sphere):
        radius = float(geometry.radius)
        direction = point_local
        if _norm(direction) <= _EPS:
            direction = (1.0, 0.0, 0.0)
        normal_local = _normalize_vec(direction, name="sphere query direction")
        surface_local = (normal_local[0] * radius, normal_local[1] * radius, normal_local[2] * radius)
    elif isinstance(geometry, Box):
        surface_local, normal_local = _closest_point_on_box(point_local, geometry.size)
    elif isinstance(geometry, Cylinder):
        surface_local, normal_local = _closest_point_on_cylinder(
            point_local, radius=float(geometry.radius), length=float(geometry.length)
        )
    else:
        # generic B-rep: approximate by its local AABB treated as a box
        (mn, mx) = _geometry_local_aabb(geometry)
        ctr = ((mn[0] + mx[0]) * 0.5, (mn[1] + mx[1]) * 0.5, (mn[2] + mx[2]) * 0.5)
        size = (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])
        rel = (point_local[0] - ctr[0], point_local[1] - ctr[1], point_local[2] - ctr[2])
        sp, normal_local = _closest_point_on_box(rel, size)
        surface_local = (sp[0] + ctr[0], sp[1] + ctr[1], sp[2] + ctr[2])

    distance = _norm(
        (point_local[0] - surface_local[0],
         point_local[1] - surface_local[1],
         point_local[2] - surface_local[2])
    )
    point_world = _mat4_vec3(tf, surface_local)
    normal_world = _normalize_vec(_mat4_transform_direction(tf, normal_local), name="surface normal")
    return _SurfaceHit(point=point_world, normal=normal_world, distance=distance)


def _query_subject_surface_at_point(subject, *, point_hint, asset_root=None, prefer_collisions):
    best = None
    for geometry, tf in _iter_subject_items(subject, prefer_collisions=prefer_collisions):
        hit = _query_surface_on_element(geometry, tf, point_hint=point_hint)
        if best is None or hit.distance < best.distance:
            best = hit
    if best is None:
        raise ValidationError("target has no geometry to query")
    return best


def _brackets_hinges_bezels_build_surface_tangents(normal, up_hint):
    up = _normalize_vec(up_hint, name="up_hint")
    projected = (
        up[0] - normal[0] * _dot(up, normal),
        up[1] - normal[1] * _dot(up, normal),
        up[2] - normal[2] * _dot(up, normal),
    )
    if _norm(projected) <= _EPS:
        fallback = (1.0, 0.0, 0.0)
        if abs(_dot(fallback, normal)) >= 0.95:
            fallback = (0.0, 1.0, 0.0)
        projected = _cross(fallback, normal)
    tangent_u = _normalize_vec(projected, name="surface tangent")
    tangent_v = _normalize_vec(_cross(normal, tangent_u), name="surface bitangent")
    return tangent_u, tangent_v


def surface_frame(
    target,
    *,
    point_hint=None,
    direction=None,
    asset_root=None,
    prefer_collisions=False,
    up_hint=(0.0, 0.0, 1.0),
):
    if (point_hint is None) == (direction is None):
        raise ValidationError("Exactly one of point_hint or direction must be provided")
    query_point = point_hint
    if direction is not None:
        dir_world = _normalize_vec(direction, name="direction")
        aabb = _subject_world_aabb(target, prefer_collisions=prefer_collisions)
        center = _aabb_center(aabb)
        size = _aabb_size(aabb)
        radius = max(_norm(size), 1e-3)
        query_point = (
            center[0] + dir_world[0] * radius * 4.0,
            center[1] + dir_world[1] * radius * 4.0,
            center[2] + dir_world[2] * radius * 4.0,
        )
    best = _query_subject_surface_at_point(
        target, point_hint=query_point, prefer_collisions=prefer_collisions
    )
    tangent_u, tangent_v = _brackets_hinges_bezels_build_surface_tangents(best.normal, up_hint)
    return SurfaceFrame(point=best.point, normal=best.normal, tangent_u=tangent_u, tangent_v=tangent_v)


# ===========================================================================
# @section placement-mount  (place_on_surface / place_on_face / *_uv / proud)
# ===========================================================================
def _axis_vector(axis):
    table = {
        "+x": (1.0, 0.0, 0.0), "-x": (-1.0, 0.0, 0.0),
        "+y": (0.0, 1.0, 0.0), "-y": (0.0, -1.0, 0.0),
        "+z": (0.0, 0.0, 1.0), "-z": (0.0, 0.0, -1.0),
    }
    axis_key = axis.strip().lower()
    if axis_key not in table:
        raise ValidationError(f"Invalid axis {axis!r}; expected one of: {', '.join(sorted(table))}")
    return table[axis_key]


def _child_local_basis_for_axis(axis):
    basis = {
        "+z": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        "-z": ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0)),
        "+x": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
        "-x": ((0.0, 1.0, 0.0), (0.0, 0.0, -1.0), (-1.0, 0.0, 0.0)),
        "+y": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        "-y": ((0.0, 0.0, -1.0), (1.0, 0.0, 0.0), (0.0, -1.0, 0.0)),
    }
    axis_key = axis.strip().lower()
    if axis_key not in basis:
        raise ValidationError(f"Invalid axis {axis!r}; expected one of: {', '.join(sorted(basis))}")
    return basis[axis_key]


def _rotation_for_surface_frame(frame, *, child_axis, spin):
    local_basis = _child_local_basis_for_axis(child_axis)
    world_basis = _mat3_from_columns(frame.tangent_u, frame.tangent_v, frame.normal)
    local_basis_mat = _mat3_from_columns(local_basis[0], local_basis[1], local_basis[2])
    base = _mat3_mul(world_basis, _mat3_transpose(local_basis_mat))
    if abs(float(spin)) <= _EPS:
        return base
    spin_world = _axis_angle_matrix(frame.normal, float(spin))
    return _mat3_mul(spin_world, base)


def _support_min_projection_on_element(geometry, tf, *, axis):
    center = (float(tf[0][3]), float(tf[1][3]), float(tf[2][3]))
    rot = _mat3_from_mat4(tf)
    axis_local = _mat3_vec3(_mat3_transpose(rot), axis)

    if isinstance(geometry, Sphere):
        return _dot(center, axis) - float(geometry.radius)
    if isinstance(geometry, Box):
        hx, hy, hz = (float(geometry.size[0]) * 0.5, float(geometry.size[1]) * 0.5,
                      float(geometry.size[2]) * 0.5)
        half = hx * abs(axis_local[0]) + hy * abs(axis_local[1]) + hz * abs(axis_local[2])
        return _dot(center, axis) - half
    if isinstance(geometry, Cylinder):
        radial = math.hypot(axis_local[0], axis_local[1])
        half = float(geometry.radius) * radial + (float(geometry.length) * 0.5) * abs(axis_local[2])
        return _dot(center, axis) - half
    # generic B-rep: support of its local AABB box (centered at AABB center)
    (mn, mx) = _geometry_local_aabb(geometry)
    c_local = ((mn[0] + mx[0]) * 0.5, (mn[1] + mx[1]) * 0.5, (mn[2] + mx[2]) * 0.5)
    hx, hy, hz = (mx[0] - mn[0]) * 0.5, (mx[1] - mn[1]) * 0.5, (mx[2] - mn[2]) * 0.5
    world_center = _mat4_vec3(tf, c_local)
    half = hx * abs(axis_local[0]) + hy * abs(axis_local[1]) + hz * abs(axis_local[2])
    return _dot(world_center, axis) - half


def _subject_min_projection(subject, *, axis, asset_root=None, prefer_collisions):
    projections = [
        _support_min_projection_on_element(geometry, tf, axis=axis)
        for geometry, tf in _iter_subject_items(subject, prefer_collisions=prefer_collisions)
    ]
    if not projections:
        raise ValidationError("child has no geometry to compute support distance")
    return min(projections)


def place_on_surface(
    child,
    target,
    *,
    point_hint=None,
    direction=None,
    child_axis="+z",
    clearance=0.0,
    spin=0.0,
    asset_root=None,
    prefer_collisions=False,
    child_prefer_collisions=False,
    up_hint=(0.0, 0.0, 1.0),
):
    """Rigidly place a child on a target surface using a tangent-frame mount."""
    frame = surface_frame(
        target, point_hint=point_hint, direction=direction,
        prefer_collisions=prefer_collisions, up_hint=up_hint,
    )
    child_axis_vec = _axis_vector(child_axis)
    mount_proj = _subject_min_projection(
        child, axis=child_axis_vec, prefer_collisions=child_prefer_collisions
    )
    offset = float(clearance) - float(mount_proj)
    xyz = (
        frame.point[0] + frame.normal[0] * offset,
        frame.point[1] + frame.normal[1] * offset,
        frame.point[2] + frame.normal[2] * offset,
    )
    rot = _rotation_for_surface_frame(frame, child_axis=child_axis, spin=float(spin))
    return Origin(xyz=xyz, rpy=_mat3_to_rpy(rot))


_FACE_TABLE = {
    "+x": (0, +1.0, (0.0, math.pi / 2.0, 0.0)),
    "-x": (0, -1.0, (0.0, -math.pi / 2.0, 0.0)),
    "+y": (1, +1.0, (-math.pi / 2.0, 0.0, 0.0)),
    "-y": (1, -1.0, (math.pi / 2.0, 0.0, 0.0)),
    "+z": (2, +1.0, (0.0, 0.0, 0.0)),
    "-z": (2, -1.0, (math.pi, 0.0, 0.0)),
}

_FACE_TANGENT_AXES = {
    "+x": (1, 2), "-x": (1, 2),
    "+y": (0, 2), "-y": (0, 2),
    "+z": (0, 1), "-z": (0, 1),
}


def _as_pair(values, *, name):
    try:
        if len(values) != 2:
            raise ValidationError(f"{name} must have 2 elements")
        return (float(values[0]), float(values[1]))
    except TypeError as exc:
        raise ValidationError(f"{name} must have 2 elements") from exc


def place_on_face(
    parent_link,
    face,
    *,
    face_pos=(0.0, 0.0),
    proud=0.0,
    asset_root=None,
    prefer_collisions=True,
):
    """Compute an Origin that places a child on a face of the parent's AABB."""
    face_key = face.strip().lower()
    if face_key not in _FACE_TABLE:
        raise ValidationError(
            f"Invalid face {face!r}; expected one of: {', '.join(sorted(_FACE_TABLE))}"
        )
    axis_idx, sign, rpy = _FACE_TABLE[face_key]
    tang_a, tang_b = _FACE_TANGENT_AXES[face_key]
    face_pos_pair = _as_pair(face_pos, name="face_pos")

    aabbs = link_local_aabbs(parent_link, prefer_collisions=prefer_collisions)
    if not aabbs:
        raise ValidationError(f"Parent link {parent_link.name!r} has no geometry to compute AABB")
    (mn, mx) = _aabb_union(aabbs)

    surface = float(mx[axis_idx]) if sign > 0 else float(mn[axis_idx])
    normal_coord = surface + sign * float(proud)

    xyz = [0.0, 0.0, 0.0]
    xyz[axis_idx] = normal_coord
    xyz[tang_a] = face_pos_pair[0]
    xyz[tang_b] = face_pos_pair[1]
    return Origin(xyz=(xyz[0], xyz[1], xyz[2]), rpy=rpy)


def place_on_face_uv(
    parent_link,
    face,
    *,
    uv=(0.5, 0.5),
    uv_margin=0.0,
    proud=0.0,
    asset_root=None,
    prefer_collisions=True,
):
    """Like place_on_face but address the face using normalized (u, v) coordinates."""
    face_key = face.strip().lower()
    if face_key not in _FACE_TABLE:
        raise ValidationError(
            f"Invalid face {face!r}; expected one of: {', '.join(sorted(_FACE_TABLE))}"
        )
    axis_idx, sign, rpy = _FACE_TABLE[face_key]
    tang_a, tang_b = _FACE_TANGENT_AXES[face_key]

    if isinstance(uv_margin, tuple):
        mu, mv = _as_pair(uv_margin, name="uv_margin")
    else:
        mu = mv = float(uv_margin)
    mu = max(0.0, min(0.49, mu))
    mv = max(0.0, min(0.49, mv))

    u_raw, v_raw = _as_pair(uv, name="uv")
    u = max(0.0, min(1.0, u_raw))
    v = max(0.0, min(1.0, v_raw))
    u = mu + (1.0 - 2.0 * mu) * u
    v = mv + (1.0 - 2.0 * mv) * v

    aabbs = link_local_aabbs(parent_link, prefer_collisions=prefer_collisions)
    if not aabbs:
        raise ValidationError(f"Parent link {parent_link.name!r} has no geometry to compute AABB")
    (mn, mx) = _aabb_union(aabbs)

    surface = float(mx[axis_idx]) if sign > 0 else float(mn[axis_idx])
    normal_coord = surface + sign * float(proud)

    ta0, ta1 = float(mn[tang_a]), float(mx[tang_a])
    tb0, tb1 = float(mn[tang_b]), float(mx[tang_b])
    face_pos = (ta0 + (ta1 - ta0) * u, tb0 + (tb1 - tb0) * v)

    xyz = [0.0, 0.0, 0.0]
    xyz[axis_idx] = normal_coord
    xyz[tang_a] = float(face_pos[0])
    xyz[tang_b] = float(face_pos[1])
    return Origin(xyz=(xyz[0], xyz[1], xyz[2]), rpy=rpy)


def proud_for_flush_mount(child_link, *, axis="z", clearance=0.0, asset_root=None,
                          prefer_collisions=True):
    """Return half the child's thickness along ``axis`` (+clearance) for a flush mount."""
    axis_key = axis.strip().lower()
    axis_idx = {"x": 0, "y": 1, "z": 2}.get(axis_key)
    if axis_idx is None:
        raise ValidationError(f"Invalid axis {axis!r}; expected 'x', 'y', or 'z'.")
    aabb = link_local_aabb(child_link, prefer_collisions=prefer_collisions)
    if aabb is None:
        raise ValidationError(f"Child link {child_link.name!r} has no geometry to compute AABB")
    (mn, mx) = aabb
    thickness = float(mx[axis_idx]) - float(mn[axis_idx])
    return 0.5 * thickness + float(clearance)


# ===========================================================================
# @section placement-aabb-overlap  (cadquery_local_aabb / AllowedOverlap)
# ===========================================================================
def cadquery_local_aabb(model, *, tolerance=0.001, angular_tolerance=0.1, unit_scale=1.0):
    """Axis-aligned bounding box of a cadquery model in its local frame."""
    shape = _to_shape(model)
    try:
        verts, _tris = shape.tessellate(float(tolerance), float(angular_tolerance))
    except TypeError:
        verts, _tris = shape.tessellate(float(tolerance))
    except Exception:
        verts = None
    if verts:
        xs = [v.x for v in verts]
        ys = [v.y for v in verts]
        zs = [v.z for v in verts]
        mn = (min(xs), min(ys), min(zs))
        mx = (max(xs), max(ys), max(zs))
    else:
        bb = shape.BoundingBox()
        mn = (bb.xmin, bb.ymin, bb.zmin)
        mx = (bb.xmax, bb.ymax, bb.zmax)
    s = float(unit_scale)
    return ((mn[0] * s, mn[1] * s, mn[2] * s), (mx[0] * s, mx[1] * s, mx[2] * s))


@dataclass(frozen=True)
class AllowedOverlap:
    link_a: str
    link_b: str
    reason: str
    elem_a: Optional[str] = None
    elem_b: Optional[str] = None


# ===========================================================================
# @section mesh-openings-and-wraps  (cut_opening_on_face / wrap_*_onto_surface)
#
# These operate on triangle data (matching the sdk's mesh-level behaviour).
# A shim geometry that only carries a B-rep is tessellated first.
# ===========================================================================
def _cross_z(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_in_triangle(p, a, b, c):
    eps = 1e-9
    c1 = _cross_z(a, b, p)
    c2 = _cross_z(b, c, p)
    c3 = _cross_z(c, a, p)
    return (c1 > eps and c2 > eps and c3 > eps) or (c1 < -eps and c2 < -eps and c3 < -eps)


def _triangulate_polygon(points):
    if len(points) < 3:
        return []
    indices = list(range(len(points)))
    triangles = []
    guard = 0
    max_guard = len(points) * len(points)
    while len(indices) > 3:
        ear_found = False
        for i in range(len(indices)):
            i_prev = indices[i - 1]
            i_curr = indices[i]
            i_next = indices[(i + 1) % len(indices)]
            if _cross_z(points[i_prev], points[i_curr], points[i_next]) <= 0:
                continue
            ear = True
            for j in indices:
                if j in (i_prev, i_curr, i_next):
                    continue
                if _point_in_triangle(points[j], points[i_prev], points[i_curr], points[i_next]):
                    ear = False
                    break
            if ear:
                triangles.append((i_prev, i_curr, i_next))
                del indices[i]
                ear_found = True
                break
        guard += 1
        if not ear_found or guard > max_guard:
            raise ValidationError("Failed to triangulate profile; ensure it is simple")
    triangles.append((indices[0], indices[1], indices[2]))
    return triangles


def _polygon_centroid(points):
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    n = max(1, len(points))
    return (sx / float(n), sy / float(n))


def _geom_vertices_faces(geom):
    """Return (vertices, faces) for a shim geometry, tessellating a B-rep if needed."""
    if getattr(geom, "vertices", None) and getattr(geom, "faces", None):
        return list(geom.vertices), list(geom.faces)
    shape = _to_shape(geom)
    try:
        verts, tris = shape.tessellate(0.001, 0.1)
    except TypeError:
        verts, tris = shape.tessellate(0.001)
    vertices = [(float(v.x), float(v.y), float(v.z)) for v in verts]
    faces = [(int(t[0]), int(t[1]), int(t[2])) for t in tris]
    return vertices, faces


def cut_opening_on_face(
    shell_geometry,
    *,
    face,
    opening_profile,
    depth,
    offset=(0.0, 0.0),
    taper=0.0,
):
    """Add an opening "throat" (internal side walls) on a mesh AABB face.

    Faithful to the sdk: this does not boolean-subtract; it lofts the side
    walls of a recessed opening and merges them into ``shell_geometry``.
    """
    if not isinstance(shell_geometry, MeshGeometry):
        raise TypeError("shell_geometry must be MeshGeometry")

    vertices, faces = _geom_vertices_faces(shell_geometry)
    if not vertices:
        raise ValueError("shell_geometry has no vertices")

    d = float(depth)
    if d <= 0:
        raise ValueError("depth must be positive")

    f = (face or "").strip().lower()
    if len(f) != 2 or f[0] not in "+-" or f[1] not in "xyz":
        raise ValueError("face must be one of: '+x', '-x', '+y', '-y', '+z', '-z'")

    points = _ensure_ccw(_profile_points_2d(opening_profile))
    ox = float(offset[0])
    oy = float(offset[1])
    taper = float(taper)
    if abs(taper) >= 0.95:
        raise ValueError("abs(taper) must be < 0.95")

    center = _polygon_centroid(points)
    scale = 1.0 - taper
    inner_points = [
        (center[0] + (x - center[0]) * scale, center[1] + (y - center[1]) * scale)
        for (x, y) in points
    ]

    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    bounds = {"x": (min(xs), max(xs)), "y": (min(ys), max(ys)), "z": (min(zs), max(zs))}

    axis = f[1]
    sign = 1.0 if f[0] == "+" else -1.0
    face_plane = bounds[axis][1] if sign > 0 else bounds[axis][0]

    def map_uv(u, v, depth_local):
        inward = -sign * depth_local
        if axis == "x":
            return (face_plane + inward, u + ox, v + oy)
        if axis == "y":
            return (u + ox, face_plane + inward, v + oy)
        return (u + ox, v + oy, face_plane + inward)

    p0 = [map_uv(u, v, 0.0) for (u, v) in points]
    p1 = [map_uv(u, v, d) for (u, v) in inner_points]

    # open closed loft (no caps): connect ring p0 -> ring p1 with side quads
    base = len(vertices)
    n = len(p0)
    new_vertices = list(vertices) + p0 + p1
    new_faces = list(faces)
    for i in range(n):
        a = base + i
        b = base + (i + 1) % n
        c = base + n + (i + 1) % n
        dd = base + n + i
        new_faces.append((a, b, c))
        new_faces.append((a, c, dd))

    shell_geometry._shape = None
    shell_geometry.vertices = new_vertices
    shell_geometry.faces = new_faces
    return shell_geometry


# ---------------------------------------------------------------------------
# surface wrapping helpers (port of sdk placement wrap kernels)
# ---------------------------------------------------------------------------
def _subdivide_mesh_geometry_to_size(geometry, *, max_edge):
    if max_edge is None:
        return geometry
    max_edge_value = float(max_edge)
    if max_edge_value <= 0.0:
        raise ValidationError("max_edge must be positive")
    if not geometry.vertices or not geometry.faces:
        return geometry
    import trimesh
    vertices, faces = trimesh.remesh.subdivide_to_size(
        geometry.vertices, geometry.faces, max_edge=max_edge_value
    )
    return MeshGeometry(
        vertices=[(float(v[0]), float(v[1]), float(v[2])) for v in vertices],
        faces=[(int(f[0]), int(f[1]), int(f[2])) for f in faces],
    )


def _normalize_profile_points(profile):
    return _ensure_ccw(_profile_points_2d(profile))


def _planar_profile_cap(profile, *, max_edge):
    points = _normalize_profile_points(profile)
    faces = _triangulate_polygon(points)
    cap = MeshGeometry(
        vertices=[(float(x), float(y), 0.0) for x, y in points],
        faces=[(int(a), int(b), int(c)) for a, b, c in faces],
    )
    if not cap.faces:
        raise ValidationError("profile triangulation produced no faces")
    return _subdivide_mesh_geometry_to_size(cap, max_edge=max_edge)


def _boundary_loop_for_planar_mesh(geometry, *, boundary_profile):
    edge_counts = {}
    for a, b, c in geometry.faces:
        for u, v in ((a, b), (b, c), (c, a)):
            key = (u, v) if u < v else (v, u)
            edge_counts[key] = edge_counts.get(key, 0) + 1
    boundary_vertices = sorted(
        {index for edge, count in edge_counts.items() if count == 1 for index in edge}
    )
    if not boundary_vertices:
        raise ValidationError("profile mesh has no boundary loop")

    cumulative = [0.0]
    for i, a in enumerate(boundary_profile):
        b = boundary_profile[(i + 1) % len(boundary_profile)]
        cumulative.append(
            cumulative[-1] + math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
        )
    perimeter = cumulative[-1]
    if perimeter <= _EPS:
        raise ValidationError("profile perimeter must be non-zero")

    def _dist_to_seg(point, a, b):
        seg_x = float(b[0]) - float(a[0])
        seg_y = float(b[1]) - float(a[1])
        seg_len2 = seg_x * seg_x + seg_y * seg_y
        if seg_len2 <= _EPS:
            return math.hypot(point[0] - a[0], point[1] - a[1]), 0.0
        rel_x = float(point[0]) - float(a[0])
        rel_y = float(point[1]) - float(a[1])
        t = _brackets_hinges_bezels_clamp((rel_x * seg_x + rel_y * seg_y) / seg_len2, 0.0, 1.0)
        cx = float(a[0]) + seg_x * t
        cy = float(a[1]) + seg_y * t
        return math.hypot(float(point[0]) - cx, float(point[1]) - cy), t

    ordered = []
    for index in boundary_vertices:
        point = (float(geometry.vertices[index][0]), float(geometry.vertices[index][1]))
        best_distance = float("inf")
        best_arclength = 0.0
        for segment_index, a in enumerate(boundary_profile):
            b = boundary_profile[(segment_index + 1) % len(boundary_profile)]
            distance, t = _dist_to_seg(point, a, b)
            if distance + 1e-9 < best_distance:
                best_distance = distance
                best_arclength = (
                    cumulative[segment_index]
                    + (cumulative[segment_index + 1] - cumulative[segment_index]) * t
                )
        if best_distance > 1e-5:
            raise ValidationError("profile mesh boundary deviated from source profile")
        if best_arclength >= perimeter - 1e-8:
            best_arclength = 0.0
        ordered.append((best_arclength, index))

    ordered.sort(key=lambda item: item[0])
    loop = [index for _, index in ordered]
    area = 0.0
    for i, index in enumerate(loop):
        x1, y1, _ = geometry.vertices[index]
        x2, y2, _ = geometry.vertices[loop[(i + 1) % len(loop)]]
        area += x1 * y2 - x2 * y1
    if area < 0.0:
        loop.reverse()
    return loop


def _solid_from_planar_profile_cap(cap, *, boundary_profile, thickness):
    thickness_value = float(thickness)
    if thickness_value <= 0.0:
        raise ValidationError("thickness must be positive")
    loop = _boundary_loop_for_planar_mesh(cap, boundary_profile=boundary_profile)
    vertex_count = len(cap.vertices)
    solid = MeshGeometry(
        vertices=[
            *[(float(x), float(y), 0.0) for x, y, _ in cap.vertices],
            *[(float(x), float(y), -thickness_value) for x, y, _ in cap.vertices],
        ],
        faces=[],
    )
    for a, b, c in cap.faces:
        solid.add_face(a, b, c)
    offset = vertex_count
    for a, b, c in cap.faces:
        solid.add_face(offset + c, offset + b, offset + a)
    for i, a in enumerate(loop):
        b = loop[(i + 1) % len(loop)]
        solid.add_face(a, b, offset + b)
        solid.add_face(a, offset + b, offset + a)
    return solid


def _fallback_profile_solid(profile, *, hole_profiles, thickness):
    """Direct prism builder (visible cap on z=0, thickness toward -z).

    Holes are not supported in this fallback path; they are ignored.
    """
    points = _normalize_profile_points(profile)
    faces = _triangulate_polygon(points)
    cap = MeshGeometry(vertices=[(float(x), float(y), 0.0) for x, y in points],
                       faces=[(int(a), int(b), int(c)) for a, b, c in faces])
    return _solid_from_planar_profile_cap(cap, boundary_profile=points, thickness=thickness)


def _mesh_geometry_from_subject(mesh):
    if isinstance(mesh, MeshGeometry):
        if getattr(mesh, "vertices", None) and getattr(mesh, "faces", None):
            return mesh.copy()
        v, f = _geom_vertices_faces(mesh)
        return MeshGeometry(vertices=v, faces=f)
    if isinstance(mesh, _CQMesh):
        v, f = _geom_vertices_faces(mesh)
        return MeshGeometry(vertices=v, faces=f)
    raise ValidationError(f"mesh must be a MeshGeometry, got {type(mesh).__name__}")


def _subject_single_world_sphere(subject, *, prefer_collisions):
    items = _iter_subject_items(subject, prefer_collisions=prefer_collisions)
    if len(items) != 1:
        return None
    geometry, tf = items[0]
    if not isinstance(geometry, Sphere):
        return None
    return _mat4_vec3(tf, (0.0, 0.0, 0.0)), float(geometry.radius)


def _subject_single_world_cylinder(subject, *, prefer_collisions):
    items = _iter_subject_items(subject, prefer_collisions=prefer_collisions)
    if len(items) != 1:
        return None
    geometry, tf = items[0]
    if not isinstance(geometry, Cylinder):
        return None
    center = _mat4_vec3(tf, (0.0, 0.0, 0.0))
    axis_world = _normalize_vec(_mat4_transform_direction(tf, (0.0, 0.0, 1.0)), name="cylinder axis")
    return center, axis_world, float(geometry.radius), float(geometry.length)


def _wrap_mesh_onto_sphere(geometry, *, frame, sphere_center, sphere_radius, child_axis,
                           visible_relief, spin):
    rot = _rotation_for_surface_frame(frame, child_axis=child_axis, spin=float(spin))
    axis_local = _axis_vector(child_axis)
    outer_support = max(_dot(vertex, axis_local) for vertex in geometry.vertices)
    wrapped = []
    for vertex in geometry.vertices:
        local_proj = _dot(vertex, axis_local)
        depth_behind = outer_support - local_proj
        in_plane_local = (
            float(vertex[0]) - axis_local[0] * local_proj,
            float(vertex[1]) - axis_local[1] * local_proj,
            float(vertex[2]) - axis_local[2] * local_proj,
        )
        tangent_offset = _mat3_vec3(rot, in_plane_local)
        tangent_distance = _norm(tangent_offset)
        if tangent_distance <= _EPS:
            surface_normal = frame.normal
        else:
            tangent_dir = (tangent_offset[0] / tangent_distance,
                           tangent_offset[1] / tangent_distance,
                           tangent_offset[2] / tangent_distance)
            angle = tangent_distance / float(sphere_radius)
            surface_normal = _normalize_vec(
                (math.cos(angle) * frame.normal[0] + math.sin(angle) * tangent_dir[0],
                 math.cos(angle) * frame.normal[1] + math.sin(angle) * tangent_dir[1],
                 math.cos(angle) * frame.normal[2] + math.sin(angle) * tangent_dir[2]),
                name="sphere wrap normal",
            )
        surface_point = (
            sphere_center[0] + surface_normal[0] * float(sphere_radius),
            sphere_center[1] + surface_normal[1] * float(sphere_radius),
            sphere_center[2] + surface_normal[2] * float(sphere_radius),
        )
        wrapped.append((
            surface_point[0] + surface_normal[0] * (float(visible_relief) - depth_behind),
            surface_point[1] + surface_normal[1] * (float(visible_relief) - depth_behind),
            surface_point[2] + surface_normal[2] * (float(visible_relief) - depth_behind),
        ))
    return MeshGeometry(vertices=wrapped, faces=list(geometry.faces))


def _wrap_mesh_onto_cylinder(geometry, *, frame, cylinder_center, cylinder_axis, cylinder_radius,
                             cylinder_length, child_axis, visible_relief, spin):
    if abs(_dot(frame.normal, cylinder_axis)) >= 0.2:
        return None
    rel_anchor = (frame.point[0] - cylinder_center[0],
                  frame.point[1] - cylinder_center[1],
                  frame.point[2] - cylinder_center[2])
    anchor_axial = _dot(rel_anchor, cylinder_axis)
    anchor_radial = (rel_anchor[0] - cylinder_axis[0] * anchor_axial,
                     rel_anchor[1] - cylinder_axis[1] * anchor_axial,
                     rel_anchor[2] - cylinder_axis[2] * anchor_axial)
    if _norm(anchor_radial) <= _EPS:
        return None
    anchor_normal = _normalize_vec(anchor_radial, name="cylinder wrap normal")
    circum_axis = _normalize_vec(_cross(cylinder_axis, anchor_normal),
                                 name="cylinder circumferential tangent")
    half_length = float(cylinder_length) * 0.5
    rot = _rotation_for_surface_frame(frame, child_axis=child_axis, spin=float(spin))
    axis_local = _axis_vector(child_axis)
    outer_support = max(_dot(vertex, axis_local) for vertex in geometry.vertices)
    wrapped = []
    for vertex in geometry.vertices:
        local_proj = _dot(vertex, axis_local)
        depth_behind = outer_support - local_proj
        in_plane_local = (
            float(vertex[0]) - axis_local[0] * local_proj,
            float(vertex[1]) - axis_local[1] * local_proj,
            float(vertex[2]) - axis_local[2] * local_proj,
        )
        tangent_offset = _mat3_vec3(rot, in_plane_local)
        circum_distance = _dot(tangent_offset, circum_axis)
        axial_distance = _dot(tangent_offset, cylinder_axis)
        axial = anchor_axial + axial_distance
        if abs(axial) > half_length + 1e-9:
            return None
        angle = circum_distance / float(cylinder_radius)
        turn = _axis_angle_matrix(cylinder_axis, angle)
        surface_normal = _normalize_vec(_mat3_vec3(turn, anchor_normal),
                                        name="cylinder wrap rotated normal")
        surface_point = (
            cylinder_center[0] + cylinder_axis[0] * axial + surface_normal[0] * float(cylinder_radius),
            cylinder_center[1] + cylinder_axis[1] * axial + surface_normal[1] * float(cylinder_radius),
            cylinder_center[2] + cylinder_axis[2] * axial + surface_normal[2] * float(cylinder_radius),
        )
        wrapped.append((
            surface_point[0] + surface_normal[0] * (float(visible_relief) - depth_behind),
            surface_point[1] + surface_normal[1] * (float(visible_relief) - depth_behind),
            surface_point[2] + surface_normal[2] * (float(visible_relief) - depth_behind),
        ))
    return MeshGeometry(vertices=wrapped, faces=list(geometry.faces))


def _wrap_mesh_onto_surface_nearest(geometry, *, frame, target, child_axis, visible_relief, spin,
                                    prefer_collisions):
    rot = _rotation_for_surface_frame(frame, child_axis=child_axis, spin=float(spin))
    axis_local = _axis_vector(child_axis)
    outer_support = max(_dot(vertex, axis_local) for vertex in geometry.vertices)
    wrapped = []
    for vertex in geometry.vertices:
        local_proj = _dot(vertex, axis_local)
        depth_behind = outer_support - local_proj
        in_plane_local = (
            float(vertex[0]) - axis_local[0] * local_proj,
            float(vertex[1]) - axis_local[1] * local_proj,
            float(vertex[2]) - axis_local[2] * local_proj,
        )
        plane_offset = _mat3_vec3(rot, in_plane_local)
        query_point = (frame.point[0] + plane_offset[0],
                       frame.point[1] + plane_offset[1],
                       frame.point[2] + plane_offset[2])
        hit = _query_subject_surface_at_point(target, point_hint=query_point,
                                              prefer_collisions=prefer_collisions)
        wrapped.append((
            hit.point[0] + hit.normal[0] * (float(visible_relief) - depth_behind),
            hit.point[1] + hit.normal[1] * (float(visible_relief) - depth_behind),
            hit.point[2] + hit.normal[2] * (float(visible_relief) - depth_behind),
        ))
    return MeshGeometry(vertices=wrapped, faces=list(geometry.faces))


def _normalize_surface_wrap_mapping(mapping):
    mapping_key = str(mapping).strip().lower()
    if mapping_key not in {"auto", "intrinsic", "nearest"}:
        raise ValidationError("mapping must be one of: 'auto', 'intrinsic', 'nearest'")
    return mapping_key


def _resolve_surface_max_edge(*, surface_max_edge, max_edge):
    if surface_max_edge is None:
        return max_edge
    if max_edge is None:
        return surface_max_edge
    if abs(float(surface_max_edge) - float(max_edge)) > 1e-12:
        raise ValidationError("Provide only one of surface_max_edge or max_edge")
    return surface_max_edge


def wrap_mesh_onto_surface(
    mesh,
    target,
    *,
    point_hint=None,
    direction=None,
    child_axis="+z",
    visible_relief=0.0,
    mapping="auto",
    surface_max_edge=None,
    max_edge=None,
    spin=0.0,
    asset_root=None,
    prefer_collisions=False,
    up_hint=(0.0, 0.0, 1.0),
):
    """Conform a mesh onto a target surface and return a baked MeshGeometry."""
    mapping_mode = _normalize_surface_wrap_mapping(mapping)
    frame = surface_frame(target, point_hint=point_hint, direction=direction,
                          prefer_collisions=prefer_collisions, up_hint=up_hint)
    resolved_max_edge = _resolve_surface_max_edge(surface_max_edge=surface_max_edge,
                                                  max_edge=max_edge)
    geometry = _mesh_geometry_from_subject(mesh)
    geometry = _subdivide_mesh_geometry_to_size(geometry, max_edge=resolved_max_edge)
    if not geometry.vertices:
        raise ValidationError("mesh has no vertices")

    if mapping_mode != "nearest":
        sphere_info = _subject_single_world_sphere(target, prefer_collisions=prefer_collisions)
        if sphere_info is not None:
            sphere_center, sphere_radius = sphere_info
            return _wrap_mesh_onto_sphere(geometry, frame=frame, sphere_center=sphere_center,
                                          sphere_radius=sphere_radius, child_axis=child_axis,
                                          visible_relief=float(visible_relief), spin=float(spin))
        cylinder_info = _subject_single_world_cylinder(target, prefer_collisions=prefer_collisions)
        if cylinder_info is not None:
            cc, ca, cr, cl = cylinder_info
            intrinsic = _wrap_mesh_onto_cylinder(geometry, frame=frame, cylinder_center=cc,
                                                 cylinder_axis=ca, cylinder_radius=cr,
                                                 cylinder_length=cl, child_axis=child_axis,
                                                 visible_relief=float(visible_relief),
                                                 spin=float(spin))
            if intrinsic is not None:
                return intrinsic
        if mapping_mode == "intrinsic":
            raise ValidationError(
                "Intrinsic surface wrapping is only supported for spheres and cylinder sidewalls"
            )

    return _wrap_mesh_onto_surface_nearest(geometry, frame=frame, target=target,
                                           child_axis=child_axis,
                                           visible_relief=float(visible_relief), spin=float(spin),
                                           prefer_collisions=prefer_collisions)


def wrap_profile_onto_surface(
    profile,
    target,
    *,
    thickness,
    hole_profiles=(),
    point_hint=None,
    direction=None,
    visible_relief=0.0,
    mapping="auto",
    surface_max_edge=None,
    max_edge=None,
    spin=0.0,
    asset_root=None,
    prefer_collisions=False,
    up_hint=(0.0, 0.0, 1.0),
):
    """Wrap a 2D profile with thickness directly onto a target surface."""
    thickness_value = float(thickness)
    if thickness_value <= 0.0:
        raise ValidationError("thickness must be positive")
    resolved_max_edge = _resolve_surface_max_edge(surface_max_edge=surface_max_edge,
                                                  max_edge=max_edge)
    points = _normalize_profile_points(profile)
    holes = [_normalize_profile_points(h) for h in hole_profiles]

    if holes:
        fallback = _fallback_profile_solid(points, hole_profiles=holes, thickness=thickness_value)
        return wrap_mesh_onto_surface(fallback, target, point_hint=point_hint, direction=direction,
                                      child_axis="+z", visible_relief=visible_relief, mapping=mapping,
                                      surface_max_edge=resolved_max_edge, spin=spin,
                                      prefer_collisions=prefer_collisions, up_hint=up_hint)

    cap = _planar_profile_cap(points, max_edge=resolved_max_edge)
    try:
        solid = _solid_from_planar_profile_cap(cap, boundary_profile=points,
                                               thickness=thickness_value)
        return wrap_mesh_onto_surface(solid, target, point_hint=point_hint, direction=direction,
                                      child_axis="+z", visible_relief=visible_relief, mapping=mapping,
                                      spin=spin, prefer_collisions=prefer_collisions, up_hint=up_hint)
    except ValidationError as exc:
        if "profile mesh boundary" not in str(exc):
            raise

    fallback = _fallback_profile_solid(points, hole_profiles=[], thickness=thickness_value)
    return wrap_mesh_onto_surface(fallback, target, point_hint=point_hint, direction=direction,
                                  child_axis="+z", visible_relief=visible_relief, mapping=mapping,
                                  surface_max_edge=resolved_max_edge, spin=spin,
                                  prefer_collisions=prefer_collisions, up_hint=up_hint)
