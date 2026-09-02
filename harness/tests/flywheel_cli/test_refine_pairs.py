"""``bench/refine_pairs.py``: refine transitions exported as (brief -> diff -> delta) rows.

Every case builds a corpus in the real on-disk layout — ``<root>/<battery>/runs/<slug>``,
a git repo with two commits and two round records — because the exporter's whole job is
to survive that layout: the recorded round shas rather than ``HEAD`` (a finished run ends
on a "restore best round rNN" commit), one row per resolved run path (a battery's
symlinked cells make runs reachable twice), and a named reason for every transition it
cannot export.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from bench.refine_pairs import (
    DROP_REASONS,
    Report,
    export,
    iter_rows,
    main,
    outcome_label,
    parse_instruction,
    scan_roots,
    transition_row,
)
from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.contracts.run import RunRecord
from codeverse.flywheel.record import load_record, run_id_for
from codeverse.workspace import Workspace
from tests.flywheel_cli.conftest import make_fake_run


def _corpus(root: Path, *, battery: str = "batt", slug: str = "chair") -> Workspace:
    """One battery holding one run: two judged rounds, ``src/`` committed twice."""
    ws, _ = make_fake_run(root / battery / "runs", slug)
    return ws


def _patch(ws: Workspace, edit: Callable[[RunRecord], None]) -> RunRecord:
    rec = load_record(ws)
    edit(rec)
    ws.write_json(ws.record_path, rec)
    return rec


def _rows(root: Path, **kw) -> tuple[list[dict], Report]:
    report = Report()
    return list(iter_rows(root, report, **kw)), report


# --------------------------------------------------------------------------- pure functions
def test_parse_instruction_refine_task_line():
    tasks = parse_instruction("[gate/gate:connectivity] Leg_0: weld the leg to the seat (files: src/model.py, src/parts/leg.py)")
    assert tasks == [{"source": "gate", "kind": "gate:connectivity", "target": "Leg_0",
                      "instruction": "weld the leg to the seat",
                      "files": ["src/model.py", "src/parts/leg.py"]}]


def test_parse_instruction_grouped_bullets():
    """``compact_instructions`` folds ``Leg_0``/``Leg_1`` into one ``Leg:`` line; each
    bullet is its own task and may name the instance it came from."""
    tasks = parse_instruction(
        "Leg: (a) [gate/gate:contract] too thin; (b) [Leg_1] [judge/geometry] add a fillet (files: src/model.py)")
    assert [(t["source"], t["kind"], t["target"], t["instruction"]) for t in tasks] == [
        ("gate", "gate:contract", "Leg", "too thin"),
        ("judge", "geometry", "Leg_1", "add a fillet"),
    ]
    assert all(t["files"] == ["src/model.py"] for t in tasks)


def test_parse_instruction_bare_sentence_keeps_its_colon():
    """Detail rounds send prose.  A sentence's own colon is not a target: only a single
    bare token before ``: `` is, or the whole instruction would be cut in half."""
    prose = "Add the panel lines where the shells meet (lids, trays): 1-2 mm wide insets."
    assert parse_instruction(prose) == [
        {"source": "", "kind": "", "target": "", "instruction": prose, "files": []}]
    assert parse_instruction("window_frame: Slope the exterior sill.") == [
        {"source": "", "kind": "", "target": "window_frame", "instruction": "Slope the exterior sill.", "files": []}]


def test_outcome_label():
    assert outcome_label(0.2) == "improved"
    assert outcome_label(-0.2) == "regressed"
    assert outcome_label(0.01) == "unchanged"
    assert outcome_label(0.2, threshold=0.5) == "unchanged"
    assert outcome_label(None) == "unscored"


def test_scan_roots_picks_batteries_not_the_parent(tmp_path):
    root = tmp_path / "out"
    _corpus(root, battery="batt_a")
    _corpus(root, battery="batt_b")
    assert scan_roots(root) == [root / "batt_a", root / "batt_b"]
    assert scan_roots(root / "batt_a") == [root / "batt_a"]


# --------------------------------------------------------------------------- rows
def test_row_carries_brief_diff_and_delta(tmp_path):
    root = tmp_path / "out"
    ws = _corpus(root)
    rows, report = _rows(root)
    (row,) = rows
    assert (row["battery"], row["run"], row["round"], row["round_kind"]) == ("batt", "chair", 1, "refine")
    assert row["prompt_id"] == "chair" and row["prompt"] == "a wooden dining chair"
    assert (row["track"], row["language"]) == ("static_object", "blender")
    # the brief is the PREVIOUS round's verdict, the change is this round's
    assert [i["target"] for i in row["issues"]] == ["Leg"]
    assert [i["instruction"] for i in row["improvement_plan"]] == ["thicken the legs", "add a back rail"]
    assert row["instructions"] == ["thicken the legs"]
    assert row["before"]["files"] == ["src/model.py"]
    assert row["after"]["files"] == ["src/model.py", "src/parts/leg.py"]
    assert row["changed_files"] == ["src/model.py", "src/parts/leg.py"]
    assert "--- a/src/model.py" in row["diff"] and "+++ b/src/parts/leg.py" in row["diff"]
    assert row["diff_bytes"] == len(row["diff"].encode()) and not row["diff_truncated"]
    assert row["diff_added"] > 0 and row["diff_removed"] > 0
    assert (row["score_before"], row["score_after"], row["score_delta"]) == (0.55, 0.8, 0.25)
    assert row["before"]["build_ok"] is True and row["after"]["build_ok"] is True
    assert row["outcome"] == "improved"
    assert report.rows == 1 and not report.drops
    assert report.kinds == {"baseline": 1, "refine": 1}
    assert ws.root.name == "chair"


def test_outcome_threshold_is_recorded_on_the_row(tmp_path):
    root = tmp_path / "out"
    _corpus(root)
    (row,), _ = _rows(root, threshold=0.5)
    assert row["outcome"] == "unchanged" and row["outcome_threshold"] == 0.5


def test_gate_error_findings_carry_fix_hints(tmp_path):
    """The refine brief is half judge, half gate: ``build_refine_instructions`` copies a
    finding's ``fix_hint`` verbatim, and only ERROR findings become tasks."""
    root = tmp_path / "out"
    ws = _corpus(root)
    _patch(ws, lambda rec: rec.rounds[0].gates.append(GateReport(gate="connectivity", passed=False, findings=[
        GateFinding(gate="connectivity", severity=Severity.ERROR, target="Leg_0",
                    message="Leg_0 is not connected", fix_hint="move Leg_0 up by 3 mm"),
        GateFinding(gate="connectivity", severity=Severity.WARN, target="Seat", message="1.2 mm overlap")])))
    (row,), _ = _rows(root)
    assert row["gate_findings"] == [{"gate": "connectivity", "target": "Leg_0",
                                     "message": "Leg_0 is not connected",
                                     "fix_hint": "move Leg_0 up by 3 mm", "data": {}}]
    assert row["before"]["gate_errors"] == 1


def test_diff_is_capped_and_says_so(tmp_path):
    root = tmp_path / "out"
    _corpus(root)
    (row,), _ = _rows(root, max_diff_bytes=120)
    assert row["diff_truncated"] and row["diff_max_bytes"] == 120
    assert row["diff_bytes"] > 120 and "[truncated" in row["diff"]


def test_diff_uses_the_recorded_shas_not_head(tmp_path):
    """152 of the 239 recorded runs end on a "restore best round rNN" commit and only 54
    have HEAD at their last round, so the recorded sha is the only usable handle."""
    root = tmp_path / "out"
    ws = _corpus(root)
    rec = load_record(ws)
    (ws.src / "model.py").write_text("# restored baseline\n")
    ws.commit("restore best round r00")
    (row,), _ = _rows(root)
    assert row["after"]["commit"] == rec.rounds[1].commit
    assert "restored baseline" not in row["diff"]


def test_nested_battery_layout_keeps_battery_cell_and_arm(tmp_path):
    root = tmp_path / "out"
    make_fake_run(root / "cmp" / "cells" / "chair_cell", "harness_arm")
    (row,), report = _rows(root)
    assert (row["battery"], row["cell"], row["arm"]) == ("cmp", "chair_cell", "harness_arm")
    assert row["run"] == "chair_cell__harness_arm"
    assert report.roots == [str(root / "cmp")]


def test_transition_row_is_callable_on_one_run(tmp_path):
    """The row builder is usable without the scanner (one run, two rounds in hand)."""
    ws, rec = make_fake_run(tmp_path / "runs")
    run_id = run_id_for(tmp_path / "runs", ws.root)
    row = transition_row(ws, rec, run_id, rec.rounds[0], rec.rounds[1])
    assert row["run"] == ws.root.name and row["score_delta"] == 0.25


# --------------------------------------------------------------------------- drops
def _drop_empty_battery(root: Path) -> None:
    (root / "batt" / "cells").mkdir(parents=True)


def _drop_unreadable_record(root: Path) -> None:
    _corpus(root)
    broken = root / "batt" / "runs" / "broken"
    broken.mkdir()
    (broken / "record.json").write_text("{not json")


def _drop_duplicate_run(root: Path) -> None:
    ws = _corpus(root)
    (root / "batt" / "runs" / "chair_link").symlink_to(ws.root, target_is_directory=True)


def _drop_missing_predecessor(root: Path) -> None:
    _patch(_corpus(root), lambda rec: setattr(rec.rounds[1], "index", 5))


def _drop_missing_commit(root: Path) -> None:
    _patch(_corpus(root), lambda rec: setattr(rec.rounds[1], "commit", ""))


def _drop_missing_prev_judgment(root: Path) -> None:
    _patch(_corpus(root), lambda rec: setattr(rec.rounds[0], "judgment", None))


def _drop_no_instructions(root: Path) -> None:
    _patch(_corpus(root), lambda rec: setattr(rec.rounds[1], "instructions", []))


def _drop_git_read_failed(root: Path) -> None:
    _patch(_corpus(root), lambda rec: setattr(rec.rounds[1], "commit", "0" * 40))


def _drop_empty_diff(root: Path) -> None:
    _patch(_corpus(root), lambda rec: setattr(rec.rounds[1], "commit", rec.rounds[0].commit))


#: one corpus per reason; the parametrisation below fails loudly if a reason has none
DROP_CORPUS: dict[str, Callable[[Path], None]] = {
    "empty_battery": _drop_empty_battery,
    "unreadable_record": _drop_unreadable_record,
    "duplicate_run": _drop_duplicate_run,
    "missing_predecessor": _drop_missing_predecessor,
    "missing_commit": _drop_missing_commit,
    "missing_prev_judgment": _drop_missing_prev_judgment,
    "no_instructions": _drop_no_instructions,
    "git_read_failed": _drop_git_read_failed,
    "empty_diff": _drop_empty_diff,
}
#: the two run-level drops leave the run itself exportable
KEEPS_ROW = {"unreadable_record", "duplicate_run"}


@pytest.mark.parametrize("reason", DROP_REASONS)
def test_every_drop_reason_is_counted(tmp_path, reason):
    root = tmp_path / "out"
    DROP_CORPUS[reason](root)
    rows, report = _rows(root)
    assert dict(report.drops) == {reason: 1}
    assert len(rows) == report.rows == (1 if reason in KEEPS_ROW else 0)


def test_drop_corpus_covers_every_declared_reason():
    assert set(DROP_CORPUS) == set(DROP_REASONS)


# --------------------------------------------------------------------------- cli
def test_main_writes_jsonl_and_prints_the_summary(tmp_path, capsys):
    root = tmp_path / "out"
    _corpus(root, battery="batt_a", slug="chair")
    _corpus(root, battery="batt_b", slug="stool")
    out = tmp_path / "pairs" / "refine.jsonl"

    assert main([str(root), "--out", str(out), "--summary"]) == 0
    printed = capsys.readouterr().out
    rows = [json.loads(line) for line in out.read_text().splitlines()]

    assert len(rows) == 2 and {r["battery"] for r in rows} == {"batt_a", "batt_b"}
    assert "2 row(s) from 2 run(s) in 2 root(s)" in printed
    assert "dropped: none" in printed
    assert "rows per battery" in printed and "batt_a" in printed
    assert "rows per track" in printed and "static_object" in printed
    assert "rows per outcome" in printed and "improved" in printed
    assert "diff size: median" in printed


def test_main_reports_drops_without_writing(tmp_path, capsys):
    root = tmp_path / "out"
    _drop_no_instructions(root)
    assert main([str(root), "--summary"]) == 1
    printed = capsys.readouterr().out
    assert "0 row(s) from 1 run(s)" in printed
    assert "no_instructions" in printed
    assert "no rows to summarise" in printed


def test_main_needs_somewhere_to_put_the_result(tmp_path):
    root = tmp_path / "out"
    _corpus(root)
    with pytest.raises(SystemExit):
        main([str(root)])
    with pytest.raises(SystemExit):
        main([str(tmp_path / "nope"), "--summary"])


def test_export_replaces_the_target_atomically(tmp_path):
    root = tmp_path / "out"
    _corpus(root)
    out = tmp_path / "refine.jsonl"
    out.write_text("stale\n")
    report = export(root, out)
    assert report.rows == 1 and json.loads(out.read_text())["run"] == "chair"
    assert not list(tmp_path.glob("*.tmp"))
