from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.contracts.plan import AcceptanceItem
from codeverse.judges.caps import apply_caps
from codeverse.judges.rubrics import load_rubric

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
