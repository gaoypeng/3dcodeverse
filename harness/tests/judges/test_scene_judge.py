"""scene_v2 (2026-10-14): what the scene judge is shown — the gate facts in the scene branch, the
MEASURED PLACEMENT line, the contact crops cut around measured placement findings, and the rubric's
own numbers.  The object branch of every helper is pinned byte-identical in test_prompt_builder."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from PIL import Image

from codeverse3d.config import Placement
from codeverse3d.contracts.artifacts import GateFinding, GateReport, RenderSet, RenderView, Severity
from codeverse3d.contracts.chat import ImagePart
from codeverse3d.contracts.common import Language, Track
from codeverse3d.judges.base import JudgeInput
from codeverse3d.judges.prompt_builder import (
    CONTACT_CROP_MIN_FRAC,
    RIG_RULES,
    build_judge_messages,
    gates_section,
    judge_prompt_hash,
    placement_block,
    placement_findings_for_crops,
    plan_contact_crops,
    plan_detail_crops,
    plan_montages,
    project_box,
    view_rig_section,
)
from codeverse3d.judges.rubrics import load_rubric, measured_absent
from codeverse3d.spatial.scene_placement import placement_findings
from tests.judges.conftest import draw_chair, make_spec

E, W, INFO = Severity.ERROR, Severity.WARN, Severity.INFO
G = "scene_placement"
S = load_rubric("scene_v2")


# --------------------------------------------------------------------------- fixtures
def _f(sev: Severity, kind: str, target: str, msg: str, **data) -> GateFinding:
    return GateFinding(gate=G, severity=sev, target=target, message=msg, data={"kind": kind, **data})


def _summary(**over) -> GateFinding:
    data = {"kind": "summary", "counts": {"floating": 1, "sunken": 0, "unsupported": 1, "interpenetration": 1},
            "checked": 12, "total": 15, "exempt": {"free": 1, "backdrop": 2}, "truncated": False}
    data.update(over)
    return GateFinding(gate=G, severity=INFO, target="scene", message="12 assets checked (15 placed, 3 exempt)", data=data)


def _box(center, size) -> dict:
    return {"center": list(center), "size": list(size)}


LANTERN = _f(E, "floating", "Pondside/Lantern", "Pondside/Lantern is floating 0.12 m above Ground (touches nothing)",
             gap_m=0.12, zone="Pondside", bbox=_box((2.0, 0.6, -1.0), (0.4, 1.2, 0.4)))
CRATE = _f(W, "unsupported", "Quay/Crate", "Quay/Crate is unsupported: hovers 0.04 m above Ground", gap_m=0.04, zone="Quay",
           bbox=_box((-3.0, 0.5, 2.0), (1.0, 1.0, 1.0)))
BENCH = _f(W, "interpenetration", "Quay/Bench", "interpenetration: Quay/Bench and Quay/Wall overlap (15% of the smaller box)",
           overlap=0.15, inside_frac=0.1, other="Quay/Wall", zone="Quay", bbox=_box((1.0, 0.4, 3.0), (1.8, 0.8, 0.6)))
NO_FOG = _f(E, "no_fog", "env", "scene.fog is not set — frames read as thin_atmosphere")
NO_BOX = _f(E, "floating", "Quay/Ghost", "Quay/Ghost is floating 0.30 m above Ground", gap_m=0.30)


def _placement(*findings: GateFinding, passed: bool | None = None, summary: GateFinding | None = None) -> GateReport:
    rows = [summary or _summary(), *findings]
    return GateReport(gate=G, passed=not any(f.severity == E for f in rows) if passed is None else passed, findings=rows)


def _frames(*findings: GateFinding) -> GateReport:
    return GateReport.of("scene_frames", findings)


def _frame(sev: Severity, kind: str, view: str, msg: str) -> GateFinding:
    return GateFinding(gate="scene_frames", severity=sev, target=view, message=msg, data={"kind": kind, "view": view})


LINT = GateReport(gate="lint:scene_threejs", passed=True, findings=[
    GateFinding(gate="lint:scene_threejs", severity=W, target="src/zones/quay.js", message="unused import", data={"kind": "layout"})])


def _cam(name: str, position, look_at, *, fov: float = 50.0, time_s: float = 0.0, path: str = "/x.png", wh=(1024, 576)) -> RenderView:
    return RenderView(name=name, path=path, width=wh[0], height=wh[1], camera_position=position, look_at=look_at, fov=fov, time_s=time_s)


#: a rig around a 12 m scene: the three high overviews and the ground-level contact camera
def _rig(root: Path | None = None, *, time_s: float = 0.0) -> list[RenderView]:
    def path(name: str) -> str:
        if root is None:
            return f"/x/{name}.png"
        return str(draw_chair(root / f"{name}_t{time_s:g}.png"))

    return [
        _cam("overview_front_right", (9.0, 9.0, 11.0), (0.0, 0.5, 0.0), path=path("overview_front_right"), time_s=time_s),
        _cam("overview_back_left", (-9.0, 9.0, -11.0), (0.0, 0.5, 0.0), path=path("overview_back_left"), time_s=time_s),
        _cam("overview_top", (0.0, 30.0, 0.5), (0.0, 0.0, 0.0), path=path("overview_top"), time_s=time_s),
        _cam("overview_low_front", (5.0, 0.4, 14.0), (2.0, 0.0, 6.0), path=path("overview_low_front"), time_s=time_s),
    ]


# --------------------------------------------------------------------------- the gates section
def test_scene_gates_section_lists_placement_first_then_frames_then_the_rest_and_groups_warns():
    gates = [LINT, _placement(LANTERN, CRATE, BENCH, NO_FOG), _frames(_frame(E, "dark_frame", "Establishing", "frame too dark: mean luminance 0.07"),
                                                                       _frame(W, "content_small", "overview_top", "overview rig sees sparse content"))]
    text = gates_section(gates, scene=True)
    lines = text.splitlines()
    assert lines[0] == "GATE FINDINGS (deterministic; treat as facts): scene_placement=FAIL, scene_frames=FAIL, lint:scene_threejs=pass"
    assert lines[1] == "errors (3):"
    assert lines[2] == "- scene_placement [Pondside/Lantern]: Pondside/Lantern is floating 0.12 m above Ground (touches nothing)"
    assert lines[3] == "- scene_placement [env]: scene.fog is not set — frames read as thin_atmosphere"
    assert lines[4] == "- scene_frames [Establishing]: frame too dark: mean luminance 0.07"
    assert lines[5].startswith("MEASURED PLACEMENT (scene_placement gate")
    assert ("12 assets checked of 15 placed, 3 exempt (1 tagged free): 1 floating (worst 0.12 m), 0 sunken, "
            "1 unsupported (worst 0.04 m), 1 interpenetrating (worst 15% of the smaller box).") in lines[5]
    assert "PLACEMENT PASSED" not in text
    # the WARNs: grouped by (gate, kind), placement before frames before lint
    assert lines[6] == "warnings (4), grouped by kind:"
    assert lines[7] == "- scene_placement/unsupported ×1: [Quay/Crate] Quay/Crate is unsupported: hovers 0.04 m above Ground"
    assert lines[8].startswith("- scene_placement/interpenetration ×1: [Quay/Bench] interpenetration: Quay/Bench and Quay/Wall overlap")
    assert lines[9] == "- scene_frames/content_small ×1: [overview_top] overview rig sees sparse content"
    assert lines[10] == "- lint:scene_threejs/layout ×1: [src/zones/quay.js] unused import"
    assert len(lines) == 11
    # the object branch is untouched by the flag (its byte-identity is pinned in test_prompt_builder)
    obj = gates_section(gates)
    assert "MEASURED PLACEMENT" not in obj and obj.startswith("GATE FINDINGS (deterministic; treat as facts): lint:scene_threejs=pass, scene_placement=FAIL")
    assert gates_section([], scene=True) == "GATE FINDINGS: (no gates run)"


def test_scene_gates_section_clean_round_states_placement_passed():
    summary = _summary(counts={"floating": 0, "sunken": 0, "unsupported": 0, "interpenetration": 0}, exempt={},
                       thresholds={"contact_m": 0.02, "floating_m": 0.05, "floating_error_m": 0.08, "overlap_warn": 0.1})
    text = gates_section([_placement(summary=summary), _frames(), LINT.model_copy(update={"findings": []})], scene=True)
    assert "no errors or warnings — every checked asset is seated, the frames are exposed and the contracts hold." in text
    block = placement_block([_placement(summary=summary)])
    assert "12 assets checked of 15 placed, 0 exempt (0 tagged free): 0 floating, 0 sunken, 0 unsupported, 0 interpenetrating." in block
    assert "Thresholds: contact_m 0.02, floating_error_m 0.08, floating_m 0.05, overlap_warn 0.1." in block
    assert "PLACEMENT PASSED: no checked asset floats, sinks or overlaps another beyond the thresholds." in block
    assert "mark floating_asset / sunken_asset / interpenetration present only where a render VISIBLY shows it" in block
    # a WARN of a placement kind on a passed report: measured, named, no PASSED sentence
    hover = placement_block([_placement(CRATE, summary=summary)])
    assert "1 unsupported (worst 0.04 m)" in hover and "PLACEMENT PASSED" not in hover
    # no gate / a probe that failed: nothing measured, nothing claimed
    assert placement_block([_frames()]) == ""
    failed = GateReport(gate=G, passed=True, findings=[_f(W, "probe_failed", "scene", "placement probe failed: boom")])
    assert placement_block([failed]) == "" and "probe_failed" in gates_section([failed], scene=True)


def test_placement_block_names_a_free_exempt_share_and_a_cantilevered_row():
    free = _f(E, "free_exempt", "scene", "6 of 10 placed assets are tagged placement='free'")
    cant = _f(W, "cantilevered", "Quay/Jetty", "Quay/Jetty rests on 1 of 6 columns", gap_max_m=0.31, columns_resting=1)
    block = placement_block([_placement(free, cant)])
    assert "The share of assets tagged placement='free' is over the line" in block
    assert "1 cantilevered (worst 0.31 m)" in block and "PLACEMENT PASSED" not in block


# --------------------------------------------------------------------------- projection
def test_project_box_through_a_pinhole_camera():
    """A camera at z=10 looking at the origin with a 90° vertical fov and a square frame: the
    origin lands mid-frame, a 2 m box at the origin spans 10 % of the frame (±1 m over a ±10 m
    half-frame at that depth) each way."""
    cam = _cam("c", (0.0, 0.0, 10.0), (0.0, 0.0, 0.0), fov=90.0, wh=(512, 512))
    assert project_box(cam, (0.0, 0.0, 0.0), (2.0, 2.0, 0.0)) == pytest.approx((0.45, 0.45, 0.55, 0.55))
    # +X is screen-right (u grows), +Y is screen-up (v shrinks)
    left, top, right, bottom = project_box(cam, (5.0, 5.0, 0.0), (0.0, 0.0, 0.0))
    assert left == right == pytest.approx(0.75) and top == bottom == pytest.approx(0.25)
    # behind the lens, outside the frame, or an unknown camera: no projection
    assert project_box(cam, (0.0, 0.0, 20.0), (1.0, 1.0, 1.0)) is None
    assert project_box(cam, (30.0, 0.0, 0.0), (1.0, 1.0, 1.0)) is None
    assert project_box(RenderView(name="x", path="/x.png"), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)) is None
    # a 16:9 frame halves the horizontal fraction of the same box; the rig's 50° is the fov fallback
    wide = _cam("w", (0.0, 0.0, 10.0), (0.0, 0.0, 0.0), fov=90.0, wh=(1024, 576))
    l2, _, r2, _ = project_box(wide, (0.0, 0.0, 0.0), (2.0, 2.0, 0.0))
    assert (r2 - l2) == pytest.approx(0.1 * 9 / 16)
    nofov = RenderView(name="n", path="/x.png", camera_position=(0.0, 0.0, 10.0), look_at=(0.0, 0.0, 0.0))
    half = math.tan(math.radians(25.0)) * 10.0
    assert project_box(nofov, (0.0, 0.0, 0.0), (2 * half, 2 * half, 0.0)) == pytest.approx((0.5 - 0.5 * 9 / 16, 0.0, 0.5 + 0.5 * 9 / 16, 1.0))
    # a vertical (top-down) camera rolls like THREE's lookAt: −Z is screen-up
    top = _cam("t", (0.0, 10.0, 0.0), (0.0, 0.0, 0.0), fov=90.0, wh=(512, 512))
    _, t_top, _, b_top = project_box(top, (0.0, 0.0, -5.0), (0.0, 0.0, 0.0))
    assert t_top == b_top == pytest.approx(0.25)


# --------------------------------------------------------------------------- contact crops
def test_contact_crops_are_cut_from_the_tile_that_shows_the_asset_largest_error_first():
    gates = [_placement(CRATE, BENCH, LANTERN, NO_BOX, NO_FOG)]
    # order: ERROR first, then the larger magnitude; one per asset; only findings with a box
    assert [f.target for f in placement_findings_for_crops(gates)] == ["Pondside/Lantern", "Quay/Bench", "Quay/Crate"]
    crops = plan_contact_crops(_rig(), gates)
    assert [m.title for m in crops] == ["CONTACT CROP — Pondside/Lantern (gate: floating 0.12 m)",
                                        "CONTACT CROP — Quay/Bench (gate: interpenetration 15%)",
                                        "CONTACT CROP — Quay/Crate (gate: unsupported 0.04 m)"]
    assert all(m.is_detail and m.crop is not None and len(m.tiles) == 1 for m in crops)
    # the lantern projects 0.087 of the frame tall in the contact tile and 0.088 in overview_front_right:
    # the contact tile's 1.5× weight (it exists for this) decides
    assert crops[0].tiles[0].name == "overview_low_front"
    assert "cut from overview_low_front" in crops[0].hint and "judge the CONTACT" in crops[0].hint
    for m in crops:
        x0, y0, x1, y1 = m.crop
        assert 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0
        assert x1 - x0 >= CONTACT_CROP_MIN_FRAC - 1e-6 and y1 - y0 >= CONTACT_CROP_MIN_FRAC - 1e-6
    # the crop window holds the projected box centre
    view, (cx, cy, cz), size = crops[0].tiles[0], LANTERN.data["bbox"]["center"], LANTERN.data["bbox"]["size"]
    left, top, right, bottom = project_box(view, (cx, cy, cz), size)
    x0, y0, x1, y1 = crops[0].crop
    assert x0 <= (left + right) / 2 <= x1 and y0 <= (top + bottom) / 2 <= y1


def test_contact_crops_prefer_a_low_tile_over_the_top_down_one_and_skip_what_no_tile_sees():
    # only the top-down tile and one high overview: the high one wins even when the top projects larger
    rig = [_rig()[2], _rig()[0]]
    crops = plan_contact_crops(rig, [_placement(LANTERN)])
    assert len(crops) == 1 and crops[0].tiles[0].name == "overview_front_right"
    # the two high overviews alone: the larger projection wins (back_left is farther from the lantern)
    assert plan_contact_crops([_rig()[1], _rig()[0]], [_placement(LANTERN)])[0].tiles[0].name == "overview_front_right"
    # an asset behind every camera / outside every frame: no crop, no error
    far = _f(E, "floating", "Far/Rock", "floating", gap_m=0.5, bbox=_box((400.0, 0.0, 400.0), (1.0, 1.0, 1.0)))
    assert plan_contact_crops(_rig(), [_placement(far)]) == []
    # no overview tile with a camera (a legacy record), or no rig at all: nothing
    legacy = [RenderView(name="overview_front_right", path="/x.png"), RenderView(name="Establishing", path="/y.png")]
    assert plan_contact_crops(legacy, [_placement(LANTERN)]) == []
    assert plan_contact_crops([RenderView(name="Establishing", path="/y.png")], [_placement(LANTERN)]) == []
    # the eye rig never reaches the judge, so it is never a crop source
    eye = [_cam("eye_front", (0.0, 1.6, 14.0), (0.0, 0.5, 0.0))]
    assert plan_contact_crops(eye, [_placement(LANTERN)]) == []
    # a box that fills the tile needs no crop from that tile
    huge = _f(E, "floating", "Big/Hall", "floating", gap_m=0.2, bbox=_box((0.0, 3.0, 0.0), (20.0, 6.0, 20.0)))
    assert plan_contact_crops(_rig(), [_placement(huge)]) == []
    # the later time's tiles are not crop sources: crops cite the t=0 tile
    both = [*_rig(time_s=1.5), *_rig(time_s=0.0)]
    assert all(m.tiles[0].time_s == 0.0 for m in plan_contact_crops(both, [_placement(LANTERN)]))


def test_plan_detail_crops_scene_is_centre_plus_contact_crops_under_the_dial(tmp_path):
    shaded = [_cam("Establishing", (6.0, 2.0, 12.0), (0.0, 1.0, 0.0)), *_rig()]
    gates = [_placement(LANTERN, BENCH, CRATE)]
    three = plan_detail_crops(shaded, scene=True, gates=gates, limit=2)
    assert [m.title.split(" — ")[0] for m in three] == ["DETAIL CROP", "CONTACT CROP", "CONTACT CROP"]
    assert three[0].tiles[0].name == "Establishing" and three[0].crop == (0.25, 0.25, 0.75, 0.75)
    assert len(plan_detail_crops(shaded, scene=True, gates=gates, limit=5)) == 4   # centre + all three
    assert len(plan_detail_crops(shaded, scene=True, gates=gates, limit=1)) == 2
    assert plan_detail_crops(shaded, scene=True, gates=gates, limit=0) == []       # the dial at 0: no crop at all
    assert [m.title for m in plan_detail_crops(shaded, scene=True, limit=2)] == ["DETAIL CROP — centre of Establishing at 2×"]
    # the object branch: centre + ground band, cut to the limit, never a contact crop
    obj = [RenderView(name=n, path="/x.png") for n in ("front_right_high", "front_right_low")]
    assert [m.title.split(" — ")[1][:6] for m in plan_detail_crops(obj, scene=False, gates=gates, limit=2)] == ["centre", "ground"]
    assert len(plan_detail_crops(obj, scene=False, limit=1)) == 1 and plan_detail_crops(obj, scene=False, limit=0) == []
    # through plan_montages: the grids, then the crops (the pairwise judge's detail_crops=0 sends none)
    ms = plan_montages(RenderSet(views=shaded), scene=True, gates=gates, detail_crops=2)
    assert [m.kind for m in ms] == ["shaded", "shaded", "detail", "detail", "detail"]
    assert all(not m.is_detail for m in plan_montages(RenderSet(views=shaded), scene=True, gates=gates, detail_crops=0))


def test_a_scene_verdict_sends_the_contact_crops_after_the_centre_crop(tmp_path, cache_dir):
    rig = _rig(tmp_path)
    authored = _cam("Establishing", (6.0, 2.0, 12.0), (0.0, 1.0, 0.0), path=str(draw_chair(tmp_path / "est.png")))
    spec = make_spec(track=Track.SCENE, language=Language.SCENE_THREEJS, prompt="a quay at dusk")
    inp = JudgeInput(spec=spec, renders=RenderSet(views=[authored, *rig]), gates=[_placement(LANTERN, CRATE, NO_FOG), _frames()],
                     plan_summary="Zones: Quay, Pondside. Assets: Lantern, Crate.")
    _, msgs = build_judge_messages(inp, S, cache_dir=cache_dir)
    images = [p for p in msgs[0].parts if isinstance(p, ImagePart)]
    labels = [p.label for p in images]
    assert len(images) == 2 + 1 + 2  # two grids (5 views) + the centre crop + two contact crops
    assert labels[2].startswith("DETAIL CROP — centre of Establishing at 2× [1/3]")
    assert labels[3].startswith("CONTACT CROP — Pondside/Lantern (gate: floating 0.12 m) [2/3] — diagnostic close-up cut from overview_low_front")
    assert labels[4].startswith("CONTACT CROP — Quay/Crate (gate: unsupported 0.04 m) [3/3]")
    with Image.open(images[3].path) as im:
        assert max(im.size) <= 768 and min(im.size) > 100
    text = msgs[0].parts[0].text
    assert "MEASURED PLACEMENT (scene_placement gate" in text and "1 floating (worst 0.12 m)" in text
    assert "- detail crop 2: CONTACT CROP — Pondside/Lantern (gate: floating 0.12 m)" in text
    assert "plus 3 DETAIL CROP(s)" in text and RIG_RULES["scene_contact"] in text
    assert text.index("scene_placement=FAIL") < text.index("scene_frames=pass")
    # an object verdict never gets the scene text
    obj = JudgeInput(spec=make_spec(), renders=RenderSet(views=[authored]), gates=[_placement(LANTERN)])
    _, omsgs = build_judge_messages(obj, load_rubric("static_object_v1"), cache_dir=cache_dir)
    otext = omsgs[0].parts[0].text
    assert "MEASURED PLACEMENT" not in otext and "CONTACT CROP" not in otext and RIG_RULES["object"] in otext


def test_view_rig_counts_contact_crops_as_detail_crops():
    rs = RenderSet(views=_rig())
    montages = plan_montages(rs, scene=True, gates=[_placement(LANTERN)], detail_crops=2)
    text = view_rig_section(rs, montages, scene=True)
    assert text.startswith("VIEW RIG: 1 MONTAGE image(s) follow plus 2 DETAIL CROP(s).")
    assert "- detail crop 2: CONTACT CROP — Pondside/Lantern (gate: floating 0.12 m)" in text


# --------------------------------------------------------------------------- the rubric
def test_scene_v2_numbers_are_pinned():
    """What moved from v1 (eval/docs/EVAL.md §6, 2026-10-14) — and what stayed."""
    caps = {c.id: c for c in S.caps}
    assert S.version == 2 and S.pass_threshold == 0.70
    assert {k: (c.cap, tuple(c.kinds), tuple(c.measures), c.veto_warn_keys) for k, c in caps.items() if c.gate == G} == {
        "floating_asset": (0.60, ("floating", "unsupported"), ("floating_asset",), {"gap_m": 0.02}),
        "sunken_asset": (0.65, ("sunken",), ("sunken_asset",), {"sunk_m": 0.10}),
        "interpenetration": (0.70, ("interpenetration",), ("interpenetration",), {"overlap": 0.20}),
        "wrong_scale": (0.75, ("scale",), ("wrong_scale",), {"factor": 0.0}),
        "free_exempt": (0.75, ("free_exempt",), ("free_exempt",), {}),
    }
    # the veto lines are the gate's own tolerances (config.Placement), not numbers of their own: gap_m is
    # the INDOOR tolerance — the outdoor 0.05 (until 2026-10-15) let a 2-5 cm indoor hover the gate had
    # flagged be vetoed — and no larger than contact_m, so every floating / unsupported WARN the gate raises
    # (gap > contact_m) reaches it; sunk_m is where a sunken WARN starts; the overlap line sits inside the
    # WARN band on purpose (a 10 % box overlap is not yet a visible intersection)
    p = Placement()
    assert caps["floating_asset"].veto_warn_keys["gap_m"] == p.floating_indoor_m <= p.contact_m
    assert caps["sunken_asset"].veto_warn_keys["sunk_m"] == p.sunk_m
    assert p.overlap_warn < caps["interpenetration"].veto_warn_keys["overlap"] < p.overlap_error
    assert "floating_part" not in caps and "floating_or_sunken_asset" not in caps
    assert caps["camera_in_geometry"].kinds == ["camera_in_geometry", "camera_underground", "camera_blocked"]
    assert caps["missing_must_acceptance"].graded and caps["missing_must_acceptance"].cap == 0.6
    assert {k: caps[k].cap for k in ("build_error", "shader_error", "console_error", "dark_frame", "blown_frame", "flat_frame", "content_small")} == {
        "build_error": 0.0, "shader_error": 0.5, "console_error": 0.6, "dark_frame": 0.55, "blown_frame": 0.6, "flat_frame": 0.65, "content_small": 0.6}
    defects = {d.id: (d.penalty, d.cap) for d in S.defects}
    assert "floating_or_sunken_asset" not in defects
    assert {k: defects[k] for k in ("floating_asset", "sunken_asset", "interpenetration", "wrong_scale", "hard_edged_particles",
                                    "missing_planned_element", "hero_misplaced")} == {
        "floating_asset": (0.10, 0.60), "sunken_asset": (0.08, 0.65), "interpenetration": (0.08, 0.70), "wrong_scale": (0.06, 0.75),
        "hard_edged_particles": (0.04, None), "missing_planned_element": (0.06, None), "hero_misplaced": (0.06, None)}
    # six anchors on every criterion; the weights are v1's
    for c in S.criteria:
        assert sorted(c.anchors, key=float) == ["0.1", "0.4", "0.55", "0.7", "0.85", "1.0"], c.id
    assert {c.id: c.weight for c in S.criteria} == {c.id: c.weight for c in load_rubric("scene_v1").criteria}
    notes = S.extra_instructions
    for phrase in ("CALIBRATION", "at most 0.35", "around 0.55", "0.80 or more", "at least 0.3 apart", "room ceilings lifted",
                   "CONTACT CROP images are diagnostic close-ups", "must name the VIEW"):
        assert phrase in notes, phrase
    # the object rubrics did not move: no veto_warn_keys, the same content hash as their YAML
    for name in ("static_object_v1", "articulated_v1", "reference_v1"):
        assert all(not c.veto_warn_keys for c in load_rubric(name).caps), name
    assert judge_prompt_hash(S) != judge_prompt_hash(load_rubric("scene_v1"))


# --------------------------------------------------------------------------- the real gate → the judge
def _row(name: str, zone: str, lo: tuple[float, float, float], hi: tuple[float, float, float], **fields: object) -> dict:
    """One census placement row as host_placement.mjs writes it (spatial.scene_placement.AssetRow): the
    box is min / max in the GLB frame — the gate turns it into the center / size the contact crops read."""
    return {"name": name, "zone": zone, "bbox": {"min": list(lo), "max": list(hi)}, "columns": 1, **fields}


#: an outdoor quay for the REAL gate: a lantern 12 cm up (floating ERROR), a crate 4 cm up (unsupported
#: WARN — past contact_m, within the outdoor tolerance), a bench overlapping the wall by 15 %
#: (interpenetration WARN) and two of six placed things tagged free (a free_exempt WARN at 33 %)
QUAY = {
    "assets": [
        _row("Lantern", "Pondside", (1.8, 0.12, -1.2), (2.2, 1.32, -0.8), ground_gap_m=0.12, support="ground_y"),
        _row("Crate", "Quay", (-3.5, 0.04, 1.5), (-2.5, 1.04, 2.5), ground_gap_m=0.04, support="ground_y"),
        _row("Bench", "Quay", (0.1, 0.0, 2.7), (1.9, 0.8, 3.3), ground_gap_m=0.0, support="ground_y"),
        _row("Wall", "Quay", (0.0, 0.0, 3.2), (6.0, 1.2, 3.5), ground_gap_m=0.0, support="ground_y"),
        _row("Gull", "Pondside", (3.0, 2.0, 0.0), (3.3, 2.2, 0.3), exempt="free"),
        _row("Bunting", "Quay", (-2.0, 2.5, 0.0), (2.0, 2.6, 0.2), exempt="free"),
    ],
    "interpenetrations": [{"a": "Bench", "b": "Wall", "zone_a": "Quay", "zone_b": "Quay", "aabb_overlap": 0.15, "inside_frac": 0.1}],
    "total": 6, "checked": 4, "exempt": {"free": 2}, "ground_y": 0.0,
}


def test_the_real_placement_report_reaches_the_judge_as_counts_and_contact_crops(tmp_path, cache_dir):
    """The one test that feeds a report the REAL gate wrote (spatial.scene_placement.placement_findings),
    not this file's hand-written fakes, into placement_block / plan_contact_crops / build_judge_messages:
    the summary's counts / checked / total / exempt / thresholds and every finding's bbox / gap_m /
    overlap are bare string keys on both sides, and a rename on either would leave both suites green
    while the judge saw 0 floating and no crop (review 2026-10-15)."""
    rep = placement_findings(QUAY, indoor=False, thresholds=Placement())
    assert rep.gate == G and not rep.passed
    assert {(f.severity, f.data["kind"], f.target) for f in rep.findings} == {
        (INFO, "summary", "scene"), (E, "floating", "Pondside/Lantern"), (W, "unsupported", "Quay/Crate"),
        (W, "interpenetration", "Quay/Bench"), (W, "free_exempt", "scene")}
    # the measured block: the gate's own counts (every ASSET_KIND — cantilevered too, which the fakes above
    # omit), the worst magnitudes, the free share and the thresholds it recorded; no PASSED sentence
    block = placement_block([rep])
    assert ("4 assets checked of 6 placed, 2 exempt (2 tagged free): 1 floating (worst 0.12 m), 0 sunken, "
            "1 unsupported (worst 0.04 m), 1 interpenetrating (worst 15% of the smaller box), 0 cantilevered.") in block
    assert "The share of assets tagged placement='free' is over the line" in block
    assert "contact_m 0.02" in block and "floating_m 0.05" in block and "floating_error_m 0.08" in block
    assert "PLACEMENT PASSED" not in block
    # one contact crop per finding that carries a box (ERROR first, then the larger magnitude), each cut from
    # the ground-level overview around the centre of the box the gate derived from the row's min / max
    crops = plan_contact_crops(_rig(), [rep])
    assert [m.title for m in crops] == ["CONTACT CROP — Pondside/Lantern (gate: floating 0.12 m)",
                                        "CONTACT CROP — Quay/Bench (gate: interpenetration 15%)",
                                        "CONTACT CROP — Quay/Crate (gate: unsupported 0.04 m)"]
    assert [m.tiles[0].name for m in crops] == ["overview_low_front"] * 3
    boxes = [((2.0, 0.72, -1.0), (0.4, 1.2, 0.4)), ((1.0, 0.4, 3.0), (1.8, 0.8, 0.6)), ((-3.0, 0.54, 2.0), (1.0, 1.0, 1.0))]
    for m, (center, size) in zip(crops, boxes, strict=True):
        left, top, right, bottom = project_box(m.tiles[0], center, size)
        x0, y0, x1, y1 = m.crop
        assert x0 <= (left + right) / 2 <= x1 and y0 <= (top + bottom) / 2 <= y1, m.title
    # the verdict payload: the measured line in the text, the three crops after the centre crop, in that order
    rig = _rig(tmp_path)
    authored = _cam("Establishing", (6.0, 2.0, 12.0), (0.0, 1.0, 0.0), path=str(draw_chair(tmp_path / "est.png")))
    spec = make_spec(track=Track.SCENE, language=Language.SCENE_THREEJS, prompt="a quay at dusk")
    inp = JudgeInput(spec=spec, renders=RenderSet(views=[authored, *rig]), gates=[rep], plan_summary="Zones: Quay, Pondside.")
    _, msgs = build_judge_messages(inp, S, cache_dir=cache_dir, detail_crops=3)
    labels = [p.label for p in msgs[0].parts if isinstance(p, ImagePart)]
    assert len(labels) == 2 + 1 + 3
    assert labels[3].startswith("CONTACT CROP — Pondside/Lantern (gate: floating 0.12 m) [2/4] — diagnostic close-up cut from overview_low_front")
    assert labels[4].startswith("CONTACT CROP — Quay/Bench (gate: interpenetration 15%) [3/4] — diagnostic close-up cut from overview_low_front")
    assert labels[5].startswith("CONTACT CROP — Quay/Crate (gate: unsupported 0.04 m) [4/4] — diagnostic close-up cut from overview_low_front")
    text = msgs[0].parts[0].text
    assert "scene_placement=FAIL" in text and "MEASURED PLACEMENT (scene_placement gate" in text
    assert "1 floating (worst 0.12 m), 0 sunken, 1 unsupported (worst 0.04 m), 1 interpenetrating (worst 15% of the smaller box)" in text
    assert "plus 4 DETAIL CROP(s)" in text and "- detail crop 4: CONTACT CROP — Quay/Crate (gate: unsupported 0.04 m)" in text
    # and the arithmetic veto reads the same report: the floating ERROR keeps the claim, nothing sunken was measured
    assert measured_absent(S, "floating_asset", [rep]) is False and measured_absent(S, "sunken_asset", [rep]) is True


def test_a_hover_the_real_gate_warned_about_is_never_vetoed_indoors_or_out():
    """Review 2026-10-15 (findings 4 / 17): the veto line was the OUTDOOR tolerance (0.05 m), so an interior's
    floating WARN of 2-5 cm — raised against floating_indoor_m 0.02 — read as 'clean' and switched off the
    judge's floating_asset tick although the gate itself had flagged the asset.  The line is the indoor
    tolerance now: whatever floating / unsupported WARN the real gate raises keeps the tick, and a seated
    report (the gap within contact_m: no finding) still vetoes."""
    def report(gap: float, *, indoor: bool) -> GateReport:
        vase = _row("Vase", "Dining", (-0.05, gap, -0.05), (0.05, gap + 0.3, 0.05), ground_gap_m=gap, support="Table")
        return placement_findings({"assets": [vase], "total": 1, "checked": 1, "ground_y": 0.0}, indoor=indoor,
                                  thresholds=Placement())

    for gap, indoor, kind in ((0.03, True, "floating"), (0.04, False, "unsupported"), (0.06, False, "floating")):
        rep = report(gap, indoor=indoor)
        assert rep.passed and [(f.data["kind"], f.data["gap_m"]) for f in rep.findings if f.severity == W] == [(kind, gap)]
        assert measured_absent(S, "floating_asset", [rep]) is False, (gap, indoor)
    seated = report(0.01, indoor=True)
    assert seated.passed and [f.severity for f in seated.findings] == [INFO]
    assert measured_absent(S, "floating_asset", [seated]) is True
