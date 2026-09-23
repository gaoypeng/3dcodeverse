"""A graphics replay quotes the replayed round's frame metrics, never a later build's (B3)."""

from __future__ import annotations

import json

from codeverse3d.contracts.artifacts import BuildResult, GateReport
from codeverse3d.spatial.frame_stats import SequenceStats
from codeverse3d.tracks.graphics import frame_stats_text, frames_render_set
from codeverse3d.workspace import Workspace


def _metrics(ws: Workspace, lum: float) -> None:
    ws.artifacts.mkdir(parents=True, exist_ok=True)
    (ws.artifacts / "metrics.json").write_text(json.dumps({
        "stats": SequenceStats(mean_lum=lum).model_dump(mode="json"),
        "gate": GateReport(gate="gl_frames", passed=True).model_dump(mode="json")}))


def test_each_round_keeps_and_quotes_its_own_frame_metrics(tmp_path):
    ws = Workspace(tmp_path / "run").create()
    build = BuildResult(ok=True, language="glsl_shader")
    for i, lum in enumerate((0.25, 0.75)):  # two rounds: the canonical file ends as round 1's
        _metrics(ws, lum)
        frames_render_set(ws, build, i)
    assert "mean_lum=0.250" in frame_stats_text(ws, 0)
    assert "mean_lum=0.750" in frame_stats_text(ws, 1) and "mean_lum=0.750" in frame_stats_text(ws)


def test_a_round_recorded_before_the_copy_is_not_quoted_a_later_builds_metrics(tmp_path):
    ws = Workspace(tmp_path / "run").create()
    _metrics(ws, 0.75)  # the last build's (round 1)
    (ws.root / "rounds").mkdir()
    for i in (0, 1):
        (ws.root / "rounds" / f"r{i:02d}.json").write_text("{}")
    assert "later build" in frame_stats_text(ws, 0) and "mean_lum" not in frame_stats_text(ws, 0)
    assert "mean_lum=0.750" in frame_stats_text(ws, 1)


def test_an_old_graphics_run_quotes_the_canonical_metrics_for_the_round_its_finalise_rebuilt(tmp_path):
    """smoke_glsl-like: three rounds recorded before rounds kept a copy; the old finalise rebuilt
    the best (``best_round`` 1), so artifacts/metrics.json is round 1's — not round 2's (the
    highest), and not "a later build's" for round 1."""
    ws = Workspace(tmp_path / "run").create()
    _metrics(ws, 0.5)  # round 1's, rebuilt by the old finalise
    (ws.root / "rounds").mkdir()
    for i in (0, 1, 2):
        (ws.root / "rounds" / f"r{i:02d}.json").write_text("{}")
    ws.write_json(ws.record_path, {"best_round": 1})
    assert "mean_lum=0.500" in frame_stats_text(ws, 1)
    for i in (0, 2):
        assert "mean_lum" not in frame_stats_text(ws, i)
