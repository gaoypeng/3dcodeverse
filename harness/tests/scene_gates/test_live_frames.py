"""Browser-backed: render_scene → camera_checks coverage, judge subset sheet, scene_frames gate.

The example starter scene must pass; a crushed-dark variant of it must raise
``dark_frame`` ERRORs with the lighting hint.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from codeverse3d.conventions import SCENE_VIEWS
from codeverse3d.proc import read_json_or_none
from codeverse3d.spatial.frame_metrics import frame_gate_from_renders
from codeverse3d.spatial.render_scene import (
    JUDGE_MAX_VIEWS,
    render_scene,
    select_judge_views,
)
from codeverse3d.workspace import Workspace
from tests.scene_runtime.conftest import needs_browser

pytestmark = [pytest.mark.node, needs_browser]


def _darken(ws: Workspace) -> None:
    env = ws.src / "env.js"
    text = env.read_text()
    # the starter lights through lib/environment.js sunRig (2026-09-07): crush the rig's sun and
    # fill AFTER it is built (its `fill` option is clamped UP to a readability floor) and drop
    # the env map, which lights metals and glass on its own
    text = text.replace("  scene.environment = rig.envTex;",
                        "  scene.environment = null; sun.intensity = 0.05; hemi.intensity = 0.02; hemi.color.set(0x101018); hemi.groundColor.set(0x000000);")
    # the world shell's sky is unlit (D71): black it out too, or half the frame stays bright sky
    text = text.replace("worldShell({ rand: mulberry32(SEED + 1), mood: MOOD,",
                        "worldShell({ rand: mulberry32(SEED + 1), mood: 'night', zenith: 0x000000, horizon: 0x000000, ridgeColor: 0x000000,")
    text = text.replace("scene.background = new THREE.Color(shell.fog.color)", "scene.background = new THREE.Color(0x000000)")
    assert "0.05" in text and text.count("0x000000") >= 4
    env.write_text(text)


def test_example_scene_passes_frame_gate_and_judge_subset(starter_ws: Workspace):
    out = starter_ws.renders_dir(0)
    rs = render_scene(starter_ws, out, times=(0.0, 1.5), width=640, height=360, fps_seconds=0.3)
    assert rs.console_errors == []
    names = {v.name for v in rs.views}
    assert {"overview", "pond_low", "windmill"} <= names
    assert {v.name for v in SCENE_VIEWS} <= names
    assert len(rs.views) == 2 * (3 + len(SCENE_VIEWS))          # full set on disk
    assert rs.fps and rs.fps > 5 and rs.renderer
    im = Image.open(rs.views[0].path)
    assert im.size == (640, 360)
    px = im.convert("L").resize((32, 18)).tobytes()
    assert 20 < sum(px) / len(px) < 235                          # lit, neither black nor blown
    m = read_json_or_none(out / "metrics.json")
    assert m["census"]["totals"]["triangles"] > 1000
    chk = {c["name"]: c for c in m["camera_checks"]}
    for c in chk.values():
        assert 0.0 <= c["content_frac"] <= 1.0 and abs(c["content_frac"] + c["ground_frac"] + c["sky_frac"] - 1.0) < 0.02
    assert not chk["overview"]["camera_in_geometry"]
    assert chk["overview"]["dark_frac"] < 0.2 and chk["overview"]["blown_frac"] < 0.2
    assert chk["overview"]["content_frac"] > 0.2                  # authored establishing shot shows the grove
    assert chk["overview_top"]["ground_frac"] > 0.5               # top-down: mostly ground between the trees
    assert m["framing_bbox"]["size"][0] < m["census"]["bbox"]["size"][0]  # content, not the sky dome / ground
    gate = frame_gate_from_renders(rs)
    assert gate.gate == "scene_frames" and gate.passed, [f.message for f in gate.findings]
    # judge subset: stamped ONCE at render time (RenderView.judge + views.json), sheet built from it only
    assert rs.out_dir == str(out)
    assert all(v.judge is not None for v in rs.views)
    stamped = {(v.name, v.time_s) for v in rs.views if v.judge}
    # 3 authored at t=0 + the 3 overview-rig tiles + the first two authored at t=1.5;
    # the harness's eye-level rig is a diagnostic and never reaches the judge
    assert len(stamped) == 8 <= JUDGE_MAX_VIEWS
    assert not any(n.startswith("eye_") for n, _ in stamped)
    assert stamped == {(v.name, v.time_s) for v in select_judge_views(rs).views}
    # motion is measured from the written frames and persisted for the gate / judge context
    motion = {r["name"]: r for r in m["motion"]}
    assert motion and set(motion) == {v.name for v in rs.views}
    assert all(0.0 <= r["changed_frac"] <= 1.0 for r in motion.values())
    a = Image.open(next(v.path for v in rs.views if v.name == "windmill" and v.time_s == 0.0)).convert("L")
    b = Image.open(next(v.path for v in rs.views if v.name == "windmill" and v.time_s == 1.5)).convert("L")
    assert sum(1 for x, y in zip(a.tobytes(), b.tobytes(), strict=True) if abs(x - y) > 12) > 100
    entries = json.loads((out / "views.json").read_text())
    assert all("judge" in e for e in entries)
    assert {(v["name"], v["time_s"]) for v in entries if v["judge"]} == stamped
    assert rs.contact_sheet and Path(rs.contact_sheet).is_file()
    sheet = Image.open(rs.contact_sheet)
    tile_h = Image.open(rs.views[0].path).size[1]
    assert sheet.size[1] < 5 * tile_h                             # 8 tiles in 4 columns → 2 rows, not 5


def test_dark_variant_raises_dark_frame_errors(starter_ws: Workspace):
    _darken(starter_ws)
    out = starter_ws.renders_dir(1)
    rs = render_scene(starter_ws, out, times=(0.0,), orbit=False, width=480, height=270, fps_seconds=0)
    assert rs.views, rs.console_errors
    gate = frame_gate_from_renders(out)
    assert not gate.passed
    dark = [f for f in gate.findings if f.data.get("kind") == "dark_frame"]
    assert dark and all(f.severity == "error" for f in dark)
    assert dark[0].target == "overview" and "sunRig(" in dark[0].fix_hint and "NOT black" in dark[0].fix_hint
    assert dark[0].data["mean_lum"] < 0.12 or dark[0].data["dark_frac"] > 0.35


def test_plan_bounds_guard_orbit_framing(starter_ws: Workspace, tmp_path: Path):
    """Plan bounds guard the framing: a zone that sprawls far past the bounds is dropped in
    favour of the zones inside them; bounds that contain everything change nothing."""
    from codeverse3d.contracts.plan import BBox

    def _run(i: int, extents: float) -> tuple[dict, float]:
        out = starter_ws.renders_dir(i)
        rs = render_scene(starter_ws, out, times=(0.0,), orbit_views=SCENE_VIEWS[:1], width=320, height=180, fps_seconds=0,
                          bounds=BBox(center=(0, 3, 0), extents=(extents, 10, extents)), sheet=False)
        ov = next(v for v in rs.views if v.name == SCENE_VIEWS[0].name)
        return read_json_or_none(out / "metrics.json")["framing_bbox"], ov.camera_position[1]

    wide, y_wide = _run(2, 90.0)         # meadow (≈ 84 m) fits → framed as a whole
    assert wide["size"][0] == pytest.approx(84.4, abs=0.5)
    tight, y_tight = _run(3, 30.0)       # meadow sprawls past 33 m → the pondside zone (16.7 m) is framed
    assert 10 < tight["size"][0] < 17.5
    assert y_tight < y_wide / 2
