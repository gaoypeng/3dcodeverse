"""The complexity block on a record.

``record.extra["complexity"]`` is what makes a verdict readable: it says how much
artifact the score was earned on.  (The score-vs-complexity report over a corpus of
these is `eval/bench/complexity_report.py`, tested in `eval/tests`.)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from codeverse3d.contracts.artifacts import Judgment, Measurement
from codeverse3d.contracts.common import Backends, Language, Track, Usage
from codeverse3d.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse3d.contracts.run import RoundRecord, RunRecord, RunStatus
from codeverse3d.contracts.spec import Spec
from codeverse3d.record.record import complexity_block, fill_derived, round_summary


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


def _record(rounds: list[RoundRecord]) -> RunRecord:
    bbox = BBox(center=(0.0, 0.0, 0.0), extents=(0.1, 0.1, 0.1))
    plan = StaticPlan(object_name="Thing", summary="a thing",
                      overall_bbox=BBox(center=(0.0, 0.5, 0.0), extents=(1.0, 1.0, 1.0)),
                      parts=[PartPlan(name=f"P{i}", role="body", description="d", bbox=bbox)
                             for i in range(4)])
    return RunRecord(
        spec=Spec(id="t", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="a thing",
                  backends=Backends()),
        plan=plan, workspace="/tmp/x", status=RunStatus.MAX_ROUNDS, rounds=rounds,
        started_at=datetime.now(UTC),
    )


def test_record_carries_the_last_rounds_complexity_and_the_trail() -> None:
    rec = fill_derived(_record([_round(0, 0.80, 0.42), _round(1, 0.60, 0.55)]))
    block = rec.extra["complexity"]
    assert block["index"] == 0.55           # the build the run ENDS at, whatever it scored
    assert block["plan_parts"] == 4
    assert block["parts_per_plan_part"] == pytest.approx(2.0)
    assert block["by_round"] == [0.42, 0.55]
    assert [round_summary(r)["complexity"] for r in rec.rounds] == [0.42, 0.55]


def test_complexity_block_falls_back_to_the_last_measured_round() -> None:
    rec = _record([_round(0, 0.6, 0.42), _round(1, 0.5, None)])
    assert complexity_block(rec)["index"] == 0.42
