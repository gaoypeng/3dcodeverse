"""The shared render/tool core: `spatial._render_common`, the sheet + gif writers,
`measure.solid_parts`, `tool_common` helpers and the central ToolUnavailable
boundary.  Each test pins one merge that removed a duplicate implementation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from codeverse.conventions import OBJECT_VIEWS, SCENE_VIEWS
from codeverse.spatial import _render_common as rc
from codeverse.spatial.registry import ToolContext, ToolUnavailable, get_tool
from codeverse.workspace import Workspace


# --------------------------------------------------------------------------- _render_common
def test_out_directory_creates_and_clears_stale_side_cars(tmp_path: Path) -> None:
    d = tmp_path / "a" / "b"
    (tmp_path / "a").mkdir()
    d.mkdir()
    (d / "metrics.json").write_text('{"stale": true}')
    (d / "keep.png").write_bytes(b"x")
    out = rc.out_directory(d, clean=("metrics.json", "views.json"))
    assert out == d.resolve() and out.is_dir()
    assert not (d / "metrics.json").exists() and (d / "keep.png").is_file()
    # creating a missing directory is the common case
    assert rc.out_directory(tmp_path / "fresh").is_dir()


def test_view_specs_is_the_one_camera_payload() -> None:
    specs = rc.view_specs(OBJECT_VIEWS)
    assert [s["name"] for s in specs] == [v.name for v in OBJECT_VIEWS]
    assert all(set(s) == {"name", "azimuth", "elevation"} for s in specs)
    assert all(isinstance(s["azimuth"], float) and isinstance(s["elevation"], float) for s in specs)
    # what the scene driver receives is exactly this, JSON-encoded
    from codeverse.spatial.render_scene import _views_json

    assert json.loads(_views_json(SCENE_VIEWS)) == rc.view_specs(SCENE_VIEWS)


def test_read_json_never_raises(tmp_path: Path) -> None:
    assert rc.read_json(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert rc.read_json(bad) == {}
    good = tmp_path / "g.json"
    good.write_text('{"a": 1}')
    assert rc.read_json(good) == {"a": 1}


def test_build_sheet_uses_the_settings_grid_and_guards_empty(tmp_path: Path) -> None:
    assert rc.build_sheet([], tmp_path / "none.png") is None
    for i in range(2):
        Image.new("RGB", (64, 64), (10 * i, 0, 0)).save(tmp_path / f"v{i}.png")
    out = rc.build_sheet([(f"v{i}", tmp_path / f"v{i}.png") for i in range(2)], tmp_path / "sheet.png")
    assert out and Path(out).is_file()
    from codeverse.config import get_settings

    tile = get_settings().render.sheet_tile
    with Image.open(out) as im:
        assert im.width == 2 * (tile + 6) + 6      # two columns at the configured tile size


# --------------------------------------------------------------------------- sheet: cells + gif
def test_contact_sheet_keeps_a_non_square_cell(tmp_path: Path) -> None:
    from codeverse.spatial.sheet import LABEL_H, PAD, contact_sheet, tile_size

    src = tmp_path / "wide.png"
    Image.new("RGB", (1280, 720), (30, 60, 90)).save(src)
    assert tile_size(480, sample=src) == (480, 270)
    assert tile_size(480) == (480, 480) and tile_size((300, 100)) == (300, 100)
    out = contact_sheet([("t=0s", src), ("t=1s", src)], tmp_path / "s.png", cols=2, tile=(480, 270))
    with Image.open(out) as im:
        assert im.size == (2 * (480 + PAD) + PAD, (270 + LABEL_H + PAD) + PAD)


def test_gl_contact_sheet_and_gif_go_through_the_shared_writers(tmp_path: Path) -> None:
    from codeverse.spatial.gl_render import GlFrame, write_contact_sheet, write_gif
    from codeverse.spatial.sheet import LABEL_H, PAD

    frames = []
    for i in range(3):
        p = tmp_path / f"f{i}.png"
        Image.new("RGB", (640, 360), (20 * i, 40, 60)).save(p)
        frames.append(GlFrame(index=i, time=i * 1.5, path=str(p)))
    sheet = write_contact_sheet(frames, tmp_path / "sheet.png", cols=2, tile_w=320)
    with Image.open(sheet) as im:                      # 16:9 cells, 2 columns, 2 rows
        assert im.size == (2 * (320 + PAD) + PAD, 2 * (180 + LABEL_H + PAD) + PAD)
    gif = write_gif(frames, tmp_path / "p.gif", width=160, fps=6)
    assert gif and Path(gif).is_file()
    with Image.open(gif) as im:
        assert im.n_frames == 3 and im.width == 160
    assert write_gif(frames[:1], tmp_path / "one.gif") is None      # < 2 frames = no preview


def test_turntable_gif_uses_the_same_writer(tmp_path: Path) -> None:
    from codeverse.spatial.turntable import assemble_turntable

    paths = []
    for i in range(4):
        p = tmp_path / f"tt{i}.png"
        Image.new("RGB", (64, 64), (10 * i, 0, 0)).save(p)
        paths.append(p)
    out = assemble_turntable(paths, tmp_path / "tt.gif", fps=8)
    with Image.open(out) as im:
        assert im.n_frames == 4 and im.width == 64


# --------------------------------------------------------------------------- parts loader
def test_solid_parts_is_cached_parts_without_the_empty_ones(stool_glb: Path) -> None:
    from codeverse.spatial.measure import cached_parts, solid_parts

    solid = solid_parts(stool_glb)
    assert solid and list(solid) == [k for k, v in cached_parts(stool_glb).items() if v is not None and len(v.faces)]
    assert all(v is not None and len(v.faces) for v in solid.values())


def test_shared_number_formatters() -> None:
    from codeverse.spatial.connectivity import _fmt_vec as conn_vec
    from codeverse.spatial.contract import _fmt_ext, _fmt_vec
    from codeverse.spatial.measure import fmt_extent_cm, fmt_vec

    assert _fmt_ext is fmt_extent_cm and fmt_extent_cm([0.34, 0.47]) == "34.0×47.0"
    assert _fmt_vec([-0.00001, 0.5, 0]) == "(+0.000, +0.500, +0.000)"     # never '-0.000'
    assert conn_vec((-0.00001, 0.5, 0.0)) == "(+0.0000, +0.5000, +0.0000)"  # 4 decimals, same shape
    assert fmt_vec([1.23456], digits=2) == "(+1.23)"


# --------------------------------------------------------------------------- tool plumbing
def test_tool_out_dir_is_round_stamped(stool_ctx: ToolContext) -> None:
    from codeverse.spatial.tool_common import render_cache_dir, tool_out_dir

    d = tool_out_dir(stool_ctx, "sections")
    assert d.is_dir() and d.name == f"r{stool_ctx.round_index:02d}_sections"
    assert d.parent == stool_ctx.workspace.artifacts / "tool_renders"
    glb = stool_ctx.workspace.artifacts / "object.glb"
    a = render_cache_dir(stool_ctx, glb, mode="shaded")
    b = render_cache_dir(stool_ctx, glb, mode="clay")
    assert a != b and a.is_dir() and a.parent == d.parent


def test_tool_unavailable_is_reported_by_the_registry(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """No tool catches ToolUnavailable itself any more — ToolDef.call does it for
    all of them, with the tool's own registered name."""
    import codeverse.spatial.tool_common as tc

    def boom(module, attr):
        raise ToolUnavailable(f"{module} not importable")

    monkeypatch.setattr(tc, "lazy", boom)
    monkeypatch.setattr("codeverse.spatial.tools.lazy", boom)
    monkeypatch.setattr("codeverse.spatial.tools_scene.lazy", boom)
    monkeypatch.setattr("codeverse.spatial.tools_graphics.lazy", boom)
    monkeypatch.setattr("codeverse.spatial.tools_texture.lazy", boom)
    ws = stool_ctx.workspace
    (ws.artifacts / "object_textured.glb").write_bytes((ws.artifacts / "object.glb").read_bytes())
    for name in ("build", "render_views", "scene_probe", "texture_preview"):
        obs = get_tool(name).call(stool_ctx, {})
        assert not obs.ok and obs.text.startswith(f"tool {name} unavailable:"), (name, obs.text)
    # a graphics workspace: the gl tools degrade the same way
    gl_ctx = ToolContext(workspace=stool_ctx.workspace, language="glsl_shader", track="graphics")
    obs = get_tool("gl_probe").call(gl_ctx, {})
    assert not obs.ok and obs.text.startswith("tool gl_probe unavailable:"), obs.text


def test_gl_metrics_summary_is_the_one_frame_stats_formatter(tmp_ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
    from codeverse.spatial.frame_stats import FrameStat, SequenceStats
    from codeverse.spatial.tool_common import gl_metrics_summary

    stats = SequenceStats(frames=[FrameStat(time=0.0, path="f0.png", mean_lum=0.4, std_lum=0.2, pct_black=0.0,
                                            pct_blown=0.0, colourfulness=0.3, edge_density=0.05)], mean_diff=0.0)
    gate = GateReport(gate="gl_frames", passed=False, findings=[
        GateFinding(gate="gl_frames", severity=Severity.ERROR, message=f"static image in {tmp_ws.root}/src",
                    fix_hint="animate with u_time", data={"kind": "static"}),
        GateFinding(gate="gl_frames", severity=Severity.INFO, message="ignored"),
    ])
    monkeypatch.setattr("codeverse.languages._gl_common.read_metrics", lambda ws: (stats, gate))
    lines, numbers, ok = gl_metrics_summary(tmp_ws, hints=True, root=tmp_ws.root)
    body = "\n".join(lines)
    assert not ok and "frames=1" in body and "[static]" in body and "fix: animate with u_time" in body
    assert "ignored" not in body and str(tmp_ws.root) not in body      # INFO dropped, paths sanitised
    assert numbers["gate_errors"] == 1 and numbers["n_frames"] == 1 and numbers["gate_passed"] is False
    no_hints, _, _ = gl_metrics_summary(tmp_ws, hints=False)
    assert "fix:" not in "\n".join(no_hints)
    monkeypatch.setattr("codeverse.languages._gl_common.read_metrics", lambda ws: None)
    assert gl_metrics_summary(tmp_ws) == (["(no frame metrics)"], {}, True)


def test_render_scene_still_exports_runtime_js_dir() -> None:
    """Public symbol kept while the definition moved to spatial.node."""
    from codeverse.spatial import node, render_scene

    assert render_scene.runtime_js_dir() == node.runtime_js_dir()
