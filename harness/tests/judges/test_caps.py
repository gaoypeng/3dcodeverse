import pytest

from codeverse3d.contracts.artifacts import GateFinding, GateReport, RenderView, Severity
from codeverse3d.contracts.plan import AcceptanceItem
from codeverse3d.judges.rubrics import (
    AcceptanceVerdict,
    CriterionScore,
    JudgeOutput,
    aggregate_samples,
    apply_caps,
    load_rubric,
    measured_absent,
    veto_measured_defects,
)

R = load_rubric("static_object_v1")
A = load_rubric("articulated_v1")
S = load_rubric("scene_v1")
POSE = [RenderView(name="pose_rest", path="p")]


def _gate(name, sev, msg, target="", **data):
    return GateReport(gate=name, passed=sev != Severity.ERROR,
                      findings=[GateFinding(gate=name, severity=sev, target=target, message=msg, data=data)])


def _connectivity(passed: bool, *msgs: tuple[str, str]):
    sev = {"error": Severity.ERROR, "warn": Severity.WARN, "info": Severity.INFO}
    return GateReport(gate="connectivity", passed=passed,
                      findings=[GateFinding(gate="connectivity", severity=sev[s], target="", message=m) for s, m in msgs])


E, W = Severity.ERROR, Severity.WARN


@pytest.mark.parametrize(("rubric", "gates", "kw", "overall", "rules"), [
    (R, [_gate("build:blender", E, "Traceback ... NameError")], {}, 0.0, {"build_error"}),
    (R, [_gate("connectivity", E, "part Leg1 is 3cm above others", kind="floating")], {}, 0.6, {"floating_part"}),
    (R, [_gate("connectivity", E, "Leg1 has no_contact with any part")], {}, 0.6, {"floating_part"}),
    (R, [_gate("connectivity", W, "floating sliver")], {}, 0.9, set()),
    (R, [_gate("connectivity", E, "penetration Seat/Leg 12mm"), _gate("connectivity", E, "Arm floating", kind="floating")],
     {}, 0.6, {"floating_part", "penetration_error"}),
    (R, [_gate("connectivity", E, "'Leg' and 'Seat' interpenetrate by ≈15 mm", target="Leg")], {}, 0.7, {"penetration_error"}),
    (A, [_gate("connectivity", E, "'Leg' and 'Seat' interpenetrate by ≈15 mm", target="Leg")], {"views": POSE}, 0.7,
     {"penetration_error"}),
    (A, [_gate("joint_sweep", E, "link 'DrawerKnob' touches nothing connected to the root at pose rest "
                                 "(nearest 'Carcass' at 56.4 mm)", target="DrawerKnob")],
     {"views": POSE}, 0.6, {"floating_part"}),
    (S, [], {"console_errors": ["TypeError: x is undefined"]}, 0.6, {"console_error"}),
    (S, [_gate("shader_probe", E, "GLSL compile failed in water.js", kind="shader")], {}, 0.5, {"shader_error"}),
    # the older floating_part rule (gate *, cap 0.6) fires too; the tighter cap wins
    (S, [_gate("scene_placement", E, "Pondside/Lantern is floating 0.30 m above PondWater", target="Pondside/Lantern")],
     {"defects_present": {}}, 0.6, {"floating_or_sunken_asset"}),
])
def test_gate_caps(rubric, gates, kw, overall, rules):
    res = apply_caps(rubric, 0.9, gates, {}, [], **kw)
    assert res.overall == overall
    applied = {c.rule for c in res.caps_applied}
    assert applied == rules if rules == set() else rules <= applied


PASSED = _connectivity(True, ("info", "all 9 parts are connected (9 contacts, gap <= 2 mm)"),
                       ("warn", "'Seat' and 'Leg' overlap by 1.4 mm (weld)"))
CONTRACT_ERR = GateReport(gate="contract", passed=False, findings=[GateFinding(
    gate="contract", severity=E, target="bbox", message="height 0.31 m vs plan 0.45 m (31% off)")])


@pytest.mark.parametrize(("rubric", "defect", "gates", "absent"), [
    (R, "floating_part", [PASSED], True),
    (R, "interpenetration", [PASSED], True),
    (R, "floating_part", [_connectivity(False, ("error", "part 'Seat' is floating 12 mm above 'Leg'"))], False),
    (R, "interpenetration", [_connectivity(False, ("error", "'Leg' and 'Seat' interpenetrate by ≈15 mm"))], False),
    (R, "floating_part", [], False),  # a gate that did not run measured nothing
    (R, "wrong_object", [_connectivity(True)], False),  # no gate measures 'wrong object'
    # a contract ERROR about the bbox does not switch off the veto connectivity earned
    (R, "floating_part", [CONTRACT_ERR, _connectivity(True, ("info", "all 4 parts are connected"))], True),
    (R, "interpenetration", [CONTRACT_ERR, _connectivity(True, ("info", "all 4 parts are connected"))], True),
    (S, "floating_or_sunken_asset", [GateReport(gate="scene_placement", passed=True, findings=[GateFinding(
        gate="scene_placement", severity=Severity.INFO, target="scene", message="13 assets checked: 0 floating")])], True),
])
def test_measured_absent(rubric, defect, gates, absent):
    """Gates decide geometry (law 3): a passed gate vetoes the VLM's claim of what it measured."""
    assert measured_absent(rubric, defect, gates) is absent


def test_veto_switches_off_only_measured_defects_and_the_contract_cap_still_applies():
    kept, overridden = veto_measured_defects(R, {"interpenetration": True, "floating_part": True, "wrong_object": True}, [PASSED])
    assert overridden == ["interpenetration", "floating_part"]
    assert kept == {"interpenetration": False, "floating_part": False, "wrong_object": True}
    gates = [CONTRACT_ERR, _connectivity(True, ("info", "all 4 parts are connected"))]
    res = apply_caps(R, 0.9, gates, {}, [], defects_present={"floating_part": False})
    assert res.overall == 0.75 and [c.rule for c in res.caps_applied] == ["contract_violation"]


def test_graded_acceptance_cap_keeps_the_gradient_but_not_the_pass():
    ten = [AcceptanceItem(id=f"M{i}", text="x", priority="must") for i in range(10)]
    nine_ok = {a.id: True for a in ten} | {"M3": False}
    res = apply_caps(R, 0.99, [], nine_ok, ten)
    assert res.overall == pytest.approx(0.96) and res.caps_applied[0].rule == "missing_must_acceptance"
    assert res.caps_applied[0].evidence.startswith("9 of 10 must items verified; not verified: M3")
    assert apply_caps(R, 0.99, [], {}, ten[:2]).overall == pytest.approx(0.6), "0 of 2 verified is the floor"
    assert apply_caps(R, 0.99, [], {"M0": True}, ten[:2]).overall == pytest.approx(0.8)
    should = [AcceptanceItem(id="S1", text="y", priority="should")]
    assert apply_caps(R, 0.9, [], {"S1": False}, should).overall == 0.9, "a should item never caps"
    assert apply_caps(S, 0.99, [], nine_ok, ten).overall == 0.6, "an ungraded rule keeps its flat cap"
    # pass/fail is the owner's policy: the verdict still fails on must_missing
    sample = JudgeOutput(criteria={c.id: CriterionScore(score=0.99, evidence="") for c in R.criteria},
                         acceptance={k: AcceptanceVerdict(verified=v) for k, v in nine_ok.items()},
                         defects={d.id: False for d in R.defects})
    j = aggregate_samples(R, [sample], gates=[], acceptance_items=ten)
    assert j.overall == pytest.approx(0.96) and not j.passed and "must items unverified: M3" in j.summary
