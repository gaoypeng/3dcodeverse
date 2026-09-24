"""Name gate findings by their typed kind and validate the routing table."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse3d.skills.registry import ROUTES, finding_kind, finding_kinds, select

E, W, I = Severity.ERROR, Severity.WARN, Severity.INFO  # noqa: E741


def _f(gate, sev, message="", **data):
    return GateFinding(gate=gate, severity=sev, message=message, data=data)


@pytest.mark.parametrize(("finding", "kind"), [
    # the gate's own kind, whatever the message says (N52: the regex read "interpenetrate")
    (_f("connectivity", W, "part 'X' contains 3 tiny disconnected island(s)", kind="penetration"),
     "connectivity/penetration"),
    (_f("scene_frames", E, "nothing moves: the largest change …", kind="no_motion"), "scene_frames/no_motion"),
    # the lint gates are one family; a finding recorded before its gate typed it is untyped
    (_f("lint:urdf", W, "link 'lid' never appears as a string", kind="link_name"), "lint/link_name"),
    (_f("lint:blender", W, "src/parts/leg.py is never imported by src/model.py"), "lint/untyped"),
    # INFO is census: "all 7 parts are connected" must not attach the penetration sheet
    (_f("connectivity", I, "contact ledger: 7 parts", kind="ledger"), None),
])
def test_a_finding_is_named_by_its_gate_and_typed_kind(finding, kind):
    assert finding_kind(finding) == kind


def test_finding_kinds_accepts_reports_and_findings_distinct_in_order():
    a = _f("connectivity", E, kind="penetration")
    b = _f("contract", W, kind="part_bbox")
    assert finding_kinds([a, b]) == finding_kinds([GateReport(gate="x", passed=False, findings=[a, b])]) \
        == finding_kinds([a, b, a]) == ["connectivity/penetration", "contract/part_bbox"]
    assert finding_kinds([]) == [] and finding_kinds(None) == []


_LEG = "import bpy\n\n\ndef build_leg():\n    bpy.ops.mesh.primitive_cube_add(size=1)\n    obj = bpy.context.object\n    obj.name = 'Leg'\n    return obj\n"
_JOIN = "bpy.ops.object.join()\n"


@pytest.mark.parametrize(("model", "r9"), [
    ("import bpy\n", True),                                                           # parts/leg.py never imported
    ("import bpy\nfrom parts.leg import build_leg\n\nbuild_leg()\n" + _JOIN, False),  # a bpy trap is not R9's
])
def test_r9_answers_a_part_file_never_imported_and_no_other_lint(tmp_ws, library, model, r9):
    """R9 fired on ANY blender lint finding while the lints wrote no kind."""
    from codeverse3d.languages.blender import lint_workspace

    (tmp_ws.src / "parts").mkdir(parents=True, exist_ok=True)
    (tmp_ws.src / "parts" / "leg.py").write_text(_LEG)
    (tmp_ws.src / "model.py").write_text(model)
    lint = lint_workspace(tmp_ws)
    assert [f for f in lint.findings if f.severity != I], "the fixture must give the lint something to report"
    got = select("static_object", "blender", "repair", findings=[lint], library=library)
    assert ("R9" in next(s for s in got if s.name == "c3d-blender-forms").rules) is r9


def test_r13_answers_a_joint_moving_against_the_plan_not_a_skipped_check(library):
    from codeverse3d.tracks.articulated_object import MOTION_GATE, MOTION_SKIPPED, MOTION_WRONG

    def rules(kind):
        rep = GateReport(gate=MOTION_GATE, passed=False, findings=[_f(MOTION_GATE, E, "m", kind=kind)])
        got = select("articulated_object", "urdf_blender", "repair", findings=[rep], library=library)
        return next(s for s in got if s.name == "c3d-urdf-joints").rules

    assert "R13" in rules(MOTION_WRONG) and "R13" not in rules(MOTION_SKIPPED)


def test_r19_answers_a_scene_with_nothing_moving(library):
    """N52: R19 keyed on a gl_frames-only regex, so a scene's no_motion ERROR routed nothing."""
    nothing_moves = _f("scene_frames", E, "nothing moves: …", kind="no_motion")
    got = select("scene", "scene_threejs", "refine", findings=[GateReport(gate="scene_frames", passed=False, findings=[nothing_moves])],
                 library=library)
    motion = next(s for s in got if s.name == "c3d-scene-motion")
    assert "R19" in motion.rules and motion.gate_fired


# --------------------------------------------------------------------------- the table
#: Retired ids remain reserved because run records refer to routing decisions by id.
RETIRED_RULES = {"R5": "c3d-form-manifest, cut 2026-08-25 (read 2/19); docs/SKILLS_LEDGER.md"}


def test_rule_ids_are_unique_and_the_design_numbers_are_all_present():
    ids = [r.rule for r in ROUTES]
    assert len(ids) == len(set(ids))
    assert not (set(ids) & set(RETIRED_RULES)), (
        f"a retired route id is back in the table: {set(ids) & set(RETIRED_RULES)}")
    for n in range(1, 24):
        if f"R{n}" in RETIRED_RULES:
            continue
        assert any(r.rule == f"R{n}" for r in ROUTES), f"R{n} is missing from the table"
    assert [r for r in ROUTES if r.rule.startswith("R24")], "R24 (the graphics gate row) is missing"


def test_gate_fired_rows_always_outrank_standing_rows():
    """Law 2: a repair round must spend its budget on what actually broke."""
    fired = [r for r in ROUTES if r.gate_fired]
    standing = [r for r in ROUTES if not r.gate_fired]
    assert min(r.priority for r in fired) > max(r.priority for r in standing)
    assert all(r.priority >= 90 for r in fired)
