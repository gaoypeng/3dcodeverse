"""Refine transitions (codeverse/flywheel/refine.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.flywheel.pairs import MIN_PREFERENCE_DELTA
from codeverse.flywheel.record import load_record
from codeverse.flywheel.refine import build_refine, outcome_of, transitions
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
    _corpus(tmp_path)
    rows, drops = _rows(tmp_path)
    assert len(rows) == 1 and not drops
    t = rows[0]
    assert t.round == 1 and t.round_kind == "refine" and t.battery == "batt"
    assert t.score_before == 0.30 and t.score_after == 0.80 and t.score_delta == 0.50 and t.outcome == "improved"
    assert t.instructions == ["thicken the legs"]
    assert [g["message"] for g in t.gate_errors] == ["part 'Leg' is floating"]   # ERRORs only
    assert t.gate_errors[0]["fix_hint"] == "move Leg down 12 mm"
    assert any("thicken the legs" in i["instruction"] for i in t.improvement_plan)
    assert t.changed_files == ["src/model.py", "src/parts/leg.py"]
    assert "size=2.0" in t.diff and t.diff_bytes == len(t.diff) and not t.diff_truncated
    assert t.before_commit and t.after_commit and t.before_commit != t.after_commit
    assert t.before_files is None and t.after_files is None       # --with-code is opt-in


def test_with_code_inlines_the_answer_and_the_tree_it_started_from(tmp_path):
    _corpus(tmp_path)
    rows, _ = _rows(tmp_path, with_code=True)
    t = rows[0]
    assert set(t.after_files) == {"src/model.py", "src/parts/leg.py"}    # what changed
    assert "size=2.0" in t.after_files["src/model.py"]
    assert "src/model.py" in t.before_files and "size=1.0" in t.before_files["src/model.py"]
    assert "src/parts/leg.py" not in t.before_files                      # it did not exist yet


@pytest.mark.parametrize("delta,label", [(0.5, "improved"), (0.05, "improved"), (0.04, "unchanged"),
                                         (0.0, "unchanged"), (-0.05, "regressed"), (None, "unscored")])
def test_the_outcome_rule_is_the_one_pairs_uses(delta, label):
    assert outcome_of(delta, MIN_PREFERENCE_DELTA) == label


def test_a_run_reached_through_a_symlink_is_exported_once_under_its_physical_battery(tmp_path):
    _corpus(tmp_path, battery="zz_physical")
    alias = tmp_path / "aa_alias" / "runs"
    alias.mkdir(parents=True)
    (alias / "chair").symlink_to(tmp_path / "zz_physical" / "runs" / "chair")
    rows, drops = _rows(tmp_path)
    assert len(rows) == 1 and drops == {"duplicate_run": 1}
    assert rows[0].battery == "zz_physical", "labelled by the battery it was reached through first"


@pytest.mark.parametrize("break_it,reason", [
    ("kind", None), ("commit", "no_commit"), ("build", "predecessor_build_failed"),
    # RoundRecord.build is optional: a round that never reached the build stage carries
    # None, and reading .ok on it took the whole export down with an AttributeError
    ("no_build", "predecessor_build_failed"),
    ("judgment", "predecessor_unjudged"),
])
def test_every_unexportable_round_is_counted_under_a_named_reason(tmp_path, break_it, reason):
    ws = _corpus(tmp_path)
    rec = load_record(ws)
    if break_it == "kind":
        rec.rounds[1].kind = "texture"
    elif break_it == "commit":
        rec.rounds[0].commit = ""
    elif break_it == "build":
        rec.rounds[0].build.ok = False
    elif break_it == "no_build":
        rec.rounds[0].build = None
    else:
        rec.rounds[0].judgment = None
    ws.write_json(ws.record_path, rec)
    rows, drops = _rows(tmp_path)
    assert rows == []
    assert drops == ({} if reason is None else {reason: 1}), f"{break_it}: {dict(drops)}"


def test_build_refine_writes_jsonl_atomically(tmp_path):
    _corpus(tmp_path)
    out = tmp_path / "nested" / "refine.jsonl"
    n, drops = build_refine(tmp_path, out)
    assert n == 1 and not drops and not list(out.parent.glob("*.part"))
    row = json.loads(out.read_text().splitlines()[0])
    assert row["outcome"] == "improved" and "before_files" not in row   # exclude_none keeps rows small
