"""scene_frames gate: synthetic camera_checks → findings, hints, caps (pure python)."""

from __future__ import annotations

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.judges.rubrics import apply_caps, load_rubric
from codeverse3d.spatial.frame_metrics import (
    frame_findings,
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


def test_camera_in_geometry_and_near_hit():
    rep = frame_findings(_metrics(_chk("Buried", camera_in_geometry=True, inside_mesh_bbox=["Tower"], nearest_hit_m=0.1),
                                  _chk("Close", nearest_hit_m=0.3)))
    assert [(k, s) for k, s, _ in _kinds(rep)] == [("camera_in_geometry", Severity.ERROR)] * 2
    assert "Tower" in rep.findings[0].message and "0.5 m" in rep.findings[0].fix_hint
    # one camera, one gate (audit 2026-09-24 N11): the build's scene_probe used to judge the first
    # camera again (WARN at 0.3 m / 85 % dark) beside these ERRORs (0.5 m / 35 %) in the same round
    from codeverse3d.spatial.probes import probe_report

    cam0 = _chk("Buried", camera_in_geometry=True, inside_mesh_bbox=["Tower"], nearest_hit_m=0.1, dark_frac=0.9)
    probe, _ = probe_report({"boot": {"ok": True, "stage": "ready"}, "update_ok": True, "first_camera": cam0})
    assert not [f for f in probe.findings if f.target == "Buried"], [f.message for f in probe.findings]


def test_a_lens_whose_sight_rays_end_within_reach_is_blocked():
    rep = frame_findings(_metrics(_chk("AthanorDetail", nearest_hit_m=0.8, nearest_hit_name="StonePillar_3", near_rays=7, rays_total=9,
                                       near_limit_m=1.5),
                                  _chk("WorkTable", nearest_hit_m=0.9, nearest_hit_name="Bench", near_rays=2, rays_total=9),
                                  _chk("overview_top", kind="overview", nearest_hit_m=0.8, near_rays=9, rays_total=9)))
    assert [(k, s, t) for k, s, t in _kinds(rep)] == [("camera_blocked", Severity.ERROR, "AthanorDetail")]
    assert "StonePillar_3" in rep.findings[0].message and not rep.passed
    # few rays within reach but the line of sight to the target is cut: a WARN; a close-up stays quiet
    cut = frame_findings(_metrics(_chk("AthanorDetail", nearest_hit_m=0.897, near_rays=3, rays_total=9, target_distance_m=2.8,
                                       target_hit_m=0.9, target_hit_name="VaultPillarMasonry"),
                                  _chk("HeroCloseUp", nearest_hit_m=1.1, near_rays=4, rays_total=9, target_distance_m=1.6,
                                       target_hit_m=1.15, target_hit_name="Athanor")))
    assert [(k, s, t) for k, s, t in _kinds(cut)] == [("camera_target_blocked", Severity.WARN, "AthanorDetail")]
    assert "VaultPillarMasonry" in cut.findings[0].message and cut.passed


def test_a_lens_under_a_ground_surface_is_named_even_when_its_frame_looks_fine():
    rep = frame_findings(_metrics(_chk("SlipwaySurge", eye_height_m=2.0, ground_below_m=2.0, ground_below_name="Sea",
                                       ground_above_m=1.4, ground_above_name="HeadlandTerrain"), ground_y=4.8))
    assert [(k, s) for k, s, _ in _kinds(rep)] == [("camera_under_ground_mesh", Severity.WARN)]
    assert "HeadlandTerrain" in rep.findings[0].message
    buried = frame_findings(_metrics(_chk("SlipwaySurge", ground_above_m=1.4, ground_above_name="HeadlandTerrain",
                                          mean_lum=0.03, dark_frac=0.9), ground_y=4.8))
    assert "camera_underground" in [k for k, _, _ in _kinds(buried)] and not buried.passed


def test_eye_height_is_measured_under_the_lens_when_the_census_has_it():
    """Relief terrain: eye height is measured to the ground under the lens, not the highest ground."""
    rep = frame_findings(_metrics(_chk("Hill", eye_height_m=3.2, ground_below_m=1.62, ground_below_name="SnowTerrain"),
                                  _chk("Ant", eye_height_m=3.2, ground_below_m=0.06, ground_below_name="SnowTerrain"),
                                  _chk("Drone", eye_height_m=98.0, ground_below_m=95.0, ground_below_name="Ground"),
                                  ground_y=3.137))
    assert [(k, s) for k, s, _ in _kinds(rep)] == [("camera_low", Severity.WARN), ("camera_high", Severity.WARN)]
    assert "SnowTerrain" in rep.findings[0].message and rep.findings[0].data["view"] == "Ant"
    # nothing beneath the lens (under the terrain / off the ground's edge) → the scene-wide rule
    under = frame_findings(_metrics(_chk("Under", eye_height_m=-0.5, ground_below_m=None), ground_y=0.0))
    assert [k for k, _, _ in _kinds(under)] == ["camera_below_high_ground"]


def test_eye_height_sanity_against_ground():
    # below the highest ground (census ground_y is the TOP of every ground mesh) with a fine frame: only a WARN
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


def test_scene_rubric_caps_fire_on_frame_kinds():
    rubric = load_rubric("scene_v1")
    rep = frame_findings(_metrics(_chk("Establishing", mean_lum=0.07, dark_frac=0.41, content_frac=0.1)))
    res = apply_caps(rubric, 0.9, [rep], {}, [])
    fired = {c.rule: c.cap for c in res.caps_applied}
    assert fired == {"dark_frame": 0.55, "content_small": 0.6}
    assert res.overall == 0.55
    rep2 = frame_findings(_metrics(_chk("Cam", blown_frac=0.3), _chk("Flat", modal_frac=0.95), _chk("Soft", modal_frac=0.88)))
    kinds = {(k, s) for k, s, _ in _kinds(rep2)}
    assert ("blown_frame", Severity.ERROR) in kinds and not rep2.passed
    assert ("flat_frame", Severity.ERROR) in kinds  # > 0.92 on an authored camera
    assert ("flat_frame", Severity.WARN) in kinds   # 0.85-0.92
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


def test_a_loaded_glb_that_reaches_no_frame_is_flagged():
    rep = frame_findings({"camera_checks": [], "census": {"glb_assets": [
        {"url": "/assets/clinker_skiff.glb", "meshes": 7, "meshes_in_scene": 0, "in_scene": False},
        {"url": "/assets/potbelly_stove.glb", "meshes": 5, "meshes_in_scene": 5, "in_scene": True},
    ]}})
    bad = [f for f in rep.findings if f.data["kind"] == "unused_glb_asset"]
    assert len(bad) == 1, "only the unused one is a finding"
    assert "clinker_skiff" in bad[0].message
    assert bad[0].severity is Severity.WARN, "the picture still renders; this must not fail the run"
    assert rep.passed, "a WARN must not fail the gate"


def test_a_hero_in_the_scene_but_in_no_authored_frame_is_an_error():
    """``glb_frac`` is the mask render per GLB per camera."""
    url = "/assets/athanor.glb"
    census = {"ground_y": 0.0, "glb_assets": [{"url": url, "meshes": 9, "meshes_in_scene": 9, "in_scene": True, "size_m": 2.2}]}
    unseen = frame_findings({"census": census, "camera_checks": [
        _chk("AthanorDetail", glb_frac={url: 0.0}), _chk("StairsPushIn", glb_frac={url: 0.001}),
        _chk("overview_top", "orbit", glb_frac={url: 0.3})]})          # the rig sees it: not an authored shot
    kinds = _kinds(unseen)
    assert ("hero_unseen", Severity.ERROR, "athanor.glb") in kinds and not unseen.passed
    msg = next(f.message for f in unseen.findings if f.data["kind"] == "hero_unseen")
    assert "AthanorDetail 0.0%" in msg and "no camera sees the hero" in msg
    assert "(2.2 m across as placed)" in msg and "not small" in msg      # loop 23: 0.3 % read as "microscopic" without the size
    # seen well in one shot, but the camera NAMED for it (the host's hero_for) barely shows it → WARN on that camera
    small = frame_findings({"census": census, "camera_checks": [
        _chk("AthanorDetail", glb_frac={url: 0.004}, hero_for=[url]), _chk("StairsPushIn", glb_frac={url: 0.12})]})
    assert [(k, s, t) for k, s, t in _kinds(small)] == [("hero_small_in_its_camera", Severity.WARN, "AthanorDetail")]
    # the host's rule is the one rule: a name WORD of the file (lantern_room.glb → LanternDetail), which the
    # gate's own whole-stem match missed in 24 of 25 recorded camera/hero pairs
    lamp = "/assets/lantern_room.glb"
    lcensus = {"glb_assets": [{"url": lamp, "meshes": 3, "meshes_in_scene": 3, "in_scene": True}]}
    word = frame_findings({"census": lcensus, "camera_checks": [
        _chk("LanternDetail", glb_frac={lamp: 0.004}, hero_for=[lamp]), _chk("Harbour", glb_frac={lamp: 0.2})]})
    assert [(k, t) for k, _, t in _kinds(word)] == [("hero_small_in_its_camera", "LanternDetail")]
    # a well-framed hero, and an older census without glb_frac, say nothing
    fine = frame_findings({"census": census, "camera_checks": [_chk("AthanorDetail", glb_frac={url: 0.2})]})
    old = frame_findings({"census": census, "camera_checks": [_chk("AthanorDetail")]})
    assert fine.passed and old.passed and not [k for k, _, _ in _kinds(fine) + _kinds(old) if k.startswith("hero_")]


def test_auto_exposure_lifts_a_frame_past_the_gates_dark_line():
    """Regression (audit 2026-09-24 N13): the host's auto-exposure stopped at mean 0.10 while the
    gate calls < 0.12 "frame too dark", so a lifted frame still drew the ERROR.  Pinned by value."""
    import re

    from codeverse3d.spatial.frame_metrics import DARK_MEAN_LUM
    from codeverse3d.spatial.node import runtime_js_dir

    host = (runtime_js_dir() / "lib" / "scene_host.mjs").read_text()
    lo = float(re.search(r"const EXPOSURE_BAND = \[([\d.]+),", host).group(1))
    assert lo == DARK_MEAN_LUM


def test_a_blender_payload_gets_bpy_fix_hints_and_three_js_keeps_its_own():
    """The thresholds are one; the words are the language's (``metrics["language"]``)."""
    from codeverse3d.spatial.frame_metrics import HINTS

    dark = _chk("Establishing", mean_lum=0.04, lum_std=0.03, dark_frac=0.8)
    low = _chk("Street", eye_height_m=0.1, ground_below_m=0.1, ground_below_name="Ground")
    three = frame_findings(_metrics(dark, low))
    bpy = frame_findings({**_metrics(dark, low), "language": "scene_blender"})
    assert [(f.data["kind"], f.severity) for f in three.findings] == [(f.data["kind"], f.severity) for f in bpy.findings]
    t_hints = {f.data["kind"]: f.fix_hint for f in three.findings}
    b_hints = {f.data["kind"]: f.fix_hint for f in bpy.findings}
    assert t_hints["dark_frame"] == HINTS["scene_threejs"]["dark"] and "sunRig" in t_hints["dark_frame"]
    assert "sun.data.energy" in b_hints["dark_frame"] and "sunRig" not in b_hints["dark_frame"]
    assert "heightAt" in t_hints["camera_low"] and "ctx.height_at" in b_hints["camera_low"]
    assert set(HINTS["scene_blender"]) == set(HINTS["scene_threejs"]), "every hint has a bpy wording"


def test_a_slow_blender_frame_set_warns_and_names_the_heaviest_zone():
    """Owner D2: offline rendering has no draw budget; seconds per judged frame is its cost."""
    from codeverse3d.spatial.frame_metrics import SLOW_FRAME_CPU_FACTOR, SLOW_FRAME_S

    def payload(ms: float, device: str = "GPU") -> dict:
        views = [{"name": "A", "kind": "authored", "path": "a.png", "time_s": t, "position": [0, 1, 5],
                  "lookAt": [0, 0, 0], "fov": 50, "render_ms": ms} for t in (0.0, 1.5)]
        groups = [{"name": "Market", "kind": "content", "triangles": 900_000},
                  {"name": "Harbour", "kind": "content", "triangles": 120_000},
                  {"name": "Ground", "kind": "ground", "triangles": 5_000_000}]
        return {**_metrics(_chk("A")), "views": views, "census": {"ground_y": 0.0, "groups": groups},
                "language": "scene_blender", "blender": {"engine": "cycles", "samples": 32, "device": device}}

    slow = [f for f in frame_findings(payload(SLOW_FRAME_S * 1000 + 500)).findings if f.data.get("kind") == "slow_frame"]
    assert len(slow) == 1 and slow[0].severity == Severity.WARN and slow[0].target == "Market"
    assert "Market" in slow[0].message and "900,000" in slow[0].message
    assert not [f for f in frame_findings(payload(SLOW_FRAME_S * 1000 - 500)).findings if f.data.get("kind") == "slow_frame"]
    # the CPU fallback is ~3.3x slower by construction: the same frame there is not the scene's fault
    cpu = payload(SLOW_FRAME_S * 1000 + 500, device="CPU")
    assert not [f for f in frame_findings(cpu).findings if f.data.get("kind") == "slow_frame"]
    assert [f for f in frame_findings(payload(SLOW_FRAME_S * SLOW_FRAME_CPU_FACTOR * 1000 + 500, "CPU")).findings
            if f.data.get("kind") == "slow_frame"]
    # a three.js payload (no blender block) never reads render_ms
    three = {k: v for k, v in payload(60_000).items() if k not in ("blender", "language")}
    assert not [f for f in frame_findings(three).findings if f.data.get("kind") == "slow_frame"]
