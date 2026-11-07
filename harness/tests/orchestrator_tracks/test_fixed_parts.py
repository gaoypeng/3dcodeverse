"""Spec.fixed_parts: project-seeded files no session may rewrite, never planned, measured as declared."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from codeverse3d.contracts.artifacts import Measurement, PartMeasure, Severity
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse3d.contracts.spec import FixedPart, Spec
from codeverse3d.workspace import Workspace

SHELL = FixedPart(path="src/parts/survey_shell.js", name="SurveyShell", export="buildSurveyShell",
                  bbox_min=(-7.0, 0.0, -7.5), bbox_max=(7.0, 24.3, 7.5), note="measured survey geometry")
DATA = FixedPart(path="src/house.js", export="HOUSE")


def _spec(*fixed: FixedPart) -> Spec:
    return Spec(id="x", track=Track.STATIC_OBJECT, language=Language.THREEJS, prompt="a house",
                fixed_parts=list(fixed))


def test_fixed_parts_must_be_sources_with_a_whole_box() -> None:
    with pytest.raises(ValueError, match="under src/"):
        _spec(FixedPart(path="plan.json"))
    with pytest.raises(ValueError, match="under src/"):
        _spec(FixedPart(path="src/../spec.json"))
    with pytest.raises(ValueError, match="both bbox_min and bbox_max"):
        _spec(FixedPart(path="src/a.js", bbox_min=(0, 0, 0)))


def test_the_plan_key_moves_only_when_a_spec_has_fixed_parts() -> None:
    from codeverse3d.orchestrator import hash_inputs
    from codeverse3d.tracks.lifecycle import plan_stage_inputs

    plain = _spec()
    assert "fixed_parts" not in plan_stage_inputs(plain)  # every existing run's plan key stays put
    assert hash_inputs(plan_stage_inputs(plain)) != hash_inputs(plan_stage_inputs(_spec(SHELL)))


def test_the_planner_and_agent_are_told_and_plain_specs_are_unchanged() -> None:
    from codeverse3d.tracks.prompting import constraints_text

    assert constraints_text(_spec()) == "(none)"
    text = constraints_text(_spec(SHELL, DATA))
    assert "FIXED PARTS" in text and "never attach_to them" in text
    assert "src/parts/survey_shell.js exports buildSurveyShell, builds the part 'SurveyShell'" in text
    assert "x -7.00..7.00, y 0.00..24.30, z -7.50..7.50 m" in text
    assert "- src/house.js exports HOUSE" in text


def test_every_session_gets_the_fixed_parts_read_only(tmp_path: Path) -> None:
    from codeverse3d.tracks.generation import _read_only

    ws = Workspace(tmp_path / "run").create()
    ws.write_json(ws.spec_path, _spec(SHELL, DATA))
    assert _read_only("threejs", ws) == ["src/parts/survey_shell.js", "src/house.js"]
    assert _read_only("scene_threejs", ws)[0] == "src/lib/"  # the language's own stay first


def _measured(*parts: PartMeasure) -> Measurement:
    return Measurement(bbox_min=(-8, 0, -8), bbox_max=(8, 25, 8), extents=(16, 25, 16), center=(0, 12.5, 0),
                       tri_count=1000, n_meshes=len(parts), n_islands=len(parts), parts=list(parts))


def test_the_gate_passes_a_part_built_as_declared_and_errs_on_a_moved_or_missing_one() -> None:
    from codeverse3d.spatial.contract import FIXED_GATE, check_fixed_parts

    as_declared = PartMeasure(name="SurveyShell", bbox_min=(-7.0, 0.0, -7.5), bbox_max=(7.0, 24.3, 7.51))
    assert check_fixed_parts(_measured(as_declared), [SHELL, DATA]).passed

    squashed = PartMeasure(name="SurveyShell", bbox_min=(-7.0, 0.0, -7.5), bbox_max=(7.0, 24.3, 6.6))
    rep = check_fixed_parts(_measured(squashed), [SHELL])
    assert not rep.passed and rep.gate == FIXED_GATE
    assert rep.errors[0].data["kind"] == "fixed_part_bbox" and rep.errors[0].data["off_m"] == pytest.approx(0.9)

    rep = check_fixed_parts(_measured(PartMeasure(name="Facade", bbox_min=(0, 0, 0), bbox_max=(1, 1, 1))), [SHELL])
    assert [f.data["kind"] for f in rep.findings if f.severity is Severity.ERROR] == ["fixed_part_missing"]


def test_a_moved_fixed_part_caps_the_reference_rubric() -> None:
    from codeverse3d.contracts.artifacts import GateFinding, GateReport
    from codeverse3d.judges.rubrics import apply_caps, load_rubric

    gate = GateReport.of("contract_fixed", [GateFinding(gate="contract_fixed", severity=Severity.ERROR,
                                                        message="moved", data={"kind": "fixed_part_bbox"})])
    assert apply_caps(load_rubric("reference"), 0.9, [gate], {}).overall == pytest.approx(0.6)


def test_a_plan_that_claims_a_fixed_part_is_reasked() -> None:
    from codeverse3d.tracks.planner import fixed_part_clash

    box = BBox(center=(0, 1, 0), extents=(1, 2, 1))
    plan = StaticPlan(object_name="House", summary="s", overall_bbox=box,
                      parts=[PartPlan(name="StreetFacade", role="facade", description="the street wall", bbox=box),
                             PartPlan(name="survey_shell", role="shell", description="the shell", bbox=box)])
    assert "['survey_shell']" in fixed_part_clash(plan, _spec(SHELL))
    assert fixed_part_clash(plan, _spec()) == ""


def test_the_skeleton_keeps_a_fixed_part_and_stops_without_one(tmp_path: Path) -> None:
    from codeverse3d.tracks.lifecycle import _skeleton

    ws = Workspace(tmp_path / "run").create()
    shell = ws.root / SHELL.path
    shell.parent.mkdir(parents=True, exist_ok=True)
    shell.write_text("export function buildSurveyShell() {}\n")

    def overwrite(ws_: Workspace, plan: object) -> list[Path]:  # a language whose skeleton rewrites src/
        shell.write_text("// placeholder\n")
        return [shell]

    ctx = SimpleNamespace(ws=ws, plan=None, spec=_spec(SHELL), runtime=SimpleNamespace(skeleton=overwrite))
    _skeleton(None, ctx)  # type: ignore[arg-type]
    assert shell.read_text() == "export function buildSurveyShell() {}\n"

    ctx.spec = _spec(SHELL, FixedPart(path="src/missing.js"))
    with pytest.raises(FileNotFoundError, match="src/missing.js"):
        _skeleton(None, ctx)  # type: ignore[arg-type]


def test_fixed_parts_are_object_track_only_and_stored_canonical() -> None:
    assert _spec(FixedPart(path="src//parts/./a.js")).fixed_parts[0].path == "src/parts/a.js"
    with pytest.raises(ValueError, match="object tracks only"):
        Spec(id="x", track=Track.SCENE, language=Language.SCENE_THREEJS, prompt="p", fixed_parts=[DATA])


def test_refine_and_repair_prompts_carry_the_fixed_parts() -> None:
    from codeverse3d.prompts import load_text

    for name in ("tracks/refine_object.j2", "tracks/repair.j2"):
        assert "{% if fixed_parts %}" in load_text(name)
