"""``bench/rejudge_offline.replay_round`` reproduces a stored verdict from its samples, version and vetoes included."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import bench.rejudge_offline as ro
from codeverse3d.contracts.artifacts import (
    GateFinding,
    GateReport,
    Judgment,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse3d.contracts.plan import AcceptanceItem
from codeverse3d.contracts.run import RoundRecord
from codeverse3d.judges.rubrics import (
    SCORING_VERSION,
    AcceptanceVerdict,
    CriterionScore,
    JudgeOutput,
    aggregate_samples,
    load_rubric,
)

R = load_rubric("static_object_v1")
ITEMS = [AcceptanceItem(id="M1", text="four legs", priority="must"),
         AcceptanceItem(id="M2", text="0.45 m tall", priority="must", how="measure"),
         AcceptanceItem(id="S1", text="bevelled edges", priority="should")]
# a failing contract gate next to a passed connectivity gate: the veto must read connectivity alone
GATES = [
    GateReport(gate="contract", passed=False, findings=[GateFinding(
        gate="contract", severity=Severity.ERROR, target="bbox", message="height 0.31 m vs plan 0.45 m")]),
    GateReport(gate="connectivity", passed=True, findings=[
        GateFinding(gate="connectivity", severity=Severity.INFO, target="", message="all 5 parts are connected (5 contacts)"),
        GateFinding(gate="connectivity", severity=Severity.WARN, target="", message="'Seat' and 'Leg_0' overlap by 1.2 mm (weld)")]),
]


def _sample(score: float, *defects: str) -> JudgeOutput:
    return JudgeOutput(
        criteria={c.id: CriterionScore(score=score, evidence="v") for c in R.criteria}, summary="a chair",
        acceptance={"M1": AcceptanceVerdict(verified=True), "M2": AcceptanceVerdict(verified=False),
                    "S1": AcceptanceVerdict(verified=True)},
        defects={d.id: d.id in defects for d in R.defects},
    )


def _write_round(root: Path, judgment: Judgment | None) -> Path:
    run = root / "run"
    (run / "rounds").mkdir(parents=True)
    (run / "plan.json").write_text(json.dumps({"acceptance": [a.model_dump() for a in ITEMS]}))
    rec = RoundRecord(index=0, kind="baseline", gates=GATES,
                      renders=RenderSet(views=[RenderView(name="front", path="front.png")]), judgment=judgment)
    path = run / "rounds" / "r00.json"
    path.write_text(rec.model_dump_json())
    return path


def test_replay_reproduces_a_stored_verdict_and_reports_its_scoring_version(tmp_path):
    samples = [_sample(0.9, "interpenetration", "render_artifacts"), _sample(0.8, "interpenetration", "render_artifacts")]
    j = aggregate_samples(R, samples, gates=GATES, acceptance_items=ITEMS, judge_backend="fake:judge")
    # uncapped 0.85, render_artifacts -0.05 → 0.80; interpenetration vetoed by the passed connectivity
    # gate; caps: contract 0.75, graded acceptance 0.6 + 0.4·1/2 = 0.8 → 0.75
    assert j.overall == pytest.approx(0.75) and not j.passed
    raw = json.loads(j.raw)
    assert raw["overridden"] == ["interpenetration"] and raw["scoring_version"] == SCORING_VERSION

    row = ro.replay_round(_write_round(tmp_path, j))
    assert row is not None
    assert row.stored == j.overall and abs(row.replay - j.overall) <= 1e-9 and abs(row.delta) <= 1e-9
    assert row.scoring_version_stored == SCORING_VERSION == row.scoring_version_now
    assert row.rubric_hash_stored == row.rubric_hash_now == R.content_hash()
    assert row.overridden == ["interpenetration"]
    assert row.caps_before == row.caps_after == ["contract_violation", "missing_must_acceptance"]
    assert row.defects_before == row.defects_after == ["render_artifacts"]
    assert row.gate_errors == 1 and row.stored_passed is False and row.replay_passed is False


def test_identity_holds_same_version_verdicts_and_reports_older_ones_as_drift(tmp_path, capsys):
    j = aggregate_samples(R, [_sample(0.9)], gates=GATES, acceptance_items=ITEMS)

    def stored_as(version: int, overall: float) -> Judgment:
        raw = json.loads(j.raw) | {"scoring_version": version, "overall": overall}
        return j.model_copy(update={"overall": overall, "raw": json.dumps(raw)})

    off = round(j.overall + 0.05, 4)
    _write_round(tmp_path / "now", stored_as(SCORING_VERSION, off))
    assert ro.main([str(tmp_path / "now"), "--rubric", R.name, "--identity"]) == 1
    assert "IDENTITY FAILED: 1 verdict(s)" in capsys.readouterr().err

    # a corpus with NOTHING at today's version is drift all the way down: the guard is dormant,
    # says so, and refuses the green exit unless told the empty set is expected
    _write_round(tmp_path / "old", stored_as(1, off))
    assert ro.main([str(tmp_path / "old"), "--rubric", R.name, "--identity"]) == 2
    err = capsys.readouterr().err
    assert "identity guard is DORMANT" in err and f"scoring version {SCORING_VERSION}" in err and "nothing was checked" in err
    assert ro.main([str(tmp_path / "old"), "--rubric", R.name, "--identity", "--allow-empty-identity"]) == 0
    assert "identity guard is DORMANT" in capsys.readouterr().err
    rep = ro.replay_corpus([tmp_path / "old"], rubric_name=R.name)
    assert rep.n == 1 and rep.n_moved == 1 and rep.n_scoring_drift == 1
    assert rep.rows[0].scoring_version_stored == 1 and rep.rows[0].delta == pytest.approx(-0.05)

    _write_round(tmp_path / "same", stored_as(SCORING_VERSION, j.overall))
    assert ro.main([str(tmp_path / "same"), "--rubric", R.name, "--identity"]) == 0
    assert "identity OK: 1 of 1" in capsys.readouterr().out


def test_a_verdict_written_before_the_stamp_reads_as_version_0(tmp_path):
    j = aggregate_samples(R, [_sample(0.9)], gates=GATES, acceptance_items=ITEMS)
    raw = json.loads(j.raw)
    raw.pop("scoring_version", None)
    _write_round(tmp_path, j.model_copy(update={"raw": json.dumps(raw)}))
    assert ro.replay_corpus([tmp_path], rubric_name=R.name).rows[0].scoring_version_stored == 0


def test_a_round_without_a_judged_sample_is_skipped(tmp_path):
    assert ro.replay_round(_write_round(tmp_path, None)) is None
