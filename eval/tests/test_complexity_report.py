"""`bench/complexity_report.py`: does score rise or fall with complexity, and what does a
complexity point cost?  (The block a record carries is tested with the harness.)"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench import complexity_report as CR
from bench.stats import correlation
from codeverse3d.contracts.artifacts import Measurement
from codeverse3d.contracts.plan import BBox, PartPlan, StaticPlan

REPO = Path(__file__).resolve().parents[1]


def _measurement(index: float, parts: int = 8) -> Measurement:
    return Measurement(
        bbox_min=(0, 0, 0), bbox_max=(1, 1, 1), extents=(1, 1, 1), center=(0.5, 0.5, 0.5),
        tri_count=1000, n_meshes=parts, n_islands=parts,
        extra={"complexity": {"version": 1, "index": index, "band": "moderate", "part_count": parts,
                              "tri_count": 1000, "materials": 3, "silhouette": 4.0,
                              "feature_density": 500.0, "symmetry_groups": 1, "hollowness": 0.2,
                              "assembly_depth": 1, "components": {}, "extra": {}}},
    )


# ------------------------------------------------------------------ the report
def _write_run(root: Path, slug: str, *, index: float, overall: float, detail: float,
               cost: float = 1.0, minutes: float = 10.0) -> Path:
    run = root / "runs" / slug
    (run / "artifacts").mkdir(parents=True)
    box = BBox(center=(0, 0.5, 0), extents=(1, 1, 1))
    rec = {
        "spec": {"id": slug, "prompt": "a chair", "track": "static_object", "language": "blender",
                 "tags": ["bench", "hard"]},
        "workspace": str(run),
        "plan": StaticPlan(object_name="Chair", summary="s", overall_bbox=box, parts=[
            PartPlan(name=n, role="r", description="d", bbox=box) for n in ("a", "b")]).model_dump(mode="json"),
        "status": "passed", "best_round": 0,
        "total_usage": {"cost_usd": cost},
        "extra": {"budget": {"elapsed_min": minutes}},  # a record before step timing counts its clock
        "rounds": [{
            "index": 0, "kind": "baseline", "gates": [],
            "measurement": _measurement(index).model_dump(mode="json"),
            "judgment": {"rubric": "static_object_v1", "overall": overall, "passed": overall >= 0.72,
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
    assert {r["slug"]: r["minutes"] for r in rows} == {"low": 5, "mid": 20, "high": 40}
    assert rows[0]["plan_parts"] == 2 and rows[0]["n_materials"] == 3
    # the whole point of the study: a monotone fall shows up as a strong negative r
    xs, ys = CR._pairs(rows, "index", "overall")
    assert correlation(xs, ys) < -0.98
    assert correlation(xs, ys, ranked=True) == pytest.approx(-1.0)
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
