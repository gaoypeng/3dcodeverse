"""codeverse/agents/transcript.py — the per-session transcript budget."""

from __future__ import annotations

from pathlib import Path

from codeverse.agents import transcript as tr
from codeverse.agents.transcript import Trajectory


def test_the_transcript_stops_at_its_budget_with_a_marker_row(tmp_path: Path, monkeypatch):
    """Rows were byte-capped one by one but the FILE was not: a child emitting 100k
    lines wrote a 27 MB transcript.jsonl (~406 MB at the 4000-char row cap)."""
    monkeypatch.setattr(tr, "LINE_BUDGET_ROWS", 5)
    t = Trajectory(tmp_path / "traj")
    for i in range(20):
        t.append("stdout", line=f"line {i}")
    rows = t.read_transcript()
    assert [r["kind"] for r in rows] == ["stdout"] * 5 + ["line_budget_exhausted"]
    assert rows[-1]["rows"] == 5

    monkeypatch.setattr(tr, "LINE_BUDGET_BYTES", 400)
    t2 = Trajectory(tmp_path / "traj2")
    for _ in range(50):
        t2.append("stdout", line="x" * 100)
    rows2 = t2.read_transcript()
    assert len(rows2) < 6 and rows2[-1]["kind"] == "line_budget_exhausted"
