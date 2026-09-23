"""Render caching: one cache authority, keyed by content, safe under concurrent writers."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.conventions import OBJECT_VIEWS
from codeverse3d.spatial.registry import ToolContext
from codeverse3d.workspace import Workspace


def test_cached_render_glb_leaves_the_cache_to_render_glb(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """``render.render_glb`` (content hash) is the one cache authority: a same-size, same-mtime rebuild re-renders."""
    import codeverse3d.spatial.tool_common as tc
    from codeverse3d.contracts.artifacts import RenderSet, RenderView

    calls: list[Path] = []

    def fake_render_glb(glb, out_dir, *, views, mode, width, height, isolate, explode, sheet):
        calls.append(Path(out_dir))
        png = Path(out_dir) / f"{views[0].name}.png"
        png.write_bytes(b"png")
        return RenderSet(views=[RenderView(name=views[0].name, path=str(png))], contact_sheet=None)

    monkeypatch.setattr("codeverse3d.spatial.render.render_glb", fake_render_glb)
    glb = stool_ctx.workspace.artifacts / "object.glb"
    preset = OBJECT_VIEWS[0]
    first = tc.cached_render_glb(stool_ctx, glb, views=[preset])
    stamp = glb.stat()
    glb.write_bytes(glb.read_bytes()[::-1])                      # new content, same size
    import os

    os.utime(glb, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))     # ...and the same mtime
    second = tc.cached_render_glb(stool_ctx, glb, views=[preset])
    assert len(calls) == 2 and calls[0] == calls[1], calls       # re-rendered into the same dir
    assert first.views[0].path == second.views[0].path
    assert not list(calls[0].glob("renderset.json"))             # no second marker on disk


def test_store_in_cache_survives_a_concurrent_identical_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two writers of one cache entry (a judge fan-out): no exception, and the cache ends up complete."""
    import shutil
    import threading
    import time

    from codeverse3d.spatial import render as R

    out = tmp_path / "out"
    out.mkdir()
    record = {"views": [{"name": f"v{i}"} for i in range(6)]}
    for i in range(6):
        (out / f"view_v{i}.png").write_bytes(b"x" * 1000)
    cache_dir = tmp_path / "cache" / "deadbeefdeadbeefdeadbeef"
    cache_dir.parent.mkdir(parents=True)

    orig = shutil.copy2
    monkeypatch.setattr(R.shutil, "copy2", lambda src, dst, **kw: (time.sleep(0.03), orig(src, dst, **kw))[1])

    errs: list[str] = []

    def store(delay: float) -> None:
        time.sleep(delay)
        try:
            R._store_in_cache(cache_dir, out, record)
        except Exception as e:  # noqa: BLE001 — the failure mode under test
            errs.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=store, args=(d,)) for d in (0.0, 0.08)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errs == []
    assert (cache_dir / "views.json").is_file()
    assert sorted(p.name for p in cache_dir.glob("view_*.png")) == [f"view_v{i}.png" for i in range(6)]
    assert not list(cache_dir.parent.glob("*.tmp")), "no tmp dirs left behind"


def test_gl_metrics_summary_is_the_one_frame_stats_formatter(tmp_ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
    from codeverse3d.spatial.frame_stats import FrameStat, SequenceStats
    from codeverse3d.spatial.tool_common import gl_metrics_summary

    stats = SequenceStats(frames=[FrameStat(time=0.0, path="f0.png", mean_lum=0.4, std_lum=0.2, pct_black=0.0,
                                            pct_blown=0.0, colourfulness=0.3, edge_density=0.05)], mean_diff=0.0)
    gate = GateReport(gate="gl_frames", passed=False, findings=[
        GateFinding(gate="gl_frames", severity=Severity.ERROR, message=f"static image in {tmp_ws.root}/src",
                    fix_hint="animate with u_time", data={"kind": "static"}),
        GateFinding(gate="gl_frames", severity=Severity.INFO, message="ignored"),
    ])
    monkeypatch.setattr("codeverse3d.languages._gl_common.read_metrics", lambda ws: (stats, gate))
    lines, numbers, ok = gl_metrics_summary(tmp_ws, hints=True, root=tmp_ws.root)
    body = "\n".join(lines)
    assert not ok and "frames=1" in body and "[static]" in body and "fix: animate with u_time" in body
    assert "ignored" not in body and str(tmp_ws.root) not in body      # INFO dropped, paths sanitised
    assert numbers["gate_errors"] == 1 and numbers["n_frames"] == 1 and numbers["gate_passed"] is False
    no_hints, _, _ = gl_metrics_summary(tmp_ws, hints=False)
    assert "fix:" not in "\n".join(no_hints)
    monkeypatch.setattr("codeverse3d.languages._gl_common.read_metrics", lambda ws: None)
    assert gl_metrics_summary(tmp_ws) == (["(no frame metrics)"], {}, True)
