"""``bench/refine_pairs.py``: refine transitions exported as (brief -> diff -> delta) rows.

Every case builds a corpus in the real on-disk layout — ``<root>/<battery>/runs/<slug>``,
a git repo with two commits and two round records — because the exporter's whole job is
to survive that layout: the recorded round shas rather than ``HEAD`` (a finished run ends
on a "restore best round rNN" commit), one row per resolved run path labelled with the
battery it physically lives in (``bench/out``'s cell symlinks point ACROSS batteries), the
brief the agent sessions were really handed rather than the pre-grouping task list, and a
named reason for every run and transition it cannot export.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from bench.refine_pairs import (
    DROP_REASONS,
    Report,
    changes_block,
    export,
    iter_rows,
    main,
    outcome_label,
    parse_instruction,
    scan_roots,
    transition_row,
)
from codeverse.contracts.artifacts import BuildResult, GateFinding, GateReport, Judgment, Severity
from codeverse.contracts.run import RoundRecord, RunRecord
from codeverse.flywheel.record import load_record, run_id_for
from codeverse.judges.rubrics import is_degraded
from codeverse.workspace import Workspace
from tests.flywheel_cli.conftest import make_fake_run


def _corpus(root: Path, *, battery: str = "batt", slug: str = "chair") -> Workspace:
    """One battery holding one run: two judged rounds, ``src/`` committed twice."""
    ws, _ = make_fake_run(root / battery / "runs", slug)
    return ws


def _degraded() -> Judgment:
    """What ``judges.rubrics.degraded_judgment`` writes when the judge call fails: a
    0.0 verdict flagged both ways.  ``is_degraded`` below keeps this honest."""
    return Judgment(rubric="static_object_v1", judge_backend="gemini:gemini-3.1-pro-preview",
                    scores={"fidelity": 0.0}, overall=0.0, passed=False,
                    summary="judge_error: 503 from the judge", n_samples=0,
                    raw=json.dumps({"status": "degraded", "error": "503"}))


def _prompt(ws: Workspace, session: str, round_index: int, lines: list[str]) -> None:
    """One session's prompt file, in the shape ``agents.cli_common.begin_session`` writes
    and ``prompts/tracks/refine_object.j2`` renders."""
    d = ws.root / "trajectories" / f"{session}_r{round_index:02d}"
    d.mkdir(parents=True, exist_ok=True)
    numbered = "\n".join(f"{i}. {ln}" for i, ln in enumerate(lines, 1))
    (d / "prompt.md").write_text(
        f"<!-- system -->\nYou are a Blender author\n\n<!-- prompt -->\n"
        f"# Refine `Chair` — round {round_index}\n\nThe object already builds.\n\n"
        f"## Changes to make (in priority order; `[gate/...]` items are hard failures — fix them first)\n\n"
        f"{numbered}\n\nTargets: Leg\n\n## What the judge saw\n1. not an instruction\n")


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
    (root / "merged_report").mkdir()  # neither a run nor a battery: reported, not silent
    assert scan_roots(root) == ([root / "batt_a", root / "batt_b"], [root / "merged_report"])
    assert scan_roots(root / "batt_a") == ([root / "batt_a"], [])


def test_changes_block_reads_the_numbered_list_and_stops_at_the_next_section():
    """The refine templates number ``compact_instructions``' lines and follow them with
    ``Targets:``; a numbered line in a LATER section is not an instruction."""
    prompt = ("# Refine `Chair` — round 1\n\n"
              "## Changes to make (in priority order)\n\n"
              "1. Leg: (a) [judge/geometry] thicken\n\n2. [judge/material] Seat: oak,\n   quarter-sawn\n\n"
              "Targets: Leg, Seat\n\n## What the judge saw\n1. legs too thin\n")
    assert changes_block(prompt) == ["Leg: (a) [judge/geometry] thicken",
                                     "[judge/material] Seat: oak, quarter-sawn"]
    assert changes_block("# Rebuild `Chair`\n\n1. fix the syntax error\n") == []


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
    assert row["refine_tasks"] == ["thicken the legs"]
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
    """The cap must actually cut the text: a row that carries the WHOLE diff and merely
    says "truncated" is the failure the cap exists to prevent."""
    root = tmp_path / "out"
    _corpus(root)
    (row,), _ = _rows(root, max_diff_bytes=120)
    assert row["diff_truncated"] and row["diff_max_bytes"] == 120
    assert row["diff_bytes"] > 120 and "[truncated" in row["diff"]
    head, marker, _rest = row["diff"].partition("\n... [truncated ")
    assert marker and len(head.encode()) == 120, "the kept head is exactly the cap"
    assert f"{row['diff_bytes'] - 120} bytes" in row["diff"], "and it names what it dropped"
    (whole,), _ = _rows(root)  # the same diff uncapped: same prefix, more of it
    assert whole["diff"].startswith(head) and len(whole["diff"]) > len(head)
    assert not whole["diff_truncated"] and whole["diff_bytes"] == row["diff_bytes"]


def test_diff_lines_count_content_not_file_headers(tmp_path):
    """``+++``/``---`` are one pair per changed FILE.  Counting them inflates every row's
    churn by the number of files the round touched — the label a preference model reads."""
    root = tmp_path / "out"
    _corpus(root)
    (row,), _ = _rows(root)
    # round 1 rewrites 3 of the 4 lines of src/model.py and adds the 1-line src/parts/leg.py
    assert (row["diff_added"], row["diff_removed"]) == (4, 3)
    naive_added = sum(1 for ln in row["diff"].splitlines() if ln.startswith("+"))
    naive_removed = sum(1 for ln in row["diff"].splitlines() if ln.startswith("-"))
    assert (naive_added, naive_removed) == (6, 5), "two file header pairs sit in that diff"


def test_changed_files_are_the_ones_this_round_touched(tmp_path):
    """Per-file attribution is the round's OWN diff, not the file list at its commit: a
    round that edits one of two files must not be recorded as having rewritten both."""
    root = tmp_path / "out"
    ws = _corpus(root)
    (ws.src / "parts" / "leg.py").write_text("LEG = 0.06\n")
    commit = ws.commit("round 2")
    _patch(ws, lambda rec: rec.rounds.append(RoundRecord(
        index=2, kind="refine", commit=commit, instructions=["thicken the legs again"],
        build=BuildResult(ok=True, language="blender"), judgment=rec.rounds[1].judgment)))
    rows, _ = _rows(root)
    second = rows[-1]
    assert second["round"] == 2
    assert second["changed_files"] == ["src/parts/leg.py"]
    assert second["after"]["files"] == ["src/model.py", "src/parts/leg.py"], "both exist, one changed"
    assert "src/model.py" not in second["diff"]


def test_only_refine_kind_rounds_are_transitions(tmp_path):
    """A texture pass edits ``src/`` and has everything a row needs — a judged
    predecessor, a commit, instructions.  It is still not a refine transition: nothing
    derived it from a verdict, so it is a round SEEN and a row not written, with no drop
    to explain (the drop reasons are for transitions that should have been exportable)."""
    root = tmp_path / "out"
    ws = _corpus(root)
    (ws.src / "model.py").write_text("# textured\nimport bpy\n")
    commit = ws.commit("texture pass")
    _patch(ws, lambda rec: rec.rounds.append(RoundRecord(
        index=2, kind="texture", commit=commit, instructions=["bake a quarter-sawn oak albedo"],
        build=BuildResult(ok=True, language="blender"))))
    rows, report = _rows(root)
    assert [r["round_kind"] for r in rows] == ["refine"]
    assert report.kinds == {"baseline": 1, "refine": 1, "texture": 1}
    assert report.transitions == 1 and not report.drops


def test_a_degraded_verdict_is_unscored_not_a_zero(tmp_path):
    """A judge outage writes a 0.0 ``judge_error:`` Judgment.  Read as a score it turns a
    fine round into the corpus's worst regression (0.0 - 0.55), and the sign of that row
    is exactly what the pairs are for."""
    assert is_degraded(_degraded()), "the fixture must stay the real degraded shape"
    root = tmp_path / "out"
    ws = _corpus(root)
    _patch(ws, lambda rec: setattr(rec.rounds[1], "judgment", _degraded()))
    (row,), report = _rows(root)
    assert row["score_before"] == 0.55
    assert row["score_after"] is None and row["score_delta"] is None
    assert row["outcome"] == "unscored"
    assert row["after"]["score"] is None and row["after"]["passed"] is None
    assert not report.drops


def test_a_degraded_previous_verdict_drops_the_transition(tmp_path):
    """The brief is the PREVIOUS round's verdict.  A judge outage wrote no verdict, so
    there is no brief — and a 0.0 one would make every issue list empty on purpose."""
    root = tmp_path / "out"
    ws = _corpus(root)
    _patch(ws, lambda rec: setattr(rec.rounds[0], "judgment", _degraded()))
    rows, report = _rows(root)
    assert rows == [] and dict(report.drops) == {"missing_prev_judgment": 1}


# --------------------------------------------------------------------------- instructions
def test_instruction_lines_are_the_ones_the_sessions_were_handed(tmp_path):
    """``RoundRecord.instructions`` is the COMPILED task list; each file-disjoint group's
    agent session is handed its own ``compact_instructions`` output, folded by target and
    capped.  The two differ for 167 of the corpus's 205 transitions, so the row carries
    both and says which one ``instruction_tasks`` was parsed from."""
    root = tmp_path / "out"
    ws = _corpus(root)
    compiled = ["[judge/geometry] Leg_0: thicken it (files: src/parts/leg.py)",
                "[judge/geometry] Leg_1: thicken it (files: src/parts/leg.py)",
                "[judge/material] Seat: quarter-sawn oak (files: src/model.py)"]
    _patch(ws, lambda rec: setattr(rec.rounds[1], "instructions", compiled))
    leg = "Leg: (a) [judge/geometry] thicken it; (b) [Leg_1] [judge/geometry] thicken it (files: src/parts/leg.py)"
    seat = "[judge/material] Seat: quarter-sawn oak (files: src/model.py)"
    _prompt(ws, "refine_leg", 1, [leg])
    _prompt(ws, "refine_seat", 1, [seat])

    (row,), _ = _rows(root)
    assert row["instruction_source"] == "prompt"
    assert [s["session"] for s in row["sent_instructions"]] == ["refine_leg", "refine_seat"]
    assert row["instruction_lines"] == [leg, seat]
    assert row["refine_tasks"] == compiled
    assert row["instruction_lines"] != row["refine_tasks"]
    assert [t["target"] for t in row["instruction_tasks"]] == ["Leg", "Leg_1", "Seat"]


def test_a_retry_session_is_not_a_second_brief(tmp_path):
    """``<label>.a2`` is the SAME brief re-sent after a silent bail (``_session_label``);
    counting it twice would double every task in the row."""
    root = tmp_path / "out"
    ws = _corpus(root)
    line = "Leg: (a) [judge/geometry] thicken it (files: src/parts/leg.py)"
    _prompt(ws, "refine", 1, [line])
    _prompt(ws, "refine.a2", 1, [line])
    (row,), _ = _rows(root)
    assert row["instruction_lines"] == [line]
    assert [s["session"] for s in row["sent_instructions"]] == ["refine"]


def test_instruction_lines_fall_back_to_the_record_when_no_prompt_survives(tmp_path):
    """10 of the corpus's 205 transitions have no prompt file left.  The compiled task
    list is then all there is, and the row says so rather than shipping an empty brief."""
    root = tmp_path / "out"
    _corpus(root)
    (row,), _ = _rows(root)
    assert row["sent_instructions"] == [] and row["instruction_source"] == "record"
    assert row["instruction_lines"] == row["refine_tasks"] == ["thicken the legs"]


# --------------------------------------------------------------------------- identity
def test_a_cross_battery_alias_is_labelled_with_the_physical_battery(tmp_path):
    """``bench/out``'s aliases point ACROSS batteries (``compare_v4_calm/cells/*`` is a
    symlink into ``compare_v4`` and ``compare_v4_harness_calm``) and the alias sorts
    FIRST, so labelling by the root that reached a run first credits it to a battery it
    never ran in.  The label is the battery the run physically lives in; the alias is
    reported, not lost."""
    root = tmp_path / "out"
    ws = _corpus(root, battery="zz_physical", slug="chair")
    alias = root / "aa_alias" / "runs"
    alias.mkdir(parents=True)
    (alias / "chair").symlink_to(ws.root, target_is_directory=True)

    rows, report = _rows(root)
    assert report.roots == [str(root / "aa_alias"), str(root / "zz_physical")]
    assert [r["battery"] for r in rows] == ["zz_physical"]
    assert rows[0]["run_dir"] == str(ws.root)
    assert dict(report.drops) == {"duplicate_run": 1}
    assert dict(report.aliases) == {"aa_alias -> zz_physical": 1}


def test_a_single_run_directory_exports_with_the_identity_a_full_scan_gives_it(tmp_path):
    """The documented one-run input.  ``iter_runs`` searches strictly BELOW its argument,
    so the run itself was invisible and the export reported zero rows as a success."""
    root = tmp_path / "out"
    ws = _corpus(root, battery="batt", slug="chair")
    whole, _ = _rows(root)
    alone, report = _rows(ws.root)
    assert len(alone) == 1 and report.runs == 1 and not report.drops
    assert (alone[0]["battery"], alone[0]["run"]) == ("batt", "chair")
    assert alone[0] == whole[0]


def test_a_single_nested_run_keeps_the_whole_battery_path(tmp_path):
    """The same for the deepest layout, ``<battery>/arms/<arm>/cells/<cell>/<slug>``,
    where the battery is the OUTERMOST marker on the path: stopping at the first one up
    (``<arm>``, which owns a ``cells/``) labels the run with its arm instead."""
    root = tmp_path / "out"
    ws, _ = make_fake_run(root / "batt" / "arms" / "control" / "cells" / "chair_cell", "harness_arm")
    whole, _ = _rows(root)
    (row,), report = _rows(ws.root)
    assert (row["battery"], row["arm"], row["cell"]) == ("batt", "control", "chair_cell")
    assert row["run"] == "control__chair_cell__harness_arm"
    assert report.roots == [str(ws.root)]
    assert row == whole[0]


def test_main_accepts_one_run_directory(tmp_path, capsys):
    root = tmp_path / "out"
    ws = _corpus(root)
    assert main([str(ws.root), "--summary"]) == 0
    assert "1 row(s) from 1 run(s) in 1 root(s)" in capsys.readouterr().out


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
def _drop_not_a_battery(root: Path) -> None:
    _corpus(root)
    (root / "merged_report").mkdir()  # bench/out holds three of these


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
    "not_a_battery": _drop_not_a_battery,
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
#: the scan-level drops leave the run itself exportable
KEEPS_ROW = {"not_a_battery", "unreadable_record", "duplicate_run"}


@pytest.mark.parametrize("reason", DROP_REASONS)
def test_every_drop_reason_is_counted(tmp_path, reason):
    root = tmp_path / "out"
    DROP_CORPUS[reason](root)
    rows, report = _rows(root)
    assert dict(report.drops) == {reason: 1}
    assert len(rows) == report.rows == (1 if reason in KEEPS_ROW else 0)


def test_a_skipped_child_is_named_in_the_report(tmp_path, capsys):
    """Counting it is half the job: "every drop is counted" has to say WHICH directory
    was passed over, or a mistyped battery name reads as an empty corpus."""
    from bench.refine_pairs import scan_report

    root = tmp_path / "out"
    _drop_not_a_battery(root)
    _, report = _rows(root)
    assert report.skipped == [str(root / "merged_report")]
    assert str(root / "merged_report") in scan_report(report)
    assert "not_a_battery" in scan_report(report)


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
