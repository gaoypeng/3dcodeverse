"""Refine rounds as SFT samples (bench/refine_sft.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.refine_sft import DEFAULT_OUTCOMES, build, envelope, main, sample, user_turn
from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.flywheel.record import load_record
from codeverse.tracks.generation import parse_multifile
from tests.flywheel_cli.conftest import make_fake_run


def _corpus(root: Path, *, scores: tuple[float, float] = (0.30, 0.80)) -> Path:
    ws, _ = make_fake_run(root / "batt" / "runs", "chair", scores=scores)
    rec = load_record(ws)
    rec.rounds[0].gates = [GateReport(gate="connectivity", passed=False, findings=[GateFinding(
        gate="connectivity", severity=Severity.ERROR, target="Leg",
        message="part 'Leg' is floating", fix_hint="move Leg down 12 mm so it meets Seat")])]
    ws.write_json(ws.record_path, rec)
    return root


def test_an_improved_round_becomes_a_three_message_sample(tmp_path):
    rows, report, drops = build(_corpus(tmp_path))
    assert len(rows) == 1 and report.runs == 1 and not drops
    s = rows[0]
    assert [m["role"] for m in s["messages"]] == ["system", "user", "assistant"]
    assert s["score_before"] == 0.30 and s["score_after"] == 0.80
    assert s["changed_files"] == ["src/model.py", "src/parts/leg.py"]
    assert s["id"].endswith("/r01") and s["track"] == "static_object" and s["language"] == "blender"
    # the answer is the file the round produced, and it parses back through the envelope
    files = parse_multifile(s["messages"][2]["content"])
    assert set(files) == {"src/model.py", "src/parts/leg.py"} and "size=2.0" in files["src/model.py"]
    assert "bpy" in s["messages"][0]["content"].lower()


def test_the_brief_carries_what_the_round_was_told_and_the_code_it_started_from(tmp_path):
    rows, _, _ = build(_corpus(tmp_path))
    user = rows[0]["messages"][1]["content"]
    assert "a wooden dining chair" in user
    assert "part 'Leg' is floating" in user and "move Leg down 12 mm" in user   # gate finding + fix hint
    assert "thicken the legs" in user                                            # the judge's plan / instruction
    assert "=== FILE: src/model.py ===" in user and "size=1.0" in user           # the BEFORE state
    assert "size=2.0" not in user                                                # never the answer
    assert "in full" in user.rsplit("\n", 1)[-1]


def test_only_improved_rounds_are_exported_and_the_rest_are_counted(tmp_path):
    rows, _, drops = build(_corpus(tmp_path, scores=(0.80, 0.80)))   # unchanged
    assert rows == [] and drops == {"outcome:unchanged": 1}
    rows, _, drops = build(_corpus(tmp_path / "b", scores=(0.80, 0.30)))
    assert rows == [] and drops == {"outcome:regressed": 1}
    rows, _, _ = build(_corpus(tmp_path / "c", scores=(0.80, 0.30)), outcomes=("regressed",))
    assert len(rows) == 1 and DEFAULT_OUTCOMES == ("improved",)


def test_an_answer_over_the_cap_is_dropped_not_truncated(tmp_path):
    rows, _, drops = build(_corpus(tmp_path), max_answer_chars=50)
    assert rows == [] and drops == {"answer_too_long": 1}


def test_a_row_whose_commit_is_gone_is_counted_not_raised(tmp_path):
    from bench.refine_pairs import Report, iter_rows

    row = next(iter(iter_rows(_corpus(tmp_path), Report())))
    row["after"]["commit"] = "0" * 40
    assert sample(row) is None and row["_drop"].startswith("git_read_failed")


def test_envelope_and_user_turn_are_pure(tmp_path):
    assert envelope({"b.py": "B", "a.py": "A"}) == (
        "=== FILE: a.py ===\nA\n=== END FILE ===\n=== FILE: b.py ===\nB\n=== END FILE ===")
    assert parse_multifile(envelope({"a.py": "A"})) == {"a.py": "A"}
    row = {"track": "static_object", "language": "blender", "prompt": "a stool",
           "gate_findings": [], "issues": [], "instruction_lines": []}
    text = user_turn(row, {"src/x.py": "code"})
    assert "## Gate errors" not in text and "## What the judge saw" not in text
    assert "## Current files" in text and "a stool" in text


def test_main_writes_jsonl_and_reports(tmp_path, capsys):
    out = tmp_path / "sft.jsonl"
    assert main([str(_corpus(tmp_path)), "--out", str(out), "--summary"]) == 0
    printed = capsys.readouterr().out
    assert "1 sample(s) from 1 run(s)" in printed and "answer chars: median" in printed
    assert "by language: blender 1" in printed
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["schema_version"] == 1


@pytest.mark.parametrize("lang,entry", [("urdf_blender", "src/model.py")])
def test_an_articulated_run_keeps_only_its_changed_src_files(tmp_path, lang, entry):
    from codeverse.contracts.common import Language

    ws, _ = make_fake_run(tmp_path / "batt" / "runs", "cab", language=Language(lang))
    rows, _, _ = build(tmp_path)
    assert len(rows) == 1
    assert rows[0]["changed_files"] == [entry, "src/parts/leg.py"] and rows[0]["language"] == lang
    assert set(parse_multifile(rows[0]["messages"][2]["content"])) == {entry, "src/parts/leg.py"}
    assert "robot.urdf" in rows[0]["messages"][1]["content"]  # the brief still shows the whole src/
