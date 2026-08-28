from articraft_cq import cq, math, MeshGeometry, _to_shape, mesh_from_geometry, ValidationError  # noqa: F401

# ===========================================================================
# @section geom-gears   (SpurGear / BevelGearPair / Worm)
#
# Clean-room CadQuery reimplementation mirroring the Apache-2.0 Articraft sdk
# gear surface (itself vendored/adapted from `cq_gears`, github meadiode/cq_gears,
# Apache License 2.0). The sdk gears are ALREADY pure parametric CadQuery B-rep
# with a GearBase/.build() API, so we mirror their cq calls exactly: each gear
# class stores parameters at construction and emits a real cq solid/compound from
# .build(). Model scripts run unchanged:
#
#     spur = SpurGear(module=0.001, teeth_number=20, width=0.006, bore_d=0.004)
#     body = cq.Workplane("XY").gear(spur).val()
#     mesh = mesh_from_cadquery(body, "spur_gear")
#
# Only the assigned names (SpurGear, BevelGearPair, Worm) plus the dependency
# classes/helpers they require (GearBase, BevelGear, math + OCP helpers, and the
# gear()/addGear() Workplane plugins) are copied here.
# ===========================================================================
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Sewing,
)
from OCP.GeomAPI import GeomAPI_IntSS
from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
from OCP.ShapeFix import ShapeFix_Face
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_HSequenceOfShape, TopTools_ListOfShape


# ---------------------------------------------------------------------------
# geometry helpers (verbatim from the sdk gear surface)
# ---------------------------------------------------------------------------
def sphere_to_cartesian(r, gamma, theta):
    """Convert spherical coordinates to cartesian."""

    return (
        r * np.sin(gamma) * np.sin(theta),
        r * np.sin(gamma) * np.cos(theta),
        r * np.cos(gamma),
    )


def s_arc(sr, c_gamma, c_theta, r_delta, start, end, n=32):
    """Get arc points plotted on a sphere's surface."""

    t = np.expand_dims(np.linspace(start, end, n), axis=1)
    a = sphere_to_cartesian(1.0, c_gamma + r_delta, c_theta)
    k = sphere_to_cartesian(1.0, c_gamma, c_theta)
    c = np.cos(t) * a + np.sin(t) * np.cross(k, a) + np.dot(k, a) * (1.0 - np.cos(t)) * k
    c = c * sr
    return [dim.squeeze() for dim in np.hsplit(c, 3)]


def s_inv(gamma0, gamma):
    """Spherical involute curve function."""

    phi = np.arccos(np.tan(gamma0) / np.tan(gamma))
    return np.arccos(np.cos(gamma) / np.cos(gamma0)) / np.sin(gamma0) - phi


def circle3d_by3points(a, b, c):
    """Find a circle in 3D space defined by three points."""

    u = b - a
    w = np.cross(c - a, u)
    u = u / np.linalg.norm(u)
    w = w / np.linalg.norm(w)
    v = np.cross(w, u)

    bx = np.dot(b - a, u)
    cx, cy = np.dot(c - a, u), np.dot(c - a, v)

    h = ((cx - bx / 2.0) ** 2 + cy**2 - (bx / 2.0) ** 2) / (2.0 * cy)
    cc = a + u * (bx / 2.0) + v * h
    r = np.linalg.norm(a - cc)

    return r, cc


def rotation_matrix(axis, alpha):
    """Construct a 3D rotation transform matrix."""

    ux, uy, uz = axis
    sina, cosa = np.sin(alpha), np.cos(alpha)
    return np.array(
        (
            (
                cosa + (1.0 - cosa) * ux**2,
                ux * uy * (1.0 - cosa) - uz * sina,
                ux * uz * (1.0 - cosa) + uy * sina,
            ),
            (
                uy * ux * (1.0 - cosa) + uz * sina,
                cosa + (1.0 - cosa) * uy**2,
                uy * uz * (1.0 - cosa) - ux * sina,
            ),
            (
                uz * ux * (1.0 - cosa) - uy * sina,
                uz * uy * (1.0 - cosa) + ux * sina,
                cosa + (1.0 - cosa) * uz**2,
            ),
        )
    )


def angle_between(o, a, b):
    """Find the angle between vectors OA and OB."""

    p = a - o
    q = b - o
    return np.arccos(np.dot(p, q) / (np.linalg.norm(p) * np.linalg.norm(q)))


def make_shell(faces, tol=1e-2):
    """Like ``cq.Shell.makeShell`` but allows an explicit tolerance."""

    shell_builder = BRepBuilderAPI_Sewing(tol)
    for face in faces:
        shell_builder.Add(face.wrapped)
    shell_builder.Perform()
    return cq.Shell(shell_builder.SewedShape())


def make_cross_section_face(faces, cut_plane, int_tol=1e-7, wire_con_tol=1e-3):
    ss = GeomAPI_IntSS()
    cps = BRepAdaptor_Surface(cut_plane.wrapped).Surface().Surface()

    curves = []
    for face in faces:
        gfs = BRepAdaptor_Surface(face.wrapped).Surface().Surface()
        ss.Perform(cps, gfs, int_tol)
        if ss.NbLines():
            for i in range(ss.NbLines()):
                curves.append(ss.Line(i + 1))

    edges = []
    for curve in curves:
        eb = BRepBuilderAPI_MakeEdge(curve)
        edges.append(eb.Edge())

    wb = BRepBuilderAPI_MakeWire()
    elist = TopTools_ListOfShape()
    for edge in edges:
        elist.Append(edge)
    wb.Add(elist)

    if not wb.IsDone():
        edges_in = TopTools_HSequenceOfShape()
        wires_out = TopTools_HSequenceOfShape()
        for edge in edges:
            edges_in.Append(edge)
        ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(
            edges_in,
            wire_con_tol,
            False,
            wires_out,
        )
        wire = TopoDS.Wire_s(wires_out.First())
    else:
        wire = wb.Wire()

    fb = BRepBuilderAPI_MakeFace(wire, True)
    face = fb.Face()
    if not cq.Face(face).isValid():
        fix = ShapeFix_Face(face)
        fix.FixOrientation()
        fix.Perform()
        face = fix.Face()
    return cq.Face(face)


# ---------------------------------------------------------------------------
# gear classes
# ---------------------------------------------------------------------------
class GearBase:
    ka = 1.0
    kd = 1.25

    curve_points = 20
    surface_splines = 5

    wire_comb_tol = 1e-2
    spline_approx_tol = 1e-2
    shell_sewing_tol = 1e-2
    isection_tol = 1e-7
    spline_approx_min_deg = 3
    spline_approx_max_deg = 8

    def __init__(self, *args, **kv_args):
        raise NotImplementedError("Constructor is not defined")

    def build(self, **kv_params):
        params = {**self.build_params, **kv_params}
        return self._build(**params)


class SpurGear(GearBase):
    def __init__(
        self,
        module,
        teeth_number,
        width,
        pressure_angle=20.0,
        helix_angle=0.0,
        clearance=0.0,
        backlash=0.0,
        addendum_coeff=None,
        dedendum_coeff=None,
        **build_params,
    ):
        if addendum_coeff is not None and addendum_coeff <= 0:
            raise ValueError("Addendum coefficient (addendum_coeff) must be greater than 0.")
        if dedendum_coeff is not None and dedendum_coeff <= 0:
            raise ValueError("Dedendum coefficient (dedendum_coeff) must be greater than 0.")

        self.ka = addendum_coeff if addendum_coeff is not None else self.ka
        self.kd = dedendum_coeff if dedendum_coeff is not None else self.kd
        self.m = m = module
        self.z = z = teeth_number
        self.a0 = a0 = np.radians(pressure_angle)
        self.clearance = clearance
        self.backlash = backlash
        self.helix_angle = np.radians(helix_angle)
        self.width = width

        d0 = m * z
        adn = self.ka / (z / d0)
        ddn = self.kd / (z / d0)

        if 2.0 * ddn + 2.0 * clearance >= d0:
            raise ValueError(
                "Invalid dedendum or clearance: resulting dedendum circle diameter is negative or zero."
            )

        da = d0 + 2.0 * adn
        dd = d0 - 2.0 * ddn - 2.0 * clearance
        s0 = m * (np.pi / 2.0 - backlash * np.tan(a0))
        inv_a0 = np.tan(a0) - a0

        self.r0 = r0 = d0 / 2.0
        self.ra = ra = da / 2.0
        self.rd = rd = dd / 2.0
        self.rb = rb = np.cos(a0) * d0 / 2.0
        self.rr = rr = max(rb, rd)
        self.tau = tau = np.pi * 2.0 / z

        if helix_angle != 0.0:
            self.twist_angle = width / (r0 * np.tan(np.pi / 2.0 - self.helix_angle))
        else:
            self.surface_splines = 2
            self.twist_angle = 0.0

        self.build_params = build_params

        r = np.linspace(rr, ra, self.curve_points)
        cos_a = r0 / r * np.cos(a0)
        a = np.arccos(np.clip(cos_a, -1.0, 1.0))
        inv_a = np.tan(a) - a
        s = r * (s0 / d0 + inv_a0 - inv_a)
        phi = s / r
        self.t_lflank_pts = np.dstack(
            (np.cos(phi) * r, np.sin(phi) * r, np.zeros(self.curve_points))
        ).squeeze()

        b = np.linspace(phi[-1], -phi[-1], self.curve_points)
        self.t_tip_pts = np.dstack(
            (np.cos(b) * ra, np.sin(b) * ra, np.zeros(self.curve_points))
        ).squeeze()

        self.t_rflank_pts = np.dstack(
            ((np.cos(-phi) * r)[::-1], (np.sin(-phi) * r)[::-1], np.zeros(self.curve_points))
        ).squeeze()

        rho = tau - phi[0] * 2.0
        p1 = np.array((self.t_rflank_pts[-1][0], self.t_rflank_pts[-1][1], 0.0))
        p2 = np.array((np.cos(-phi[0] - rho / 2.0) * rd, np.sin(-phi[0] - rho / 2.0) * rd, 0.0))
        p3 = np.array((np.cos(-phi[0] - rho) * rr, np.sin(-phi[0] - rho) * rr, 0.0))

        bcr, bcxy = circle3d_by3points(p1, p2, p3)
        t1 = np.arctan2(p1[1] - bcxy[1], p1[0] - bcxy[0])
        t2 = np.arctan2(p3[1] - bcxy[1], p3[0] - bcxy[0])
        if t1 < 0.0:
            t1 += np.pi * 2.0
        if t2 < 0.0:
            t2 += np.pi * 2.0
        t1, t2 = min(t1, t2), max(t1, t2)
        t = np.linspace(t1 + np.pi * 2.0, t2 + np.pi * 2.0, self.curve_points)
        self.t_root_pts = np.dstack(
            (bcxy[0] + bcr * np.cos(t), bcxy[1] + bcr * np.sin(t), np.zeros(self.curve_points))
        ).squeeze()

    def tooth_points(self):
        return np.concatenate(
            (self.t_lflank_pts, self.t_tip_pts, self.t_rflank_pts, self.t_root_pts)
        )

    def gear_points(self):
        tpts = self.tooth_points()
        pts = tpts.copy()
        angle = self.tau
        for _ in range(self.z - 1):
            pts = np.concatenate((pts, tpts @ rotation_matrix((0.0, 0.0, 1.0), angle)))
            angle += self.tau
        return pts

    def _build_tooth_faces(self, twist_angle_a, twist_angle_b, z_pos, width):
        surf_splines = int(np.ceil(abs(self.twist_angle) / np.pi))
        surf_splines = max(1, surf_splines) * self.surface_splines
        spline_tf = np.linspace(
            (twist_angle_a, z_pos), (twist_angle_b, z_pos + width), surf_splines
        )
        t_faces = []
        for spline in (
            self.t_lflank_pts,
            self.t_tip_pts,
            self.t_rflank_pts,
            self.t_root_pts,
        ):
            face_pts = []
            for a, z in spline_tf:
                r_mat = rotation_matrix((0.0, 0.0, 1.0), a)
                pts = spline.copy()
                pts[:, 2] = z
                pts = pts @ r_mat
                face_pts.append([cq.Vector(*pt) for pt in pts])
            face = cq.Face.makeSplineApprox(
                face_pts,
                tol=self.spline_approx_tol,
                minDeg=self.spline_approx_min_deg,
                maxDeg=self.spline_approx_max_deg,
            )
            t_faces.append(face)
        return t_faces

    def _build_gear_faces(self):
        t_faces = self._build_tooth_faces(0.0, self.twist_angle, 0.0, self.width)
        faces = []
        for i in range(self.z):
            for tf in t_faces:
                faces.append(
                    tf.rotate(
                        (0.0, 0.0, 0.0),
                        (0.0, 0.0, 1.0),
                        np.degrees(self.tau * i),
                    )
                )
        wp = cq.Workplane("XY").add(faces)
        topface_wires = cq.Wire.combine(wp.edges("<Z").vals(), tol=self.wire_comb_tol)
        topface = cq.Face.makeFromWires(topface_wires[0])
        botface_wires = cq.Wire.combine(wp.edges(">Z").vals(), tol=self.wire_comb_tol)
        botface = cq.Face.makeFromWires(botface_wires[0])
        wp = wp.add(topface).add(botface)
        return wp.vals()

    def _make_bore(self, body, bore_d):
        if bore_d is None:
            return body
        return (
            cq.Workplane("XY")
            .add(body)
            .faces("<Z")
            .workplane()
            .circle(bore_d / 2.0)
            .cutThruAll()
            .val()
        )

    def _make_teeth_cutout_wire(self, plane, t1, t2, twist_angle):
        at1 = t1 * self.tau + self.tau / 2.0 + twist_angle
        at2 = t2 * self.tau + self.tau / 2.0 + twist_angle
        p1x = np.cos(at1)
        p1y = np.sin(at1)
        p2x = np.cos((at1 + at2) / 2.0)
        p2y = np.sin((at1 + at2) / 2.0)
        p3x = np.cos(at2)
        p3y = np.sin(at2)
        rc = self.ra + 1.0
        rd = self.rd - 0.01
        return (
            plane.moveTo(p1x * rd, p1y * rd)
            .lineTo(p1x * rc, p1y * rc)
            .threePointArc((p2x * rc, p2y * rc), (p3x * rc, p3y * rc))
            .lineTo(p3x * rd, p3y * rd)
            .threePointArc((p2x * rd, p2y * rd), (p1x * rd, p1y * rd))
            .close()
        )

    def _remove_teeth(self, body, t1, t2):
        plane = cq.Workplane("XY").workplane(offset=-0.1).add(body)
        if self.twist_angle == 0.0:
            cutout = self._make_teeth_cutout_wire(plane, t1, t2, 0.0).extrude(
                self.width + 0.2,
                combine=False,
            )
        else:
            cutout = self._make_teeth_cutout_wire(plane, t1, t2, 0.0).twistExtrude(
                self.width + 0.2,
                np.degrees(-self.twist_angle),
                combine=False,
            )
        return cq.Workplane("XY").add(body).cut(cutout).val()

    def _make_missing_teeth(self, body, missing_teeth):
        if missing_teeth is None:
            return body
        if isinstance(missing_teeth[0], (list, tuple)):
            for t1, t2 in missing_teeth:
                body = self._remove_teeth(body, t1, t2)
        else:
            t1, t2 = missing_teeth
            body = self._remove_teeth(body, t1, t2)
        return body

    def _make_recess(
        self,
        body,
        hub_d,
        recess_d,
        recess=None,
        bottom_recess=None,
        bottom_hub_d=None,
        bottom_recess_d=None,
    ):
        if recess is None and bottom_recess is None:
            return body
        if recess is not None:
            assert recess_d is not None, "Top face recess diameter is not set"
        if bottom_recess is not None:
            assert bottom_recess_d is not None or recess_d is not None, (
                "Bottom face recess diameter is not set"
            )
        if recess:
            body = cq.Workplane("XY").add(body).faces(">Z").workplane()
            if hub_d is not None:
                body = body.circle(hub_d / 2.0)
            body = body.circle(recess_d / 2.0).cutBlind(-recess).val()
        if bottom_recess:
            body = cq.Workplane("XY").add(body).faces("<Z").workplane()
            if bottom_hub_d is None:
                bottom_hub_d = hub_d
            if bottom_recess_d is None:
                bottom_recess_d = recess_d
            if bottom_hub_d is not None:
                body = body.circle(bottom_hub_d / 2.0)
            body = body.circle(bottom_recess_d / 2.0).cutBlind(-bottom_recess).val()
        return body

    def _make_hub(self, body, hub_d, hub_length, bore_d):
        if hub_length is None:
            return body
        assert hub_d is not None, "Hub diameter is not set"
        body = cq.Workplane("XY").add(body).faces(">Z").workplane()
        if bore_d is not None:
            body = body.circle(bore_d / 2.0)
        body = body.circle(hub_d / 2.0).extrude(hub_length)
        return body.val()

    def _make_spokes(self, body, spokes_id, spokes_od, n_spokes, spoke_width, spoke_fillet):
        if n_spokes is None:
            return body
        assert n_spokes > 1, "Number of spokes must be > 1"
        assert spoke_width is not None, "Spoke width is not set"
        assert spokes_od is not None, "Outer spokes diameter is not set"
        if spokes_id is None:
            r1 = spoke_width / 2.0
        else:
            r1 = max(spoke_width / 2.0, spokes_id / 2.0)
        r2 = spokes_od / 2.0
        r1 += 0.0001
        r2 -= 0.0001
        tau = np.pi * 2.0 / n_spokes
        a1 = np.arcsin((spoke_width / 2.0) / (spokes_id / 2.0))
        a2 = np.arcsin((spoke_width / 2.0) / (spokes_od / 2.0))
        a3 = tau - a2
        a4 = tau - a1
        cutout = (
            cq.Workplane("XY")
            .workplane(offset=-0.1)
            .moveTo(np.cos(a1) * r1, np.sin(a1) * r1)
            .lineTo(np.cos(a2) * r2, np.sin(a2) * r2)
            .radiusArc((np.cos(a3) * r2, np.sin(a3) * r2), -r2)
            .lineTo(np.cos(a4) * r1, np.sin(a4) * r1)
            .radiusArc((np.cos(a1) * r1, np.sin(a1) * r1), r1)
            .close()
            .extrude(self.width + 1.0)
        )
        if spoke_fillet is not None:
            cutout = cutout.edges("|Z").fillet(spoke_fillet)
        body = cq.Workplane("XY").add(body)
        for i in range(n_spokes):
            body = body.cut(
                cutout.rotate(
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, 1.0),
                    np.degrees(tau * i),
                )
            )
        return body.val()

    def _make_chamfer(self, body, chamfer=None, chamfer_top=None, chamfer_bottom=None):
        e = 0.01
        if chamfer is None and chamfer_top is None and chamfer_bottom is None:
            return body
        if chamfer is not None:
            if chamfer_top is None:
                chamfer_top = chamfer
            if chamfer_bottom is None:
                chamfer_bottom = chamfer
        if chamfer_top is not None:
            if isinstance(chamfer_top, (list, tuple)):
                wx, wy = chamfer_top
            else:
                wx, wy = chamfer_top, chamfer_top
            cutter = (
                cq.Workplane("XZ")
                .moveTo(self.ra - wx, self.width + e)
                .hLine(wx + e)
                .vLine(-wy - e)
                .close()
                .revolve()
            )
            body = cq.Workplane("XY").add(body).cut(cutter)
        if chamfer_bottom is not None:
            if isinstance(chamfer_bottom, (list, tuple)):
                wx, wy = chamfer_bottom
            else:
                wx, wy = chamfer_bottom, chamfer_bottom
            cutter = (
                cq.Workplane("XZ")
                .moveTo(self.ra + e, wy)
                .vLine(-wy - e)
                .hLine(-wx - e)
                .close()
                .revolve()
            )
            body = cq.Workplane("XY").add(body).cut(cutter)
        return body.val()

    def _build(
        self,
        bore_d=None,
        missing_teeth=None,
        hub_d=None,
        hub_length=None,
        recess_d=None,
        recess=None,
        bottom_recess=None,
        bottom_recess_d=None,
        bottom_hub_d=None,
        n_spokes=None,
        spoke_width=None,
        spoke_fillet=None,
        spokes_id=None,
        spokes_od=None,
        chamfer=None,
        chamfer_top=None,
        chamfer_bottom=None,
        *args,
        **kv_args,
    ):
        faces = self._build_gear_faces()
        shell = make_shell(faces, tol=self.shell_sewing_tol)
        body = cq.Solid.makeSolid(shell)
        body = self._make_chamfer(body, chamfer, chamfer_top, chamfer_bottom)
        body = self._make_bore(body, bore_d)
        body = self._make_missing_teeth(body, missing_teeth)
        body = self._make_recess(
            body,
            hub_d,
            recess_d,
            recess,
            bottom_recess=bottom_recess,
            bottom_hub_d=bottom_hub_d,
            bottom_recess_d=bottom_recess_d,
        )
        body = self._make_hub(body, hub_d, hub_length, bore_d)
        if spokes_id is None:
            spokes_id = hub_d
        if spokes_od is None:
            spokes_od = recess_d
        body = self._make_spokes(body, spokes_id, spokes_od, n_spokes, spoke_width, spoke_fillet)
        return body


class BevelGear(GearBase):
    surface_splines = 12

    def __init__(
        self,
        module,
        teeth_number,
        cone_angle,
        face_width,
        pressure_angle=20.0,
        helix_angle=0.0,
        clearance=0.0,
        backlash=0.0,
        **build_params,
    ):
        self.m = m = module
        self.z = z = teeth_number
        self.a0 = a0 = np.radians(pressure_angle)
        self.clearance = clearance
        self.backlash = backlash
        self.helix_angle = np.radians(helix_angle)
        self.face_width = face_width

        self.gamma_p = gamma_p = np.radians(cone_angle)
        rp = m * z / 2.0
        self.gs_r = gs_r = rp / np.sin(gamma_p)
        assert gs_r > face_width, f"face_width value is too big, it should be < {gs_r:0.3f}"
        self.gamma_b = gamma_b = np.arcsin(np.cos(a0) * np.sin(gamma_p))
        self.gamma_f = gamma_f = gamma_p + np.arctan(self.ka * m / gs_r)
        self.gamma_r = gamma_r = gamma_p - np.arctan(self.kd * m / gs_r)
        self.tau = tau = np.pi * 2.0 / z

        if helix_angle != 0.0:
            beta = np.arctan(face_width * np.tan(self.helix_angle) / (2.0 * gs_r - face_width))
            self.twist_angle = np.arcsin(gs_r / rp * np.sin(beta)) * 2.0
        else:
            self.surface_splines = 2
            self.twist_angle = 0.0
        assert not np.isnan(self.twist_angle), "Twist angle is NaN"

        self.build_params = build_params
        self.cone_h = np.cos(gamma_r) * gs_r
        phi_r = s_inv(gamma_b, gamma_p)
        self.mp_theta = mp_theta = np.pi / z + 2.0 * phi_r

        gamma_tr = max(gamma_b, gamma_r)
        gamma = np.linspace(gamma_tr, gamma_f, self.curve_points)
        theta = s_inv(gamma_b, gamma) + backlash / (module * teeth_number)
        self.t_lflank_pts = np.dstack(sphere_to_cartesian(1.0, gamma, theta)).squeeze()

        theta_tip = np.linspace(theta[-1], mp_theta - theta[-1], self.curve_points)
        self.t_tip_pts = np.dstack(
            sphere_to_cartesian(1.0, np.full(self.curve_points, gamma_f), theta_tip)
        ).squeeze()

        self.t_rflank_pts = np.dstack(
            sphere_to_cartesian(1.0, gamma[::-1], mp_theta - theta[::-1])
        ).squeeze()

        if gamma_r < gamma_b:
            p1 = self.t_rflank_pts[-1]
            p2 = np.array(sphere_to_cartesian(1.0, gamma_b, theta[0] + tau))
            p3 = np.array(sphere_to_cartesian(1.0, gamma_r, (tau + mp_theta) / 2.0))
            rr, rcc = circle3d_by3points(p1, p2, p3)
            rcc_gamma = np.arccos(np.dot(p3, rcc) / (np.linalg.norm(p3) * np.linalg.norm(rcc)))
            p1p3 = angle_between(rcc, p1, p3)
            a_start = (np.pi - p1p3 * 2.0) / 2.0
            a_end = -a_start + np.pi
            self.t_root_pts = np.dstack(
                s_arc(
                    1.0,
                    gamma_r + rcc_gamma,
                    (tau + mp_theta) / 2.0,
                    rcc_gamma,
                    np.pi / 2.0 + a_start,
                    np.pi / 2.0 + a_end,
                    self.curve_points,
                )
            ).squeeze()
        else:
            r_theta = np.linspace(mp_theta - theta[0], theta[0] + tau, self.curve_points)
            self.t_root_pts = np.dstack(
                sphere_to_cartesian(1.0, np.full(self.curve_points, gamma_tr), r_theta)
            ).squeeze()

    def tooth_points(self):
        return np.concatenate(
            (self.t_lflank_pts, self.t_tip_pts, self.t_rflank_pts, self.t_root_pts)
        )

    def gear_points(self):
        tpts = self.tooth_points()
        pts = tpts.copy()
        angle = self.tau
        for _ in range(self.z - 1):
            pts = np.concatenate((pts, tpts @ rotation_matrix((0.0, 0.0, 1.0), angle)))
            angle += self.tau
        return pts

    def _build_tooth_faces(self):
        pc_h = np.cos(self.gamma_r) * self.gs_r
        pc_f = pc_h / np.cos(self.gamma_f)
        pc_rb = pc_f * np.sin(self.gamma_f)
        tc_h = np.cos(self.gamma_f) * (self.gs_r - self.face_width)
        tc_f = tc_h / np.cos(self.gamma_r)
        tc_rb = tc_f * np.sin(self.gamma_f)
        ta1 = -(pc_f - self.gs_r) / self.face_width * self.twist_angle
        ta2 = (self.gs_r - tc_f) / self.face_width * self.twist_angle

        surf_splines = int(np.ceil(abs(self.twist_angle) / (np.pi * 2.0)))
        surf_splines = max(1, surf_splines) * self.surface_splines
        spline_tf = np.linspace((pc_f, ta1), (tc_f - 0.01, ta2), surf_splines)

        tcp_size = tc_rb * 1000.0
        top_cut_plane = cq.Face.makePlane(
            length=tcp_size, width=tcp_size, basePnt=(0.0, 0.0, tc_h), dir=(0.0, 0.0, 1.0)
        )
        bcp_size = pc_rb * 1000.0
        bott_cut_plane = cq.Face.makePlane(
            length=bcp_size, width=bcp_size, basePnt=(0.0, 0.0, pc_h), dir=(0.0, 0.0, 1.0)
        )

        def get_zmax(face):
            return face.BoundingBox().zmax

        t_faces = []
        for spline in (
            self.t_lflank_pts,
            self.t_tip_pts,
            self.t_rflank_pts,
            self.t_root_pts,
        ):
            face_pts = []
            for r, a in spline_tf:
                r_mat = rotation_matrix((0.0, 0.0, 1.0), a)
                pts = (spline @ r_mat) * r
                face_pts.append([cq.Vector(*pt) for pt in pts])
            face = cq.Face.makeSplineApprox(
                face_pts,
                tol=self.spline_approx_tol,
                minDeg=self.spline_approx_min_deg,
                maxDeg=self.spline_approx_max_deg,
            )
            cpd = face.split(top_cut_plane)
            face = max(list(cpd), key=get_zmax) if isinstance(cpd, cq.Compound) else cpd
            cpd = face.split(bott_cut_plane)
            face = min(list(cpd), key=get_zmax) if isinstance(cpd, cq.Compound) else cpd
            t_faces.append(face)
        return t_faces

    def _build_gear_faces(self):
        t_faces = self._build_tooth_faces()
        faces = []
        for i in range(self.z):
            for tf in t_faces:
                faces.append(
                    tf.rotate(
                        (0.0, 0.0, 0.0),
                        (0.0, 0.0, 1.0),
                        np.degrees(self.tau * i),
                    )
                )
        wp = cq.Workplane("XY").add(faces)
        topface_wires = cq.Wire.combine(wp.edges("<Z").vals(), tol=self.wire_comb_tol)
        topface = cq.Face.makeFromWires(topface_wires[0])
        botface_wires = cq.Wire.combine(wp.edges(">Z").vals(), tol=self.wire_comb_tol)
        botface = cq.Face.makeFromWires(botface_wires[0])
        wp = wp.add(topface).add(botface)
        return wp.vals()

    def _trim_bottom(self, body, do_trim=False):
        if not do_trim:
            return body
        r = self.gs_r
        p1 = sphere_to_cartesian(r, self.gamma_r * 0.99, np.pi / 2.0)
        p2 = sphere_to_cartesian(r, self.gamma_p, np.pi / 2.0)
        p3 = sphere_to_cartesian(r, self.gamma_f * 1.01, np.pi / 2.0)
        x1 = np.tan(self.gamma_f) * self.cone_h + 1.0
        trimmer = (
            cq.Workplane("XZ")
            .moveTo(p1[0], p1[2])
            .threePointArc((p2[0], p2[2]), (p3[0], p3[2]))
            .lineTo(x1, p3[2])
            .lineTo(x1, p1[2])
            .close()
            .revolve(combine=False)
        )
        return cq.Workplane("XY").add(body).cut(trimmer).val()

    def _trim_top(self, body, do_trim=False):
        if not do_trim:
            return body
        r = self.gs_r - self.face_width
        p1 = sphere_to_cartesian(r, self.gamma_r, np.pi / 2.0)
        p2 = sphere_to_cartesian(r, self.gamma_p, np.pi / 2.0)
        p3 = sphere_to_cartesian(r, self.gamma_f * 1.01, np.pi / 2.0)
        trimmer = (
            cq.Workplane("XZ")
            .moveTo(p1[0], p1[2])
            .threePointArc((p2[0], p2[2]), (p3[0], p3[2]))
            .lineTo(0.0, p3[2])
            .lineTo(0.0, p1[2])
            .close()
            .revolve(combine=False)
        )
        return cq.Workplane("XY").add(body).cut(trimmer).val()

    def _make_bore(self, body, bore_d):
        if bore_d is None:
            return body
        return (
            cq.Workplane("XY")
            .add(body)
            .faces("<Z")
            .workplane()
            .circle(bore_d / 2.0)
            .cutThruAll()
            .val()
        )

    def _build(self, bore_d=None, trim_bottom=True, trim_top=True, **kv_args):
        faces = self._build_gear_faces()
        shell = make_shell(faces)
        body = cq.Solid.makeSolid(shell)
        body = self._trim_bottom(body, trim_bottom)
        body = self._trim_top(body, trim_top)
        t_align_angle = -self.mp_theta / 2.0 - np.pi / 2.0 + np.pi / self.z
        body = (
            cq.Workplane("XY")
            .add(body)
            .rotate((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 180.0)
            .translate((0.0, 0.0, self.cone_h))
            .rotate((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), np.degrees(t_align_angle))
            .solids()
            .val()
        )
        return self._make_bore(body, bore_d)


class BevelGearPair(GearBase):
    gear_cls = BevelGear

    asm_gear_color = "goldenrod"
    asm_pinion_color = "lightsteelblue"

    def __init__(
        self,
        module,
        gear_teeth,
        pinion_teeth,
        face_width,
        axis_angle=90.0,
        pressure_angle=20.0,
        helix_angle=0.0,
        clearance=0.0,
        backlash=0.0,
        **build_params,
    ):
        self.axis_angle = axis_angle = np.radians(axis_angle)
        aa_sin = np.sin(axis_angle)
        aa_cos = np.cos(axis_angle)
        delta_gear = np.arctan(aa_sin / (pinion_teeth / gear_teeth + aa_cos))
        delta_pinion = np.arctan(aa_sin / (gear_teeth / pinion_teeth + aa_cos))
        self.gear = self.gear_cls(
            module,
            gear_teeth,
            np.degrees(delta_gear),
            face_width,
            pressure_angle,
            helix_angle,
            clearance,
            backlash,
        )
        self.pinion = self.gear_cls(
            module,
            pinion_teeth,
            np.degrees(delta_pinion),
            face_width,
            pressure_angle,
            -helix_angle,
            backlash=backlash,
        )
        self.build_params = build_params

    def assemble(
        self,
        build_gear=True,
        build_pinion=True,
        transform_pinion=True,
        gear_build_args={},
        pinion_build_args={},
        **kv_args,
    ):
        gearset = cq.Assembly(name="bevel_pair")
        if build_gear:
            in_args = self.build_params.get("gear_build_args", {})
            args = {**self.build_params, **in_args, **kv_args, **gear_build_args}
            gear = self.gear.build(**args)
            gearset.add(gear, name="gear", loc=cq.Location(), color=cq.Color(self.asm_gear_color))
        if build_pinion:
            in_args = self.build_params.get("pinion_build_args", {})
            args = {**self.build_params, **in_args, **kv_args, **pinion_build_args}
            pinion = self.pinion.build(**args)
            loc = cq.Location()
            if transform_pinion:
                loc *= cq.Location(
                    cq.Vector(0.0, 0.0, self.gear.cone_h),
                    cq.Vector(0.0, 1.0, 0.0),
                    np.degrees(self.axis_angle),
                )
                loc *= cq.Location(cq.Vector((0.0, 0.0, -self.pinion.cone_h)))
                if self.pinion.z % 2 == 0:
                    loc *= cq.Location(
                        cq.Vector(0.0, 0.0, 0.0),
                        cq.Vector(0.0, 0.0, 1.0),
                        np.degrees(np.pi / self.pinion.z),
                    )
            gearset.add(pinion, name="pinion", loc=loc, color=cq.Color(self.asm_pinion_color))
        return gearset

    def _build(self, *args, **kv_args):
        return self.assemble(*args, **kv_args).toCompound()


class Worm(GearBase):
    surface_splines = 8
    wire_comb_tol = 0.1
    t_face_parts = 2

    def __init__(
        self,
        module,
        lead_angle,
        n_threads,
        length,
        pressure_angle=20.0,
        clearance=0.0,
        backlash=0.0,
        **build_params,
    ):
        self.m = m = module
        self.a0 = a0 = np.radians(pressure_angle)
        self.clearance = clearance
        self.backlash = backlash
        self.lead_angle = np.radians(lead_angle)
        self.length = length
        self.n_threads = n_threads
        self.build_params = build_params

        d0 = self.n_threads * m / np.abs(np.tan(self.lead_angle))
        self.r0 = d0 / 2.0
        adn = self.ka * m
        ddn = self.kd * m
        self.la = la = adn
        self.ld = ld = -(ddn + clearance)
        self.ra = self.r0 + adn
        self.rd = self.r0 - ddn

        s0 = m * (np.pi / 2.0 - backlash * np.tan(a0)) / 2.0
        p1x = np.tan(a0) * abs(ld)
        p1p2 = (abs(la) + abs(ld)) / np.cos(a0)
        p1 = (-s0 - p1x, ld, 0.0)
        p2 = (np.sin(a0) * p1p2 + p1[0], np.cos(a0) * p1p2 + p1[1], 0.0)
        p3 = (-p2[0], p2[1], 0.0)
        p4 = (-p1[0], p1[1], 0.0)
        p5 = (p4[0] + (np.pi * m - p4[0] * 2.0), p4[1], 0.0)
        self.t_lflank_pts = np.array((p1, p2))
        self.t_tip_pts = np.array((p2, p3))
        self.t_rflank_pts = np.array((p3, p4))
        self.t_root_pts = np.array((p4, p5))
        self.tooth_height = abs(la) + abs(ld)

    def tooth_points(self):
        return np.concatenate(
            (self.t_lflank_pts, self.t_tip_pts, self.t_rflank_pts, self.t_root_pts)
        )

    def gear_points(self):
        tpts = self.tooth_points()
        pts = tpts.copy()
        for i in range(10):
            ttpts = tpts.copy()
            ttpts[:, 0] += np.pi * self.m * (i + 1)
            pts = np.concatenate((pts, ttpts))
        return pts

    def _build_tooth_faces(self):
        t_faces = []
        ttx = self.m * np.pi * self.n_threads
        start_x = -ttx / 2.0
        step_x = ttx / self.t_face_parts
        part_turn = np.pi * 2.0 / self.t_face_parts * np.sign(self.lead_angle)
        spline_tf = np.linspace((start_x, 0.0), (start_x + step_x, part_turn), self.surface_splines)
        for spline in (
            self.t_lflank_pts,
            self.t_tip_pts,
            self.t_rflank_pts,
            self.t_root_pts,
        ):
            face_pts = []
            for tx, alpha in spline_tf:
                r_mat = rotation_matrix((1.0, 0.0, 0.0), alpha)
                pts = (spline + (tx, self.r0, 0.0)) @ r_mat
                face_pts.append([cq.Vector(*pt) for pt in pts])
            face = cq.Face.makeSplineApprox(
                face_pts,
                tol=self.spline_approx_tol,
                minDeg=self.spline_approx_min_deg,
                maxDeg=self.spline_approx_max_deg,
            )
            t_faces.append(face)
        faces = []
        for n in range(self.t_face_parts):
            for tf in t_faces:
                faces.append(
                    tf.rotate(
                        (0.0, 0.0, 0.0),
                        (1.0, 0.0, 0.0),
                        np.degrees(-n * part_turn),
                    ).translate((step_x * n, 0.0, 0.0))
                )
        return faces

    def _build_gear_faces(self):
        step = np.pi * self.m * self.n_threads
        turns = int(np.ceil(self.length / step)) + 2
        x_start = -turns * step / 2.0
        tau = np.pi * 2.0 / self.n_threads
        t_faces = self._build_tooth_faces()
        faces = []
        for th in range(self.n_threads):
            for tf in t_faces:
                faces.append(
                    tf.rotate(
                        (0.0, 0.0, 0.0),
                        (1.0, 0.0, 0.0),
                        np.degrees(tau * th),
                    )
                )
        nfaces = []
        for i in range(turns):
            for tf in faces:
                nfaces.append(tf.translate((step / 2.0 + x_start + i * step, 0.0, 0.0)))

        cp_size = self.ra * 2.0 + 2.0
        cp_x = self.length / 2.0
        left_cut_plane = cq.Face.makePlane(
            length=cp_size, width=cp_size, basePnt=(-cp_x, 0.0, 0.0), dir=(-1.0, 0.0, 0.0)
        )
        right_cut_plane = cq.Face.makePlane(
            length=cp_size, width=cp_size, basePnt=(cp_x, 0.0, 0.0), dir=(1.0, 0.0, 0.0)
        )
        lface = make_cross_section_face(
            nfaces, left_cut_plane, self.isection_tol, self.wire_comb_tol
        )
        rface = make_cross_section_face(
            nfaces, right_cut_plane, self.isection_tol, self.wire_comb_tol
        )

        def get_xmin(face):
            return face.BoundingBox().xmin

        def get_xmax(face):
            return face.BoundingBox().xmax

        g_faces = []
        for face in nfaces:
            bb = face.BoundingBox()
            if -(self.length / 2.0) < bb.xmin and bb.xmax < (self.length / 2.0):
                g_faces.append(face)
            else:
                cpd = face.split(left_cut_plane)
                if isinstance(cpd, cq.Compound):
                    g_faces.append(max(list(cpd), key=get_xmax))
                cpd = face.split(right_cut_plane)
                if isinstance(cpd, cq.Compound):
                    g_faces.append(min(list(cpd), key=get_xmin))
        g_faces.append(lface)
        g_faces.append(rface)
        return g_faces

    def _make_bore(self, body, bore_d):
        if bore_d is None:
            return body
        return (
            cq.Workplane("YZ")
            .add(body)
            .faces("<X")
            .workplane()
            .circle(bore_d / 2.0)
            .cutThruAll()
            .val()
        )

    def _build(self, bore_d=None):
        faces = self._build_gear_faces()
        shell = make_shell(faces, tol=self.shell_sewing_tol)
        body = cq.Solid.makeSolid(shell)
        return self._make_bore(body, bore_d)


# ---------------------------------------------------------------------------
# Workplane plugin helpers (importing this module installs cq.Workplane.gear /
# cq.Workplane.addGear, exactly like the sdk gear surface).
# ---------------------------------------------------------------------------
def gear(self, gear_, *build_args, **build_kv_args):
    gear_body = gear_.build(*build_args, **build_kv_args)
    return self.eachpoint(lambda loc: gear_body.located(loc), True)


def addGear(self, gear_, *build_args, **build_kv_args):
    return self.union(gear(self, gear_, *build_args, **build_kv_args))


cq.Workplane.gear = gear
cq.Workplane.addGear = addGear
