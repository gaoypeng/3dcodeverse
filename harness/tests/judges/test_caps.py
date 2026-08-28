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
