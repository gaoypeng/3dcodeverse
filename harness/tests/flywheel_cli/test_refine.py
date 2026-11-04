"""Refine transitions (codeverse3d/addons/dataset/refine.py)."""

from __future__ import annotations

import json
from pathlib import Path

from codeverse3d.addons.dataset.refine import build_refine, transitions
from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse3d.record.record import load_record
from tests.flywheel_cli.conftest import make_fake_run


def _corpus(root: Path, *, scores=(0.30, 0.80), battery: str = "batt", slug: str = "chair"):
    ws, _ = make_fake_run(root / battery / "runs", slug, scores=scores)
    rec = load_record(ws)
    rec.rounds[0].gates = [GateReport(gate="connectivity", passed=False, findings=[GateFinding(
        gate="connectivity", severity=Severity.ERROR, target="Leg", message="part 'Leg' is floating",
        fix_hint="move Leg down 12 mm"), GateFinding(
        gate="connectivity", severity=Severity.WARN, target="Seat", message="thin", fix_hint="")])]
    ws.write_json(ws.record_path, rec)
    return ws


def _rows(root: Path, **kw):
    from collections import Counter

    drops: Counter[str] = Counter()
    return list(transitions(root, drops=drops, **kw)), drops


def test_a_refine_round_carries_its_brief_its_diff_and_its_delta(tmp_path):
    """One refine transition; --with-code inlines the answer and the tree it started from; the jsonl is
    written atomically."""
    _corpus(tmp_path)
    rows, drops = _rows(tmp_path)
    assert len(rows) == 1 and not drops
    t = rows[0]
    assert t.round == 1 and t.round_kind == "refine"
    assert (t.battery, t.run) == (tmp_path.name, "batt__chair")   # its RunId: the key index.sqlite joins on (N59)
    assert t.score_before == 0.30 and t.score_after == 0.80 and t.score_delta == 0.50 and t.outcome == "improved"
    assert t.instructions == ["thicken the legs"]
    assert [g["message"] for g in t.gate_errors] == ["part 'Leg' is floating"]   # ERRORs only
    assert t.gate_errors[0]["fix_hint"] == "move Leg down 12 mm"
    assert any("thicken the legs" in i["instruction"] for i in t.improvement_plan)
    assert t.changed_files == ["src/model.py", "src/parts/leg.py"]
    assert "size=2.0" in t.diff and t.diff_bytes == len(t.diff) and not t.diff_truncated
    assert t.before_commit and t.after_commit and t.before_commit != t.after_commit
    assert t.before_files is None and t.after_files is None       # --with-code is opt-in

    rows, _ = _rows(tmp_path, with_code=True)
    t = rows[0]
    assert set(t.after_files) == {"src/model.py", "src/parts/leg.py"}    # what changed
    assert "size=2.0" in t.after_files["src/model.py"]
    assert "src/model.py" in t.before_files and "size=1.0" in t.before_files["src/model.py"]
    assert "src/parts/leg.py" not in t.before_files                      # it did not exist yet

    out = tmp_path / "nested" / "refine.jsonl"
    n, drops = build_refine(tmp_path, out)
    assert n == 1 and not drops and not list(out.parent.glob("*.part"))
    row = json.loads(out.read_text().splitlines()[0])
    assert row["outcome"] == "improved" and "before_files" not in row   # exclude_none keeps rows small
