"""A graphics replay quotes the replayed round's frame metrics, never a later build's (B3)."""

from __future__ import annotations

import json

from codeverse3d.contracts.artifacts import BuildResult, GateReport
from codeverse3d.spatial.frame_stats import SequenceStats
from codeverse3d.tracks.graphics import frame_stats_text, frames_render_set
from codeverse3d.workspace import Workspace


def _metrics(ws: Workspace, lum: float) -> None:
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
