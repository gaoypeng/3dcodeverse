"""Browser-backed: render_scene → camera_checks coverage, judge subset sheet, scene_frames gate.

The example starter scene must pass; a crushed-dark variant of it must raise
``dark_frame`` ERRORs with the lighting hint.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from codeverse.conventions import SCENE_VIEWS
from codeverse.spatial.frame_metrics import frame_gate_from_renders
from codeverse.spatial.render_scene import (
    JUDGE_MAX_VIEWS,
    read_metrics,
    render_scene,
    select_judge_views,
)
from codeverse.workspace import Workspace
from tests.scene_runtime.conftest import needs_browser

pytestmark = [pytest.mark.node, needs_browser]


def _darken(ws: Workspace) -> None:
    env = ws.src / "env.js"
    text = env.read_text()
    text = text.replace("new THREE.DirectionalLight(0xfff1d6, 2.6)", "new THREE.DirectionalLight(0xfff1d6, 0.05)")
    text = text.replace("new THREE.HemisphereLight(0xbcd7ff, 0x4a5a2a, 0.9)", "new THREE.HemisphereLight(0x101018, 0x000000, 0.02)")
    text = text.replace("scene.background = new THREE.Color(0xcfdcec)", "scene.background = new THREE.Color(0x000000)")
    assert "0.05" in text and "0x000000" in text
    env.write_text(text)
    sky = ws.src / "shaders" / "sky.js"
    if sky.is_file():
        sky.write_text(sky.read_text().replace("export function", "export function /*dark*/"))


def test_example_scene_passes_frame_gate_and_judge_subset(starter_ws: Workspace):
    out = starter_ws.renders_dir(0)
    rs = render_scene(starter_ws, out, times=(0.0, 1.5), width=640, height=360, fps_seconds=0.3)
    assert rs.console_errors == []
    assert len(rs.views) == 2 * (3 + len(SCENE_VIEWS))          # full set on disk
    m = read_metrics(out)
    chk = {c["name"]: c for c in m["camera_checks"]}
    for c in chk.values():
        assert 0.0 <= c["content_frac"] <= 1.0 and abs(c["content_frac"] + c["ground_frac"] + c["sky_frac"] - 1.0) < 0.02
    assert chk["overview"]["content_frac"] > 0.2                  # authored establishing shot shows the grove
    assert chk["overview_top"]["ground_frac"] > 0.5               # top-down: mostly ground between the trees
    assert m["framing_bbox"]["size"][0] < m["census"]["bbox"]["size"][0]  # content, not the sky dome / ground
    gate = frame_gate_from_renders(rs)
    assert gate.gate == "scene_frames" and gate.passed, [f.message for f in gate.findings]
    # judge subset: ≤ 10 views, marked in views.json, sheet built from them only
    judge = select_judge_views(rs)
    assert len(judge.views) == JUDGE_MAX_VIEWS
    flags = {(v["name"], v["time_s"]) for v in json.loads((out / "views.json").read_text()) if v.get("judge")}
    assert flags == {(v.name, v.time_s) for v in judge.views}
    sheet = Image.open(rs.contact_sheet)
    tile_h = Image.open(rs.views[0].path).size[1]
    assert sheet.size[1] < 5 * tile_h                             # 10 tiles in 4 columns → 3 rows, not 5


def test_dark_variant_raises_dark_frame_errors(starter_ws: Workspace):
    _darken(starter_ws)
    out = starter_ws.renders_dir(1)
    rs = render_scene(starter_ws, out, times=(0.0,), orbit=False, width=480, height=270, fps_seconds=0)
    assert rs.views, rs.console_errors
    gate = frame_gate_from_renders(out)
    assert not gate.passed
    dark = [f for f in gate.findings if f.data.get("kind") == "dark_frame"]
    assert dark and all(f.severity == "error" for f in dark)
    assert dark[0].target == "overview" and "HemisphereLight" in dark[0].fix_hint and "NOT black" in dark[0].fix_hint
    assert dark[0].data["mean_lum"] < 0.12 or dark[0].data["dark_frac"] > 0.35


def test_plan_bounds_guard_orbit_framing(starter_ws: Workspace, tmp_path: Path):
    """Plan bounds guard the framing: a zone that sprawls far past the bounds is dropped in
    favour of the zones inside them; bounds that contain everything change nothing."""
    from codeverse.contracts.plan import BBox

    def _run(i: int, extents: float) -> tuple[dict, float]:
        out = starter_ws.renders_dir(i)
        rs = render_scene(starter_ws, out, times=(0.0,), orbit_views=SCENE_VIEWS[:1], width=320, height=180, fps_seconds=0,
                          bounds=BBox(center=(0, 3, 0), extents=(extents, 10, extents)), sheet=False)
        ov = next(v for v in rs.views if v.name == SCENE_VIEWS[0].name)
        return read_metrics(out)["framing_bbox"], ov.camera_position[1]

    wide, y_wide = _run(2, 90.0)         # meadow (≈ 84 m) fits → framed as a whole
    assert wide["size"][0] == pytest.approx(84.4, abs=0.5)
    tight, y_tight = _run(3, 30.0)       # meadow sprawls past 33 m → the pondside zone (16.7 m) is framed
    assert 10 < tight["size"][0] < 17.5
    assert y_tight < y_wide / 2
