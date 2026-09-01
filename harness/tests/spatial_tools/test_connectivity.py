from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh

from codeverse.contracts.artifacts import Severity
from codeverse.spatial.connectivity import (
    PENETRATION_ERROR_M,
    PENETRATION_MIN_FRACTION,
    THROUGH_FAR_SIDE_RATIO,
    check_connectivity,
    pair_distance,
    penetration_depth,
)
from tests.spatial_tools.conftest import FLOAT_GAP_M


def test_floating_leg_found_with_exact_gap(stool_glb: Path) -> None:
    r = check_connectivity(stool_glb)
    assert r.gate == "connectivity" and not r.passed
    errs = r.errors
    assert len(errs) == 1 and errs[0].target == "Leg_3"
    d = errs[0].data
    assert d["nearest"] == "Seat"
    assert d["gap_m"] == pytest.approx(FLOAT_GAP_M, abs=1e-4)
    assert np.allclose(d["gap_vector_m"], (0.0, FLOAT_GAP_M, 0.0), atol=1e-4)
    assert "translate 'Leg_3' by (+0.0000, +0.0050, +0.0000) m (GLB frame: Y-up, +Z front)" in errs[0].fix_hint


def test_hints_use_the_authoring_frame(stool_glb: Path) -> None:
    """The 5 mm gap is along GLB +y (up).  A Blender/CadQuery/URDF agent codes in Z-up, so
    the pasted hint must say +z (a literal '+y' would move the leg towards the back)."""
    err = check_connectivity(stool_glb, language="blender").errors[0]
    assert "translate 'Leg_3' by (+0.0000, +0.0000, +0.0050) m (blender frame: Z-up, -Y front)" in err.fix_hint
    assert np.allclose(err.data["gap_vector_m"], (0.0, 0.0, FLOAT_GAP_M), atol=1e-4)
    assert np.allclose(err.data["gap_vector_glb_m"], (0.0, FLOAT_GAP_M, 0.0), atol=1e-4)
    assert err.data["frame"] == "z_up_neg_y_front"
    err = check_connectivity(stool_glb, language="threejs").errors[0]
    assert "(+0.0000, +0.0050, +0.0000) m (threejs frame: Y-up, +Z front)" in err.fix_hint
    # nothing grounded: the ground hint names the language's up axis
    s = trimesh.Scene()
    for i, x in enumerate((0.0, 0.3)):
        m = trimesh.creation.box((0.1, 0.1, 0.1))
        m.apply_translation((x, 0.5, 0))
        s.add_geometry(m, node_name=f"P{i}", geom_name=f"P{i}")
    p = stool_glb.parent / "floating_pair.glb"
    p.write_bytes(s.export(file_type="glb"))
    ground = [f for f in check_connectivity(p, language="cadquery").findings if "touches the ground" in f.message]
    assert ground and "(z=0)" in ground[0].message and "z=0, cadquery frame" in ground[0].fix_hint


def test_solid_stool_passes(solid_stool_glb: Path) -> None:
    r = check_connectivity(solid_stool_glb)
    assert r.passed and not r.errors
    assert any("connected" in f.message for f in r.findings)


def test_penetration_is_flagged(tmp_path: Path) -> None:
    sc = trimesh.Scene()
    a = trimesh.creation.box(extents=(0.2, 0.2, 0.2))
    a.apply_translation((0, 0.1, 0))
    b = trimesh.creation.box(extents=(0.2, 0.2, 0.2))
    b.apply_translation((0, 0.1 + 0.2 - 0.03, 0))  # 30 mm deep into A
    sc.add_geometry(a, node_name="A", geom_name="A")
    sc.add_geometry(b, node_name="B", geom_name="B")
    p = tmp_path / "pen.glb"
    sc.export(str(p))
    r = check_connectivity(p)
    pen = [f for f in r.findings if "interpenetrate" in f.message]
    assert pen and pen[0].severity == Severity.ERROR
    assert pen[0].data["depth_m"] == pytest.approx(0.03, abs=0.004)
    assert pen[0].data["kind"] == "penetration" and {pen[0].target, pen[0].data["other"]} == {"A", "B"}
    assert {"depth_m", "fraction_inside", "local_fraction", "inside_count", "through_ratio", "thickness_m",
            "container", "entering"} <= set(pen[0].data)
    assert pen[0].data["thickness_m"] == pytest.approx(0.2, abs=1e-6)


def test_hairline_overlap_is_fine(tmp_path: Path) -> None:
    sc = trimesh.Scene()
    a = trimesh.creation.box(extents=(0.2, 0.2, 0.2))
    a.apply_translation((0, 0.1, 0))
    b = trimesh.creation.box(extents=(0.1, 0.1, 0.1))
    b.apply_translation((0, 0.2 + 0.05 - 0.001, 0))  # 1 mm weld overlap
    sc.add_geometry(a, node_name="A", geom_name="A")
    sc.add_geometry(b, node_name="B", geom_name="B")
    p = tmp_path / "weld.glb"
    sc.export(str(p))
    r = check_connectivity(p)
    assert r.passed
    assert not [f for f in r.findings if "interpenetrate" in f.message]


def test_tiny_island_warns(tmp_path: Path) -> None:
    body = trimesh.creation.box(extents=(0.3, 0.3, 0.3))
    body.apply_translation((0, 0.15, 0))
    crumb = trimesh.creation.box(extents=(0.004, 0.004, 0.004))
    crumb.apply_translation((0.5, 0.5, 0.5))
    part = trimesh.util.concatenate([body, crumb])
    sc = trimesh.Scene()
    sc.add_geometry(part, node_name="Body", geom_name="Body")
    p = tmp_path / "crumb.glb"
    sc.export(str(p))
    r = check_connectivity(p)
    warns = [f for f in r.findings if f.severity == Severity.WARN]
    assert warns and "tiny disconnected island" in warns[0].message


def test_missing_glb_is_error(tmp_path: Path) -> None:
    r = check_connectivity(tmp_path / "none.glb")
    assert not r.passed and r.errors


def test_pair_distance_sampled_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import codeverse.spatial.joints_collide as collide

    monkeypatch.setattr(collide, "_fcl", None)
    a = trimesh.creation.box(extents=(1, 1, 1))
    b = trimesh.creation.box(extents=(1, 1, 1))
    b.apply_translation((1.02, 0, 0))
    pd = pair_distance("a", a, "b", b)
    assert pd.distance == pytest.approx(0.02, abs=1e-6)
    assert pd.gap_vector[0] == pytest.approx(0.02, abs=1e-6)


def _open_bottom_box(extents, translation) -> trimesh.Trimesh:
    """A box with its bottom face dropped — an open shell, what agents actually export."""
    m = trimesh.creation.box(extents=extents)
    m.apply_translation(translation)
    low = m.vertices[m.faces].mean(axis=1)[:, 1]
    shell = trimesh.Trimesh(vertices=m.vertices, faces=m.faces[low > low.min() + 1e-9], process=False)
    assert not shell.is_watertight
    return shell


def test_interpenetration_is_found_between_two_non_watertight_parts(tmp_path: Path) -> None:
    """CG-2: penetration_depth skipped any direction whose target was not watertight
    (``Trimesh.contains`` needs one), so when NEITHER part was watertight it returned
    (0.0, 0.0) — indistinguishable from "no overlap", with no warning that the check had
    not run.  A 0.2 m post driven 50 mm into a base then passed the gate as soon as both
    were modelled as open shells; only the mixed closed/open case still fired, which is
    why the shipped tests stayed green."""
    base_ext, post_ext = (0.4, 0.2, 0.4), (0.2, 0.2, 0.2)
    base_at, post_at = (0, 0.1, 0), (0, 0.25, 0)  # post bottom 50 mm below the base's top face
    closed = (trimesh.creation.box(extents=base_ext), trimesh.creation.box(extents=post_ext))
    closed[0].apply_translation(base_at)
    closed[1].apply_translation(post_at)
    shells = (_open_bottom_box(base_ext, base_at), _open_bottom_box(post_ext, post_at))

    for tag, (a, b) in (("closed", closed), ("open shells", shells)):
        depth, frac = penetration_depth(a, b)
        assert depth == pytest.approx(0.05, abs=1e-3), f"{tag}: 50 mm of interpenetration must be measured"
        assert frac > 0.05, tag

    scene = trimesh.Scene()
    scene.add_geometry(shells[0], node_name="Base", geom_name="Base")
    scene.add_geometry(shells[1], node_name="Post", geom_name="Post")
    glb = tmp_path / "open_shells.glb"
    scene.export(str(glb))
    r = check_connectivity(glb)
    assert not r.passed and any("interpenetrate" in f.message for f in r.errors)
def _stool_with_a_leg_short_at_the_top(path: Path, gap_m: float = FLOAT_GAP_M) -> Path:
    """The stool, but Leg_3 is shortened by ``gap_m`` at the TOP only.

    It still stands on the floor and is ``gap_m`` shy of the seat — the single
    commonest static-object defect, and the one CG-1 waved through."""
    from tests.spatial_tools.conftest import LEG_XZ

    sc = trimesh.Scene()
    seat = trimesh.creation.box(extents=(0.4, 0.04, 0.4))
    seat.apply_translation((0, 0.43, 0))
    sc.add_geometry(seat, node_name="Seat", geom_name="Seat")
    for i, (x, z) in enumerate(LEG_XZ):
        h = 0.41 - gap_m if i == 3 else 0.41
        leg = trimesh.creation.cylinder(radius=0.02, height=h, sections=24)
        leg.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, (1, 0, 0)))
        leg.apply_translation((x, h / 2, z))  # bottom stays on the ground
        sc.add_geometry(leg, node_name=f"Leg_{i}", geom_name=f"Leg_{i}")
    sc.export(str(path))
    return path


def test_a_part_detached_at_the_top_is_floating_even_though_it_reaches_the_floor(tmp_path: Path) -> None:
    """CG-1: every ground-touching component was unioned into `support`, so a part
    that merely reached y=0 counted as supported.  Byte for byte the same defect as
    the lifted-leg control — it must be reported the same way."""
    p = _stool_with_a_leg_short_at_the_top(tmp_path / "top_gap.glb")

    r = check_connectivity(p)

    assert not r.passed, [f.message for f in r.findings]
    errs = r.errors
    assert len(errs) == 1 and errs[0].target == "Leg_3"
    assert errs[0].data["nearest"] == "Seat"
    assert errs[0].data["gap_m"] == pytest.approx(FLOAT_GAP_M, abs=1e-4)
    # and no self-contradictory "all N parts are connected" line reaches the judge
    assert not [f for f in r.findings if "parts are connected" in f.message]


def test_two_grounded_islands_are_not_one_connected_assembly(tmp_path: Path) -> None:
    """The minimal shape of the same bug: two boxes 2 m apart, both on the floor,
    used to pass as 'all 2 parts are connected (0 contacts)'."""
    sc = trimesh.Scene()
    for i, x in enumerate((0.0, 2.0)):
        m = trimesh.creation.box((0.5, 0.5, 0.5))
        m.apply_translation((x, 0.25, 0))
        sc.add_geometry(m, node_name=f"P{i}", geom_name=f"P{i}")
    p = tmp_path / "two_grounded.glb"
    sc.export(str(p))

    r = check_connectivity(p)

    assert not r.passed and len(r.errors) == 1
    assert not [f for f in r.findings if "parts are connected" in f.message]

def _hat_and_body(path: Path, dy: float) -> Path:
    """'hat' floating 100 mm above 'body', the pair translated by ``dy``."""
    sc = trimesh.Scene()
    body = trimesh.creation.box(extents=(0.2, 0.2, 0.2))
    body.apply_translation((0, 0.1 + dy, 0))
    hat = trimesh.creation.box(extents=(0.1, 0.1, 0.1))
    hat.apply_translation((0, 0.2 + 0.1 + 0.05 + dy, 0))  # 100 mm above the body's top
    sc.add_geometry(body, node_name="body", geom_name="body")
    sc.add_geometry(hat, node_name="hat", geom_name="hat")
    path.write_bytes(sc.export(file_type="glb"))
    return path


def test_a_model_authored_below_the_floor_still_reports_its_floating_part(tmp_path: Path) -> None:
    """CG-3: `grounded` was the one-sided `bounds[0][1] <= gap_m`, so a part buried 1 m
    UNDER the floor counted as touching the ground.  Every part of a sunk model therefore
    landed in `grounded`, hence in `support`, and no floating finding could be emitted at
    all — one routine agent mistake (authoring around the origin instead of on the floor,
    which the contract gate already flags separately) silently switched the whole
    connectivity floating check off for the run."""
    on_ground = check_connectivity(_hat_and_body(tmp_path / "f2_on_ground.glb", 0.0))
    sunk = check_connectivity(_hat_and_body(tmp_path / "f2_sunk.glb", -1.0))

    assert not on_ground.passed
    assert [(e.target, e.data["nearest"]) for e in on_ground.errors] == [("hat", "body")]

    # the identical model, 1 m lower, must reach the identical verdict
    assert not sunk.passed, "a sunk model must not disable the floating check"
    assert [(e.target, e.data["nearest"]) for e in sunk.errors] == [("hat", "body")]
    # ... plus the ground warning, since nothing is on the floor any more
    assert any("touches the ground" in f.message for f in sunk.findings)


def _scene(path: Path, **meshes: trimesh.Trimesh) -> Path:
    sc = trimesh.Scene()
    for name, m in meshes.items():
        sc.add_geometry(m, node_name=name, geom_name=name)
    path.write_bytes(sc.export(file_type="glb"))
    return path


def _cylinder_y(radius: float, height: float, center: tuple[float, float, float]) -> trimesh.Trimesh:
    m = trimesh.creation.cylinder(radius=radius, height=height, sections=32)
    m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, (1, 0, 0)))
    m.apply_translation(center)
    return m


def _penetrations(r) -> list:
    return [f for f in r.findings if f.data.get("kind") == "penetration"]


def _ledger(r):
    led = [f for f in r.findings if f.message.startswith("contact ledger")]
    assert len(led) == 1 and led[0].severity == Severity.INFO and led[0].target == ""
    return led[0]


def test_thin_rod_through_a_plate_is_measured_and_named_but_severity_stays_on_depth(tmp_path: Path) -> None:
    """An 8 mm rod, 1 m long, standing through a 10 mm plate.  The overlap band is ~1 % of
    the rod's surface, under PENETRATION_MIN_FRACTION, and its deepest point is 5 mm, under
    PENETRATION_ERROR_M — so the 4ddde32 gate said nothing on this GLB (verified against
    the committed module, 2026-08-30).  The dense local pass finds it and the through-ratio
    says the rod reaches the plate's far side — as a MEASUREMENT: it is a WARN, because the
    same number is what a stile through a seat or a boom seated in a mast reads, and the
    picture, not the gate, decides whether that is a defect (corpus re-run: a 0.9 ERROR line
    flipped 9 runs of designed joinery)."""
    rod = _cylinder_y(0.004, 1.0, (0, 0.5, 0))
    plate = trimesh.creation.box(extents=(0.3, 0.01, 0.3))
    plate.apply_translation((0, 0.505, 0))

    r = check_connectivity(_scene(tmp_path / "rod_plate.glb", Rod=rod, Plate=plate))

    pen = _penetrations(r)
    assert len(pen) == 1 and pen[0].severity == Severity.WARN and r.passed
    d = pen[0].data
    assert d["fraction_inside"] < PENETRATION_MIN_FRACTION, "the old floor would have silenced this"
    assert d["depth_m"] < PENETRATION_ERROR_M, "depth alone keeps it a WARN"
    assert d["depth_m"] == pytest.approx(0.005, abs=0.0005)
    assert d["inside_count"] >= 4 and d["local_fraction"] >= 0.2
    assert d["through_ratio"] >= THROUGH_FAR_SIDE_RATIO
    assert d["entering"] == "Rod" and d["container"] == "Plate" and d["thickness_m"] == pytest.approx(0.01, abs=1e-6)
    assert "reaches 93% of the way to the mid-plane of 'Plate' (10 mm thick)" in pen[0].message
    assert "middle of 'Plate'" in pen[0].fix_hint and "shorten it" in pen[0].fix_hint
    # the ledger carries the same pair with the same numbers, rounded
    over = _ledger(r).data["overlaps"]
    assert len(over) == 1 and set(over[0][:2]) == {"Rod", "Plate"}
    assert over[0][2:] == [pytest.approx(5.0, abs=0.5), pytest.approx(d["through_ratio"], abs=0.01)]


def test_a_designed_socket_stays_a_warn_with_a_low_through_ratio(tmp_path: Path) -> None:
    """A branch neck seated 3 mm into a pipe block (pipe_tee's BranchPipeNeck x MainPipeRun):
    above the 2 mm WARN line, nowhere near the far side of anything — WARN, and the message
    does not talk about reaching through."""
    pipe = trimesh.creation.box(extents=(0.3, 0.1, 0.1))
    pipe.apply_translation((0, 0.05, 0))
    neck = _cylinder_y(0.02, 0.1, (0, 0.1 - 0.003 + 0.05, 0))

    r = check_connectivity(_scene(tmp_path / "socket.glb", MainPipeRun=pipe, BranchNeck=neck))

    pen = _penetrations(r)
    assert r.passed and len(pen) == 1 and pen[0].severity == Severity.WARN
    d = pen[0].data
    assert d["depth_m"] == pytest.approx(0.003, abs=0.0005)
    assert d["through_ratio"] < 0.2
    assert "reaches" not in pen[0].message and "where they meet" in pen[0].message


def test_a_thin_part_sunk_into_a_thick_one_is_not_through(tmp_path: Path) -> None:
    """h2h_microscope's FieldIlluminator: a 5 mm disc sunk 2.3 mm into the base.  Measured
    one way, the base's top surface reaches 82 % of the way through the disc; the pair's
    through-ratio is the min over both directions, so a designed inset stays a WARN."""
    base = trimesh.creation.box(extents=(0.2, 0.05, 0.2))
    base.apply_translation((0, 0.025, 0))
    disc = _cylinder_y(0.02, 0.005, (0, 0.05 - 0.0023 + 0.0025, 0))

    r = check_connectivity(_scene(tmp_path / "inset.glb", BaseStand=base, FieldIlluminator=disc))

    pen = _penetrations(r)
    assert r.passed and len(pen) == 1 and pen[0].severity == Severity.WARN
    assert pen[0].data["through_ratio"] < 0.2, pen[0].data


def test_contact_ledger_carries_contacts_overlaps_and_ground_gaps(tmp_path: Path) -> None:
    """The 1 mm weld of test_hairline_overlap_is_fine is below the WARN line — no finding —
    but the judge payload needs to know it is there to call it a weld, so the INFO ledger
    lists it, with the contact and every part's height above the ground."""
    a = trimesh.creation.box(extents=(0.2, 0.2, 0.2))
    a.apply_translation((0, 0.1, 0))
    b = trimesh.creation.box(extents=(0.1, 0.1, 0.1))
    b.apply_translation((0, 0.2 + 0.05 - 0.001, 0))

    r = check_connectivity(_scene(tmp_path / "weld.glb", A=a, B=b))

    assert r.passed and not _penetrations(r)
    led = _ledger(r)
    assert led.message == "contact ledger: 2 parts, 1 contacts, 1 overlaps"
    d = led.data
    assert sorted(d["parts"]) == ["A", "B"] and d["contact_gap_mm"] == 2.0
    assert len(d["contacts"]) == 1 and set(d["contacts"][0][:2]) == {"A", "B"} and d["contacts"][0][2] == 0.0
    assert len(d["overlaps"]) == 1 and set(d["overlaps"][0][:2]) == {"A", "B"}
    assert d["overlaps"][0][2] == pytest.approx(1.0, abs=0.1)
    assert d["ground_gap_mm"] == {"A": 0.0, "B": pytest.approx(199.0, abs=0.1)}
    assert d["planned"] == [] and d["planned_unresolved"] == []


def test_planned_edges_are_measured_regardless_of_the_aabb_prefilter(solid_stool_glb: Path) -> None:
    """Leg_0-Seat is a contact; Leg_0-Leg_1 are 260 mm apart, a pair the AABB prefilter
    never measures — a planned join is measured anyway and reported open, not as an ERROR
    (whether it should fail the gate is the owner's call); a name the GLB does not have is
    listed unresolved."""
    r = check_connectivity(solid_stool_glb, planned_edges=[("Leg_0", "Seat"), ("Leg_0", "Leg_1"), ("Leg_0", "Ghost")])

    assert r.passed
    d = _ledger(r).data
    assert d["planned"] == [["Leg_0", "Seat", 0.0, "contact"], ["Leg_0", "Leg_1", pytest.approx(260.0, abs=0.5), "open"]]
    assert d["planned_unresolved"] == ["Ghost"]
    assert sorted(d["parts"]) == ["Leg_0", "Leg_1", "Leg_2", "Leg_3", "Seat"]
    assert len(d["contacts"]) == 4 and all(c[1] == "Seat" or c[0] == "Seat" for c in d["contacts"])


def test_a_deep_overlap_only_the_dense_pass_can_see_is_a_warn_with_every_number(tmp_path: Path) -> None:
    """A 40 mm square stile driven 17 mm into a 0.45 m seat slab — the corpus's commonest new
    finding (rear stiles through seats on 14 dining chairs).  The overlap is ~1 % of either
    surface, so the 4ddde32 gate never saw it; the dense pass measures it, but a member the
    eye cannot see inside a slab must not cap the run at 0.7 on its own: WARN, depth and
    through-ratio in data, and the judge decides from the picture (D46 d)."""
    seat = trimesh.creation.box(extents=(0.45, 0.036, 0.45))
    seat.apply_translation((0, 0.45, 0))                       # underside at y = 0.432
    stile = trimesh.creation.box(extents=(0.03, 1.0, 0.03))    # a 1 m backrest post
    stile.apply_translation((0.2, 0.432 + 0.017 - 0.5, 0.2))  # its top 17 mm inside the slab
    r = check_connectivity(_scene(tmp_path / "stile_seat.glb", Seat=seat, Stile=stile))

    pen = _penetrations(r)
    assert len(pen) == 1
    d = pen[0].data
    assert d["depth_m"] > PENETRATION_ERROR_M, "deep — the old ERROR line by depth"
    assert d["fraction_inside"] < PENETRATION_MIN_FRACTION, "…but a sliver of either surface"
    assert pen[0].severity == Severity.WARN and r.passed
    assert d["inside_count"] >= 4 and d["local_fraction"] >= 0.2 and 0.0 < d["through_ratio"] <= 2.0

    # the same depth on a visible share of the part is still the ERROR it always was
    post = trimesh.creation.box(extents=(0.2, 0.2, 0.2))
    post.apply_translation((0, 0.45 + 0.1 - 0.017 - 0.018, 0))  # 17 mm of a fat post into the seat
    r2 = check_connectivity(_scene(tmp_path / "post_seat.glb", Seat=seat, Post=post))
    pen2 = _penetrations(r2)
    assert pen2 and pen2[0].severity == Severity.ERROR and not r2.passed
    assert pen2[0].data["fraction_inside"] >= PENETRATION_MIN_FRACTION
