"""scene judge: the rubric's own numbers, and the REAL placement gate report reaching the judge."""

from __future__ import annotations

from pathlib import Path

from codeverse3d.config import Placement
from codeverse3d.contracts.artifacts import GateReport, RenderSet, RenderView, Severity
from codeverse3d.contracts.chat import ImagePart
from codeverse3d.contracts.common import Language, Track
from codeverse3d.judges.base import JudgeInput
from codeverse3d.judges.prompt_builder import (
    build_judge_messages,
    placement_block,
    plan_contact_crops,
    project_box,
)
from codeverse3d.judges.rubrics import load_rubric, measured_absent
from codeverse3d.spatial.scene_placement import placement_findings
from tests.judges.conftest import draw_chair, make_spec

E, W, INFO = Severity.ERROR, Severity.WARN, Severity.INFO
G = "scene_placement"
S = load_rubric("scene")


# --------------------------------------------------------------------------- fixtures
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


# --------------------------------------------------------------------------- the rubric
def test_scene_numbers_are_pinned():
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
    # the veto lines are the gate's own tolerances (config.Placement), not numbers of their own
    p = Placement()
    assert caps["floating_asset"].veto_warn_keys["gap_m"] == p.floating_indoor_m <= p.contact_m
    assert caps["sunken_asset"].veto_warn_keys["sunk_m"] == p.sunk_m
    assert p.overlap_warn < caps["interpenetration"].veto_warn_keys["overlap"] < p.overlap_error
    assert caps["missing_must_acceptance"].graded and caps["missing_must_acceptance"].cap == 0.6
    assert {k: caps[k].cap for k in ("build_error", "shader_error", "console_error", "dark_frame", "blown_frame", "flat_frame", "content_small")} == {
        "build_error": 0.0, "shader_error": 0.5, "console_error": 0.6, "dark_frame": 0.55, "blown_frame": 0.6, "flat_frame": 0.65, "content_small": 0.6}
    defects = {d.id: (d.penalty, d.cap) for d in S.defects}
    assert {k: defects[k] for k in ("floating_asset", "sunken_asset", "interpenetration", "wrong_scale", "hard_edged_particles",
                                    "missing_planned_element", "hero_misplaced", "diorama_look")} == {
        "floating_asset": (0.10, 0.60), "sunken_asset": (0.08, 0.65), "interpenetration": (0.08, 0.70), "wrong_scale": (0.06, 0.75),
        "hard_edged_particles": (0.04, None), "missing_planned_element": (0.06, None), "hero_misplaced": (0.06, None),
        "diorama_look": (0.05, None)}
    # the object rubrics did not move: no veto_warn_keys, the same content hash as their YAML
    for name in ("static_object", "articulated", "reference"):
        assert all(not c.veto_warn_keys for c in load_rubric(name).caps), name


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
    text = msgs[0].parts[0].text
    assert "scene_placement=FAIL" in text and "MEASURED PLACEMENT (scene_placement gate" in text
    assert "1 floating (worst 0.12 m), 0 sunken, 1 unsupported (worst 0.04 m), 1 interpenetrating (worst 15% of the smaller box)" in text
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
