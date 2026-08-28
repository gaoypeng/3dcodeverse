"""The complexity block on a record, and the score-vs-complexity report.

``record.extra["complexity"]`` is what makes a verdict readable: it says how much
artifact the score was earned on.  ``bench/complexity_report.py`` is the study
that turns a corpus of those into "does score rise or fall with complexity, and
what does a complexity point cost?" (docs/COMPLEXITY.md).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from datetime import UTC  # noqa: E402

from bench import complexity_report as CR  # noqa: E402
from codeverse.contracts.artifacts import Judgment, Measurement  # noqa: E402
from codeverse.contracts.common import Backends, Language, Track, Usage  # noqa: E402
from codeverse.contracts.plan import BBox, PartPlan, StaticPlan  # noqa: E402
from codeverse.contracts.run import RoundRecord, RunRecord, RunStatus  # noqa: E402
from codeverse.contracts.spec import Spec  # noqa: E402
from codeverse.flywheel.record import complexity_block, fill_derived, round_summary  # noqa: E402


def _measurement(index: float, parts: int = 8) -> Measurement:
    return Measurement(
        bbox_min=(0, 0, 0), bbox_max=(1, 1, 1), extents=(1, 1, 1), center=(0.5, 0.5, 0.5),
        tri_count=1000, n_meshes=parts, n_islands=parts,
        extra={"complexity": {"version": 1, "index": index, "band": "moderate", "part_count": parts,
                              "tri_count": 1000, "materials": 3, "silhouette": 4.0,
                              "feature_density": 500.0, "symmetry_groups": 1, "hollowness": 0.2,
                              "assembly_depth": 1, "components": {}, "extra": {}}},
    )


def _round(index: int, score: float, cx: float | None) -> RoundRecord:
    return RoundRecord(
        index=index, kind="baseline" if index == 0 else "refine", commit="c" * 12,
        measurement=_measurement(cx) if cx is not None else None,
        judgment=Judgment(rubric="static_object_v1", judge_backend="gemini:x",
                          scores={"geometry_detail": score}, overall=score, passed=score >= 0.72,
                          summary="s"),
        usage=Usage(cost_usd=0.5),
    )


def _record(rounds: list[RoundRecord], best: int | None = None) -> RunRecord:
    bbox = BBox(center=(0.0, 0.0, 0.0), extents=(0.1, 0.1, 0.1))
    plan = StaticPlan(object_name="Thing", summary="a thing",
                      overall_bbox=BBox(center=(0.0, 0.5, 0.0), extents=(1.0, 1.0, 1.0)),
                      parts=[PartPlan(name=f"P{i}", role="body", description="d", bbox=bbox)
                             for i in range(4)])
    return RunRecord(
        spec=Spec(id="t", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="a thing",
                  backends=Backends()),
        plan=plan, workspace="/tmp/x", status=RunStatus.PASSED, rounds=rounds, best_round=best,
        started_at=datetime.now(UTC),
    )


def test_record_carries_the_delivered_artifact_complexity() -> None:
    rec = fill_derived(_record([_round(0, 0.60, 0.42), _round(1, 0.80, 0.55)], best=1))
    block = rec.extra["complexity"]
    assert block["index"] == 0.55           # the BEST round's build, not the last measured
    assert block["plan_parts"] == 4
    assert block["parts_per_plan_part"] == pytest.approx(2.0)
    assert block["by_round"] == [0.42, 0.55]
    assert [r["complexity"] for r in rec.extra["rounds_summary"]] == [0.42, 0.55]


def test_complexity_block_falls_back_to_the_last_measured_round() -> None:
    rec = _record([_round(0, 0.6, 0.42), _round(1, 0.5, None)], best=1)
    assert complexity_block(rec)["index"] == 0.42


def test_no_measurement_means_no_block() -> None:
    rec = fill_derived(_record([_round(0, 0.6, None)], best=0))
    assert "complexity" not in rec.extra
    assert round_summary(rec.rounds[0])["complexity"] is None


# ------------------------------------------------------------------ the report
def _write_run(root: Path, slug: str, *, index: float, overall: float, detail: float,
               cost: float = 1.0, minutes: float = 10.0) -> Path:
    run = root / "runs" / slug
    (run / "artifacts").mkdir(parents=True)
    rec = {
        "spec": {"track": "static_object", "language": "blender", "tags": ["bench", "hard"]},
        "plan": {"parts": [{"name": "a"}, {"name": "b"}]},
        "status": "passed", "best_round": 0,
        "total_usage": {"cost_usd": cost},
        "telemetry": {"cost": {"wall_clock_s": minutes * 60}},
        "rounds": [{
            "index": 0, "gates": [],
            "measurement": _measurement(index).model_dump(mode="json"),
            "judgment": {"overall": overall, "passed": overall >= 0.72,
                         "scores": {c: detail for c in CR.CRITERIA}, "issues": []},
        }],
    }
    (run / "record.json").write_text(json.dumps(rec))
    return run


def test_collect_and_report_over_a_battery(tmp_path: Path) -> None:
    battery = tmp_path / "batt"
    _write_run(battery, "low", index=0.35, overall=0.85, detail=0.85, cost=0.5, minutes=5)
    _write_run(battery, "mid", index=0.55, overall=0.70, detail=0.70, cost=1.5, minutes=20)
    _write_run(battery, "high", index=0.75, overall=0.55, detail=0.55, cost=3.0, minutes=40)
    rows, skipped = CR.collect([battery])
    assert skipped == 0 and len(rows) == 3
    assert {r["slug"] for r in rows} == {"low", "mid", "high"}
    assert rows[0]["plan_parts"] == 2 and rows[0]["n_materials"] == 3
    # the whole point of the study: a monotone fall shows up as a strong negative r
    xs, ys = CR._pairs(rows, "index", "overall")
    assert CR.pearson(xs, ys) < -0.98
    assert CR.spearman(xs, ys) == pytest.approx(-1.0)
    money = CR.dollars_per_point(rows)
    assert money["runs"] == 3 and money["total_usd"] == pytest.approx(5.0)
    assert money["usd_per_point"] == pytest.approx(5.0 / (100 * (0.35 + 0.55 + 0.75)), abs=5e-5)
    report = CR.render_report(rows, skipped)
    assert "score vs complexity" in report and "cost of complexity" in report
    assert "| batt/high |" in report


def test_battery_expectations_flag_out_of_band_runs(tmp_path: Path) -> None:
    battery = tmp_path / "batt"
    _write_run(battery, "cx_a03_stool", index=0.30, overall=0.9, detail=0.9)
    _write_run(battery, "cx_d25_engine", index=0.40, overall=0.9, detail=0.9)
    rows, _ = CR.collect([battery])
    yaml_path = tmp_path / "b.yaml"
    yaml_path.write_text(
        "name: b\ntrack: static_object\nlanguage: blender\nprompts:\n"
        "  - id: cx_a03_stool\n    tier: easy\n    prompt: p\n    expected_complexity: [0.25, 0.42]\n"
        "  - id: cx_d25_engine\n    tier: hard\n    prompt: p\n    expected_complexity: [0.68, 0.88]\n"
        "  - id: cx_missing\n    tier: hard\n    prompt: p\n    expected_complexity: [0.5, 0.6]\n"
    )
    table = CR.battery_expectations(yaml_path, rows)
    assert "| cx_a03_stool | 0.25–0.42 | 0.300 | yes |" in table
    assert "LOW" in table                      # the engine collapsed to a stool
    assert "not run" in table                  # a prompt with no run is still listed


def test_real_battery_bands_are_sane() -> None:
    """complexity_v3 is the ladder: every band inside [0,1], ordered, and the
    prompts must actually climb."""
    import yaml

    data = yaml.safe_load((REPO / "bench" / "prompts" / "complexity_v3.yaml").read_text())
    bands = [(p["id"], p["expected_complexity"]) for p in data["prompts"]]
    assert len(bands) == 12
    for pid, (lo, hi) in bands:
        assert 0.0 < lo < hi <= 1.0, pid
    lows = [lo for _, (lo, _hi) in bands]
    assert lows == sorted(lows) and lows[-1] > lows[0] + 0.3


def test_row_recomputes_the_vector_when_the_record_has_none(tmp_path: Path) -> None:
    """The historic corpus predates the vector: the report must fall back to the
    GLB rather than skipping the run."""
    import trimesh

    run = tmp_path / "runs" / "old"
    (run / "artifacts").mkdir(parents=True)
    trimesh.Scene({"Box": trimesh.creation.box(extents=(0.3, 0.3, 0.3))}).export(
        str(run / "artifacts" / "object.glb"))
    (run / "record.json").write_text(json.dumps({
        "spec": {"track": "static_object", "language": "blender", "tags": []},
        "plan": {"parts": []}, "status": "passed", "best_round": 0,
        "total_usage": {"cost_usd": 1.0}, "rounds": [{
            "index": 0, "gates": [],
            "judgment": {"overall": 0.5, "passed": False,
                         "scores": dict.fromkeys(CR.CRITERIA, 0.5), "issues": []}}]}))
    row = CR.row_for(run, "old_battery")
    assert row is not None and row["part_count"] == 1 and row["index"] > 0
