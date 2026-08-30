from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.contracts.plan import AcceptanceItem
from codeverse.judges.rubrics import apply_caps, load_rubric

R = load_rubric("static_object_v1")


def _gate(name, sev, msg, **data):
    return GateReport(gate=name, passed=sev != Severity.ERROR,
                      findings=[GateFinding(gate=name, severity=sev, message=msg, data=data)])


def test_no_caps_when_clean():
    res = apply_caps(R, 0.9, [GateReport(gate="connectivity", passed=True)], {}, [])
    assert res.overall == 0.9 and not res.caps_applied


def test_build_error_caps_to_zero():
    res = apply_caps(R, 0.9, [_gate("build:blender", Severity.ERROR, "Traceback ... NameError")], {}, [])
    assert res.overall == 0.0
    assert res.caps_applied[0].rule == "build_error"


def test_floating_via_data_kind_and_message():
    res = apply_caps(R, 0.9, [_gate("connectivity", Severity.ERROR, "part Leg1 is 3cm above others", kind="floating")], {}, [])
    assert res.overall == 0.6 and res.caps_applied[0].rule == "floating_part"
    res2 = apply_caps(R, 0.9, [_gate("connectivity", Severity.ERROR, "Leg1 has no_contact with any part")], {}, [])
    assert res2.overall == 0.6


def test_warn_does_not_cap():
    res = apply_caps(R, 0.9, [_gate("connectivity", Severity.WARN, "floating sliver")], {}, [])
    assert res.overall == 0.9


def test_penetration_and_multiple_caps_take_min():
    gates = [_gate("connectivity", Severity.ERROR, "penetration Seat/Leg 12mm"),
             _gate("connectivity", Severity.ERROR, "Arm floating", kind="floating")]
    res = apply_caps(R, 0.95, gates, {}, [])
    assert res.overall == 0.6
    assert {c.rule for c in res.caps_applied} == {"floating_part", "penetration_error"}


def test_missing_must_acceptance_caps():
    items = [AcceptanceItem(id="M1", text="x", priority="must"), AcceptanceItem(id="S1", text="y", priority="should")]
    res = apply_caps(R, 0.9, [], {"M1": False, "S1": False}, items)
    assert res.overall == 0.6 and res.caps_applied[0].rule == "missing_must_acceptance"
    res2 = apply_caps(R, 0.9, [], {"M1": True, "S1": False}, items)
    assert res2.overall == 0.9
    res3 = apply_caps(R, 0.9, [], {}, items)  # missing → not verified
    assert res3.overall == 0.6


def test_scene_console_and_shader_caps():
    S = load_rubric("scene_v1")
    res = apply_caps(S, 0.9, [], {}, [], console_errors=["TypeError: x is undefined"])
    assert res.overall == 0.6 and res.caps_applied[0].rule == "console_error"
    res2 = apply_caps(S, 0.9, [_gate("shader_probe", Severity.ERROR, "GLSL compile failed in water.js", kind="shader")], {}, [])
    assert res2.overall == 0.5


# ------------------------------------------- a passed gate vetoes the checklist's claim
def _connectivity(passed: bool, *msgs: tuple[str, str]):
    from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
    sev = {"error": Severity.ERROR, "warn": Severity.WARN, "info": Severity.INFO}
    return GateReport(gate="connectivity", passed=passed,
                      findings=[GateFinding(gate="connectivity", severity=sev[s], target="", message=m) for s, m in msgs])


def test_a_floating_claim_the_connectivity_gate_measured_absent_neither_penalises_nor_caps():
    """Measured 2026-08-26 (fancy_v1 gas_street_lamp, plan-pinned pair): gate said
    'all 9 parts connected, gap <= 2 mm'; the judge marked floating_part present on a dark
    seam and defect:floating_part capped the run at 0.6 (uncapped 0.72) against 0.96 for a
    sibling the eye cannot tell apart.  Gates decide geometry (law 3)."""
    from codeverse.judges.rubrics import load_rubric, measured_absent, veto_measured_defects

    rubric = load_rubric("static_object_v1")
    passed = [_connectivity(True, ("info", "all 9 parts are connected (9 contacts, gap <= 2 mm)"),
                            ("warn", "part 'GasBurner' contains 7 tiny disconnected island(s)"))]
    assert measured_absent(rubric, "floating_part", passed)
    kept, overridden = veto_measured_defects(rubric, {"floating_part": True, "wrong_object": True}, passed)
    assert overridden == ["floating_part"]
    assert kept == {"floating_part": False, "wrong_object": True}, "only what a gate measures can be vetoed"


def test_a_real_floating_part_still_caps():
    from codeverse.judges.rubrics import load_rubric, measured_absent

    rubric = load_rubric("static_object_v1")
    failed = [_connectivity(False, ("error", "part 'Seat' is floating 12 mm above 'Leg'"))]
    assert not measured_absent(rubric, "floating_part", failed)
    assert not measured_absent(rubric, "floating_part", []), "a gate that did not run measured nothing"
    assert not measured_absent(rubric, "wrong_object", [_connectivity(True)]), "no gate measures 'wrong object'"


def test_scene_placement_gate_measures_the_floating_asset_claim():
    """scene_v1 (2026-08-26): the placement gate and the checklist defect share an id, so a passed
    gate vetoes the VLM's floating/sunken claim and an ERROR there caps the round at 0.7."""
    from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
    from codeverse.judges.rubrics import apply_caps, load_rubric, measured_absent

    rubric = load_rubric("scene_v1")
    passed = [GateReport(gate="scene_placement", passed=True, findings=[GateFinding(
        gate="scene_placement", severity=Severity.INFO, target="scene", message="13 assets checked: 0 floating, 0 sunken")])]
    assert measured_absent(rubric, "floating_or_sunken_asset", passed)
    failed = [GateReport(gate="scene_placement", passed=False, findings=[GateFinding(
        gate="scene_placement", severity=Severity.ERROR, target="Pondside/Lantern", message="Pondside/Lantern is floating 0.30 m above PondWater")])]
    assert not measured_absent(rubric, "floating_or_sunken_asset", failed)
    res = apply_caps(rubric, 0.9, failed, {}, [], defects_present={})
    assert res.overall == 0.6, "the older floating_part rule (gate *, cap 0.6) fires too; the tighter cap wins"
    assert any(c.rule == "floating_or_sunken_asset" for c in res.caps_applied)


# ------------------------------------------- 2026-08-30: the veto reaches interpenetration
def test_a_passed_connectivity_gate_vetoes_the_interpenetration_claim():
    """Audited 2026-08-30 over 420 static_object verdicts: 237 interpenetration flags, 110 of
    them citing only WARNs the rubric says to ignore, and this veto had fired for it 0 times —
    the cap rule is ``penetration_error``, the defect ``interpenetration``, and the match was
    on id.  ``CapRule.measures`` now carries the defect id."""
    from codeverse.judges.rubrics import measured_absent, veto_measured_defects

    passed = [_connectivity(True, ("info", "all 9 parts are connected (9 contacts, gap <= 2 mm)"),
                            ("warn", "'Seat' and 'Leg' overlap by 1.4 mm (weld)"))]
    assert measured_absent(R, "interpenetration", passed)
    kept, overridden = veto_measured_defects(R, {"interpenetration": True, "floating_part": True, "wrong_object": True}, passed)
    assert overridden == ["interpenetration", "floating_part"]
    assert kept == {"interpenetration": False, "floating_part": False, "wrong_object": True}
    failed = [_connectivity(False, ("error", "'Leg' and 'Seat' interpenetrate by ≈15 mm (5% of surface samples inside)"))]
    assert not measured_absent(R, "interpenetration", failed), "a measured penetration is not vetoed"
    assert not measured_absent(R, "interpenetration", []), "a gate that did not run measured nothing"


def test_a_failing_contract_gate_does_not_disable_the_connectivity_veto():
    """53 of the 62 blocked cases (2026-08-30): the rules watched gate "*", so a contract ERROR
    about the bbox switched off a veto the connectivity gate had earned."""
    from codeverse.judges.rubrics import measured_absent

    contract = GateReport(gate="contract", passed=False, findings=[GateFinding(
        gate="contract", severity=Severity.ERROR, target="bbox", message="height 0.31 m vs plan 0.45 m (31% off)")])
    gates = [contract, _connectivity(True, ("info", "all 4 parts are connected (4 contacts)"))]
    assert measured_absent(R, "floating_part", gates)
    assert measured_absent(R, "interpenetration", gates)
    res = apply_caps(R, 0.9, gates, {}, [], defects_present={"floating_part": False})
    assert res.overall == 0.75 and [c.rule for c in res.caps_applied] == ["contract_violation"], "the contract cap itself still applies"


def test_graded_acceptance_cap_keeps_the_gradient_but_not_the_pass():
    """The flat 0.6 was the decisive cap on 121 of 424 verdicts (28.6 %, 2026-08-30): one
    unverified must item out of ten scored exactly like ten out of ten."""
    import pytest

    from codeverse.judges.rubrics import (
        AcceptanceVerdict,
        CriterionScore,
        JudgeOutput,
        aggregate_samples,
    )

    ten = [AcceptanceItem(id=f"M{i}", text="x", priority="must") for i in range(10)]
    nine_ok = {a.id: True for a in ten} | {"M3": False}
    res = apply_caps(R, 0.99, [], nine_ok, ten)
    assert res.overall == pytest.approx(0.96) and res.caps_applied[0].rule == "missing_must_acceptance"
    assert res.caps_applied[0].evidence.startswith("9 of 10 must items verified; not verified: M3")
    assert apply_caps(R, 0.99, [], {}, ten[:2]).overall == pytest.approx(0.6), "0 of 2 verified is the floor"
    assert apply_caps(R, 0.99, [], {"M0": True}, ten[:2]).overall == pytest.approx(0.8)
    assert apply_caps(load_rubric("scene_v1"), 0.99, [], nine_ok, ten).overall == 0.6, "an ungraded rule keeps its flat cap"
    # pass/fail is the owner's policy and unchanged: the verdict still fails on must_missing
    sample = JudgeOutput(criteria={c.id: CriterionScore(score=0.99, evidence="") for c in R.criteria},
                         acceptance={k: AcceptanceVerdict(verified=v) for k, v in nine_ok.items()},
                         defects={d.id: False for d in R.defects})
    j = aggregate_samples(R, [sample], gates=[], acceptance_items=ten)
    assert j.overall == pytest.approx(0.96) and not j.passed and "must items unverified: M3" in j.summary


def test_a_defect_vote_tie_is_absent_and_an_acceptance_tie_still_follows_the_representative():
    """Burden of proof on the defect (2026-08-30): the rubric marks an item present only when an
    image or a gate finding shows it, so a split vote has not shown it.  Acceptance ties keep the
    owner's policy (representative sample).  419/420 corpus verdicts are n=1: nothing moved."""
    import json

    import pytest

    from codeverse.judges.rubrics import (
        AcceptanceVerdict,
        CriterionScore,
        JudgeOutput,
        aggregate_samples,
    )

    def sample(defect_on: bool, acc_ok: bool) -> JudgeOutput:
        return JudgeOutput(criteria={c.id: CriterionScore(score=0.9, evidence="") for c in R.criteria},
                           acceptance={"M1": AcceptanceVerdict(verified=acc_ok)},
                           defects={d.id: (d.id == "render_artifacts" and defect_on) for d in R.defects})

    item = [AcceptanceItem(id="M1", text="x", priority="must")]
    j = aggregate_samples(R, [sample(True, True), sample(False, True)], gates=[], acceptance_items=item)
    raw = json.loads(j.raw)
    assert raw["defect_votes"]["render_artifacts"] == [True, False] and raw["defects"]["render_artifacts"] is False
    assert raw["tie_broken"] == ["render_artifacts"] and j.overall == pytest.approx(0.9)
    j2 = aggregate_samples(R, [sample(False, True), sample(False, False)], gates=[], acceptance_items=item)
    assert j2.acceptance_results["M1"] is True and json.loads(j2.raw)["tie_broken"] == ["M1"]
    assert raw["scoring_version"] == 2 and raw["overridden"] == []
