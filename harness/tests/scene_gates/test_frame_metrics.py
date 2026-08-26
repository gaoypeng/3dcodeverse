"""scene_frames gate: synthetic camera_checks → findings, hints, caps (pure python)."""

from __future__ import annotations

import json
from pathlib import Path

from codeverse.contracts.artifacts import RenderSet, RenderView, Severity
from codeverse.judges.caps import apply_caps
from codeverse.judges.rubrics import load_rubric
from codeverse.spatial.frame_metrics import (
    FRAME_GATE,
    frame_findings,
    frame_gate_from_renders,
    frame_summary_text,
)


def _chk(name: str, kind: str = "authored", **over):
    base = {"name": name, "kind": kind, "nearest_hit_m": 6.0, "nearest_hit_name": "Ground", "inside_mesh_bbox": [],
            "camera_in_geometry": False, "eye_height_m": 1.6, "mean_lum": 0.35, "lum_std": 0.1, "dark_frac": 0.02,
            "blown_frac": 0.0, "modal_frac": 0.4, "content_frac": 0.45, "ground_frac": 0.3, "sky_frac": 0.25}
    base.update(over)
    return base


def _metrics(*checks, ground_y=0.0):
    return {"camera_checks": list(checks), "census": {"ground_y": ground_y}}


def _kinds(report):
    return [(f.data["kind"], f.severity, f.target) for f in report.findings]


def test_clean_frames_pass_with_info():
    rep = frame_findings(_metrics(_chk("Establishing"), _chk("overview_top", "orbit")))
    assert rep.gate == FRAME_GATE and rep.passed
    assert [f.data["kind"] for f in rep.findings] == ["frames_ok"]


def test_dark_frame_is_error_on_authored_with_concrete_hint():
    rep = frame_findings(_metrics(_chk("Establishing", mean_lum=0.07, dark_frac=0.41)))
    assert not rep.passed
    f = rep.findings[0]
    assert f.data["kind"] == "dark_frame" and f.severity == Severity.ERROR and f.target == "Establishing"
    assert "0.07" in f.message and "41%" in f.message
    assert "DirectionalLight" in f.fix_hint and "HemisphereLight" in f.fix_hint and "NOT black" in f.fix_hint
    assert f.data["view"] == "Establishing" and f.data["mean_lum"] == 0.07


def test_dark_frame_on_orbit_view_only_warns():
    rep = frame_findings(_metrics(_chk("eye_front", "orbit", mean_lum=0.04, dark_frac=0.77)))
    assert rep.passed
    assert _kinds(rep) == [("dark_frame", Severity.WARN, "eye_front")]


def test_blown_and_flat_frames():
    rep = frame_findings(_metrics(_chk("Cam", blown_frac=0.3), _chk("Flat", modal_frac=0.95), _chk("Soft", modal_frac=0.88)))
    kinds = {(k, s) for k, s, _ in _kinds(rep)}
    assert ("blown_frame", Severity.ERROR) in kinds
    assert ("flat_frame", Severity.ERROR) in kinds  # > 0.92 on an authored camera
    assert ("flat_frame", Severity.WARN) in kinds  # 0.85-0.92
    assert not rep.passed


def test_flat_is_not_duplicated_on_dark_frames():
    rep = frame_findings(_metrics(_chk("Night", mean_lum=0.05, dark_frac=0.9, modal_frac=1.0)))
    assert [k for k, _, _ in _kinds(rep)] == ["dark_frame"]


def test_camera_in_geometry_and_near_hit():
    rep = frame_findings(_metrics(_chk("Buried", camera_in_geometry=True, inside_mesh_bbox=["Tower"], nearest_hit_m=0.1),
                                  _chk("Close", nearest_hit_m=0.3)))
    assert [(k, s) for k, s, _ in _kinds(rep)] == [("camera_in_geometry", Severity.ERROR)] * 2
    assert "Tower" in rep.findings[0].message and "0.5 m" in rep.findings[0].fix_hint


def test_eye_height_sanity_against_ground():
    # a camera below the scene's highest ground surface whose frame renders fine is NOT
    # buried — census ground_y is the TOP of every ground mesh, so a hill or a raised bed
    # puts it above a camera standing in the open (measured: this false ERROR capped a
    # finished japanese garden at 0.50)
    rep = frame_findings(_metrics(_chk("Under", eye_height_m=-0.5), _chk("Ant", eye_height_m=0.1), _chk("Sat", eye_height_m=120.0),
                                  ground_y=0.0))
    assert [(k, s) for k, s, _ in _kinds(rep)] == [
        ("camera_below_high_ground", Severity.WARN), ("camera_low", Severity.WARN), ("camera_high", Severity.WARN)]
    assert "heightAt" in rep.findings[0].fix_hint
    # …but when the frame agrees (dark / empty / inside geometry) it stays a capped ERROR
    buried = frame_findings(_metrics(_chk("Under", eye_height_m=-0.5, mean_lum=0.03, dark_frac=0.9), ground_y=0.0))
    kinds = [k for k, _, _ in _kinds(buried)]
    assert "camera_underground" in kinds and not buried.passed


def test_content_small_roles():
    rep = frame_findings(_metrics(
        _chk("Establishing", content_frac=0.12, sky_frac=0.5, ground_frac=0.38),  # first authored = establishing → ERROR
        _chk("Detail", content_frac=0.05),                                           # other authored → WARN
        _chk("overview_top", "orbit", content_frac=0.03),                            # overview rig → WARN
        _chk("eye_front", "orbit", content_frac=0.01),                               # eye rig: never
    ))
    got = _kinds(rep)
    assert got == [("content_small", Severity.ERROR, "Establishing"), ("content_small", Severity.WARN, "Detail"),
                   ("content_small", Severity.WARN, "overview_top")]
    assert "12%" in rep.findings[0].message and "sky 50%" in rep.findings[0].message
    assert rep.findings[0].data["role"] == "establishing" and not rep.passed


def test_missing_coverage_metric_is_tolerated():
    c = _chk("Establishing")
    for k in ("content_frac", "ground_frac", "sky_frac"):
        c.pop(k)
    assert frame_findings(_metrics(c)).passed


def test_frame_gate_from_paths_and_renderset(tmp_path: Path):
    out = tmp_path / "r00"
    out.mkdir()
    (out / "metrics.json").write_text(json.dumps(_metrics(_chk("Establishing", mean_lum=0.05))))
    (out / "Establishing_t0.png").write_bytes(b"")
    assert not frame_gate_from_renders(out).passed
    assert not frame_gate_from_renders(out / "metrics.json").passed
    rs = RenderSet(views=[RenderView(name="Establishing", path=str(out / "Establishing_t0.png"))])
    assert not frame_gate_from_renders(rs).passed
    assert frame_gate_from_renders(tmp_path / "nowhere").passed  # no metrics → empty passing report
    assert frame_gate_from_renders(RenderSet()).passed


def test_scene_rubric_caps_fire_on_frame_kinds():
    rubric = load_rubric("scene_v1")
    rep = frame_findings(_metrics(_chk("Establishing", mean_lum=0.07, dark_frac=0.41, content_frac=0.1)))
    res = apply_caps(rubric, 0.9, [rep], {}, [])
    fired = {c.rule: c.cap for c in res.caps_applied}
    assert fired == {"dark_frame": 0.55, "content_small": 0.6}
    assert res.overall == 0.55
    rep2 = frame_findings(_metrics(_chk("Cam", blown_frac=0.5), _chk("Flat", modal_frac=0.99)))
    fired2 = {c.rule for c in apply_caps(rubric, 0.9, [rep2], {}, []).caps_applied}
    assert {"blown_frame", "flat_frame"} <= fired2
    # orbit-only warnings never cap
    rep3 = frame_findings(_metrics(_chk("overview_top", "orbit", mean_lum=0.05)))
    assert apply_caps(rubric, 0.9, [rep3], {}, []).caps_applied == []


def test_frame_summary_text_table():
    txt = frame_summary_text(_metrics(_chk("Establishing", mean_lum=0.07, dark_frac=0.41), _chk("overview_top", "orbit")))
    lines = txt.splitlines()
    assert lines[0].startswith("frame checks (FAILED")
    assert lines[1].startswith("Establishing [authored]: lum 0.07 dark 41%") and lines[1].endswith("— DARK FRAME")
    assert "content 45%" in lines[2] and "—" not in lines[2]
    assert frame_summary_text({}) == "frame checks: no camera_checks in metrics"


# ------------------------------------------------- a hero that is loaded and never placed
def test_a_loaded_glb_that_reaches_no_frame_is_flagged():
    """Measured 2026-08-25 on tsr_scn_boat_workshop_v2: src/scene.js loads
    /assets/clinker_skiff.glb — a real Blender hero, authored, built, copied into
    public/assets — while src/zones/central_bay.js calls a procedural buildClinkerSkiff().
    The hull in every shipped frame is JavaScript. Nothing flagged it, and the check the
    teaser wave used to verify the multi-language claim (plan.json -> assets[].kind) still
    said blender_glb, because the plan records what was PLANNED, not what rendered."""
    from codeverse.contracts.artifacts import Severity
    from codeverse.spatial.frame_metrics import frame_findings

    rep = frame_findings({"camera_checks": [], "census": {"glb_assets": [
        {"url": "/assets/clinker_skiff.glb", "meshes": 7, "meshes_in_scene": 0, "in_scene": False},
        {"url": "/assets/potbelly_stove.glb", "meshes": 5, "meshes_in_scene": 5, "in_scene": True},
    ]}})
    bad = [f for f in rep.findings if f.data["kind"] == "unused_glb_asset"]
    assert len(bad) == 1, "only the unused one is a finding"
    assert "clinker_skiff" in bad[0].message
    assert bad[0].severity is Severity.WARN, "the picture still renders; this must not fail the run"
    assert rep.passed, "a WARN must not fail the gate"


def test_a_scene_with_no_glbs_says_nothing():
    """Most scenes load no GLB at all; they must not gain a finding for it."""
    from codeverse.spatial.frame_metrics import frame_findings

    for census in ({}, {"glb_assets": []}, {"glb_assets": "not a list"}):
        rep = frame_findings({"camera_checks": [], "census": census})
        assert not [f for f in rep.findings if f.data["kind"] == "unused_glb_asset"]
