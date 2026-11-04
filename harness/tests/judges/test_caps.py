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
)

R = load_rubric("static_object")
A = load_rubric("articulated")
S = load_rubric("scene")
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
    (R, [_gate("connectivity", W, "floating sliver")], {}, 0.9, set()),
    (R, [_gate("connectivity", E, "'Leg' and 'Seat' interpenetrate by ≈15 mm", target="Leg")], {}, 0.7, {"penetration_error"}),
    # N50: a typed finding caps by its kind — neither the part's name ("island") nor its message fires floating_part
    (A, [_gate("joint_sweep", E, "link 'DrawerKnob' touches nothing connected to the root at pose rest "
                                 "(nearest 'Carcass' at 56.4 mm)", target="DrawerKnob")],
     {"views": POSE}, 0.6, {"floating_part"}),
    (S, [], {"console_errors": ["TypeError: x is undefined"]}, 0.6, {"console_error"}),
    (S, [_gate("shader_probe", E, "GLSL compile failed in water.js", kind="shader")], {}, 0.5, {"shader_error"}),
    # scene: ONE cap per placement kind, by the finding's typed kind (the v1 gate-'*' floating_part
    (S, [_gate("scene_placement", E, "Pondside/Lantern is floating 0.30 m above PondWater", target="Pondside/Lantern",
               kind="floating", gap_m=0.30)], {"defects_present": {}}, 0.6, {"floating_asset"}),
    (S, [_gate("scene_placement", E, "Quay/Crate is sunken 0.25 m into Ground", target="Quay/Crate", kind="sunken", sunk_m=0.25)],
     {}, 0.65, {"sunken_asset"}),
    (S, [_gate("scene_placement", E, "interpenetration: Quay/Bench and Quay/Wall overlap (41% of the smaller box)",
               target="Quay/Bench", kind="interpenetration", overlap=0.41)], {}, 0.7, {"interpenetration"}),
    (S, [_gate("scene_placement", E, "Pondside/Lantern is floating 0.30 m above PondWater", target="Pondside/Lantern")],
     {}, 0.6, {"floating_asset"}),
    # the camera rule names the kinds scene_frames really emits; a blocked TARGET is not a buried camera
    (S, [_gate("scene_frames", E, "camera inside geometry", target="Establishing", kind="camera_in_geometry")], {}, 0.5,
     {"camera_in_geometry"}),
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
    (R, "wrong_object", [_connectivity(True)], False),  # no gate measures 'wrong object'
    # a contract ERROR about the bbox does not switch off the veto connectivity earned
    (R, "floating_part", [CONTRACT_ERR, _connectivity(True, ("info", "all 4 parts are connected"))], True),
    # a failed report that names no ERROR finding cannot say what failed: not clean
    (R, "floating_part", [GateReport(gate="connectivity", passed=False, findings=[])], False),

])
def test_measured_absent(rubric, defect, gates, absent):
    """Gates decide geometry (law 3): a passed gate vetoes the VLM's claim of what it measured."""
    assert measured_absent(rubric, defect, gates) is absent


def _placement(passed: bool, *findings: tuple[Severity, str, str, dict]) -> GateReport:
    """A scene_placement report: the gate's summary INFO + typed findings (sev, kind, target, data)."""
    rows = [GateFinding(gate="scene_placement", severity=Severity.INFO, target="scene", message="13 assets checked",
                        data={"kind": "summary", "counts": {"floating": 0, "sunken": 0, "unsupported": 0, "interpenetration": 0}})]
    rows += [GateFinding(gate="scene_placement", severity=sev, target=target, message=f"{target} {kind}", data={"kind": kind, **data})
             for sev, kind, target, data in findings]
    return GateReport(gate="scene_placement", passed=passed, findings=rows)


@pytest.mark.parametrize(("defect", "gates", "absent"), [
    # a passed gate measured nothing of the kind: the claim is vetoed
    ("floating_asset", [_placement(True)], True),
    # a WARN-band hover with gap_m 0.08 keeps the VLM tick: the veto line is the 0.02 m INDOOR tolerance
    # (Placement.floating_indoor_m = contact_m), so every floating / unsupported WARN the gate raises reaches it
    ("floating_asset", [_placement(True, (W, "floating", "Quay/Lantern", {"gap_m": 0.08}))], False),
    # indoor floating WARN — the line was the OUTDOOR 0.05 until 2026-10-15 and vetoed both although the gate
    # had flagged them (review findings 4 / 17)
    # under the line the gate's verdict stands (it raises no such WARN: a gap within contact_m is seated)
    ("floating_asset", [_placement(True, (W, "floating", "Dining/Vase", {"gap_m": 0.01}))], True),
    # a no_fog ERROR in the same report does NOT cancel the floating veto (scoring version 4: scoped to the kind)
    ("floating_asset", [_placement(False, (E, "no_fog", "env", {}))], True),
    # ...a floating ERROR does, and so does an ERROR of the kind alone among WARNs of others
    ("floating_asset", [_placement(False, (E, "floating", "Quay/Lantern", {"gap_m": 0.2}))], False),
    # the kinds are scoped: a sunken ERROR vetoes nothing about floating, a floating ERROR nothing about sunken
    ("floating_asset", [_placement(False, (E, "sunken", "Quay/Post", {"sunk_m": 0.4}))], True),
    # WARN magnitudes against each rule's own line: sunk 0.10 m, overlap 0.20, any scale factor
    ("wrong_scale", [_placement(True, (W, "scale", "Quay/Door", {"factor": 0.35}))], False),
    # the gate did not run
    ("floating_asset", [GateReport(gate="scene_frames", passed=True)], False),

])
def test_scene_veto_is_scoped_to_the_rules_kinds(defect, gates, absent):
    assert measured_absent(S, defect, gates) is absent


def test_graded_acceptance_cap_keeps_the_gradient_but_not_the_pass():
    ten = [AcceptanceItem(id=f"M{i}", text="x", priority="must") for i in range(10)]
    nine_ok = {a.id: True for a in ten} | {"M3": False}
    res = apply_caps(R, 0.99, [], nine_ok, ten)
    assert res.overall == pytest.approx(0.96) and res.caps_applied[0].rule == "missing_must_acceptance"
    assert res.caps_applied[0].evidence.startswith("9 of 10 must items verified; not verified: M3")
    assert apply_caps(R, 0.99, [], {"M0": True}, ten[:2]).overall == pytest.approx(0.8)
    should = [AcceptanceItem(id="S1", text="y", priority="should")]
    assert apply_caps(R, 0.9, [], {"S1": False}, should).overall == 0.9, "a should item never caps"
    assert apply_caps(S, 0.99, [], nine_ok, ten).overall == pytest.approx(0.96), "scene grades it like the object rubrics"
    # pass/fail is the owner's policy: the verdict still fails on must_missing
    sample = JudgeOutput(criteria={c.id: CriterionScore(score=0.99, evidence="") for c in R.criteria},
                         acceptance={k: AcceptanceVerdict(verified=v) for k, v in nine_ok.items()},
                         defects={d.id: False for d in R.defects})
    j = aggregate_samples(R, [sample], gates=[], acceptance_items=ten)
    assert j.overall == pytest.approx(0.96) and not j.passed and "must items unverified: M3" in j.summary
