"""Spec.checks: a project's deterministic checks join every round's gates as project:<name>."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.spec import ProjectCheck, Spec
from codeverse3d.tracks.project_checks import run_project_checks

HERE = "tests.orchestrator_tracks.test_project_checks"


def roof_check(*, glb: Path, workspace: Path, spec: Spec, measurement: Any, round_index: int,
               args: dict[str, Any]) -> GateReport:
    within = args["within"]
    sev = Severity.ERROR if within < 0.85 else Severity.INFO
    return GateReport.of("", [GateFinding(gate="", severity=sev, target="Roof",
                                          message=f"{within:.0%} of the roof within 0.5 m of the survey (round {round_index})",
                                          fix_hint="build the roof planes from the survey", data={"kind": "roof_shape"})])


def two_reports(**_: Any) -> list[GateReport]:
    return [GateReport.of("eaves", []), GateReport.of("project:other", [])]


def broken(**_: Any) -> GateReport:
    raise RuntimeError("reference mesh missing")


def _spec(*checks: ProjectCheck) -> Spec:
    return Spec(id="x", track=Track.STATIC_OBJECT, language=Language.THREEJS, prompt="a house", checks=list(checks))


def test_checks_are_named_and_imported_by_path() -> None:
    with pytest.raises(ValueError):
        ProjectCheck(name="Survey Check", entry=f"{HERE}:roof_check")
    with pytest.raises(ValueError):
        ProjectCheck(name="survey", entry="no_function_here")
    assert "checks" not in _spec().model_dump(mode="json")  # a spec without checks serialises as before


def test_a_check_joins_the_round_as_project_gate(tmp_path: Path) -> None:
    spec = _spec(ProjectCheck(name="survey", entry=f"{HERE}:roof_check", args={"within": 0.59}))
    [rep] = run_project_checks(spec, tmp_path, tmp_path / "object.glb", None, 2)
    assert rep.gate == "project:survey" and not rep.passed
    err = rep.errors[0]
    assert err.gate == "project:survey" and err.data["kind"] == "roof_shape" and "(round 2)" in err.message

    spec = _spec(ProjectCheck(name="survey", entry=f"{HERE}:two_reports"))
    assert [r.gate for r in run_project_checks(spec, tmp_path, tmp_path / "o.glb", None, 0)] == [
        "project:survey.eaves", "project:other"]


def test_a_broken_check_warns_and_never_fails_the_round(tmp_path: Path) -> None:
    spec = _spec(ProjectCheck(name="survey", entry=f"{HERE}:broken"),
                 ProjectCheck(name="missing", entry="no_such_module_here:check"))
    reps = run_project_checks(spec, tmp_path, tmp_path / "o.glb", None, 0)
    assert [r.gate for r in reps] == ["project:survey", "project:missing"]
    assert all(r.passed for r in reps)
    assert "RuntimeError: reference mesh missing" in reps[0].findings[0].message
    assert reps[1].findings[0].severity is Severity.WARN


@pytest.mark.parametrize("rubric", ["reference", "static_object"])
def test_a_project_error_caps_the_score(tmp_path: Path, rubric: str) -> None:
    from codeverse3d.judges.rubrics import apply_caps, load_rubric

    spec = _spec(ProjectCheck(name="survey", entry=f"{HERE}:roof_check", args={"within": 0.59}))
    gates = run_project_checks(spec, tmp_path, tmp_path / "o.glb", None, 0)
    assert apply_caps(load_rubric(rubric), 0.9, gates, {}).overall == pytest.approx(0.6)
    ok = _spec(ProjectCheck(name="survey", entry=f"{HERE}:roof_check", args={"within": 0.97}))
    assert apply_caps(load_rubric(rubric), 0.9, run_project_checks(ok, tmp_path, tmp_path / "o.glb", None, 0),
                      {}).overall == pytest.approx(0.9)
