"""finding_kind() against real gate text, and the shape of the route table (T5).

The golden list in ``data/gate_findings.json`` was mined from 2,113 gate reports under
``bench/out``.  It is the guard against the failure mode this classifier invites: a gate
message gets reworded, ``finding_kind`` quietly returns None, and repair rounds stop
getting the skill that answers them — with nothing failing anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse.skills.registry import ROUTED_SKILLS, ROUTES, Route, finding_kind, finding_kinds

DATA = Path(__file__).parent / "data" / "gate_findings.json"
GOLDEN = json.loads(DATA.read_text())["findings"]


def test_the_golden_list_is_real_and_not_tiny():
    assert len(GOLDEN) >= 30
    assert {r["severity"] for r in GOLDEN} == {"warn", "error"}


@pytest.mark.parametrize("row", GOLDEN, ids=[f"{r['gate']}:{r['kind']}:{i}" for i, r in enumerate(GOLDEN)])
def test_every_recorded_finding_classifies_to_its_expected_kind(row: dict):
    got = finding_kind(row["gate"], row["message"], row["severity"])
    assert got is not None, f"unknown: {row['gate']} / {row['message'][:90]}"
    assert got == row["kind"]


def test_no_recorded_finding_is_unknown():
    unknown = [r for r in GOLDEN if finding_kind(r["gate"], r["message"], r["severity"]) is None]
    assert unknown == []


def test_info_findings_are_census_not_defects():
    # "all 7 parts are connected" must not attach the interpenetration sheet
    assert finding_kind("connectivity", "all 7 parts are connected (9 contacts, gap <= 2 mm)", "info") is None
    assert finding_kind("contract", "all plan parts present and within tolerance", "info") is None
    assert finding_kind("lint:blender", "named objects: ['Seat', 'Leg']", "info") is None


def test_unknown_gates_and_empty_text_are_none_not_a_crash():
    assert finding_kind("", "") is None
    assert finding_kind("brand_new_gate", "something happened", "error") is None
    assert finding_kind("connectivity", "", "error") is None


def test_finding_kinds_accepts_reports_findings_and_strings():
    class F:
        def __init__(self, gate, message, severity="warn"):
            self.gate, self.message, self.severity = gate, message, severity

    class R:
        def __init__(self, findings):
            self.findings = findings

    a = F("connectivity", "'A' and 'B' interpenetrate by 5.0 mm (3% of surface samples inside)")
    b = F("contract", "part 'Leg' bbox deviates from the plan (worst 2.2x tolerance)")
    assert finding_kinds([a, b]) == ["connectivity/interpenetration", "contract/part_bbox"]
    assert finding_kinds([R([a]), R([b])]) == ["connectivity/interpenetration", "contract/part_bbox"]
    assert finding_kinds([a, a]) == ["connectivity/interpenetration"]  # distinct, in order
    assert finding_kinds([]) == [] and finding_kinds(None) == []


# --------------------------------------------------------------------------- the table
def test_rule_ids_are_unique_and_the_design_numbers_are_all_present():
    ids = [r.rule for r in ROUTES]
    assert len(ids) == len(set(ids))
    for n in range(1, 24):
        assert any(r.rule == f"R{n}" for r in ROUTES), f"R{n} is missing from the table"
    assert [r for r in ROUTES if r.rule.startswith("R24")], "R24 (the graphics gate row) is missing"


def test_gate_fired_rows_always_outrank_standing_rows():
    """Law 2: a repair round must spend its budget on what actually broke."""
    fired = [r for r in ROUTES if r.gate_fired]
    standing = [r for r in ROUTES if not r.gate_fired]
    assert min(r.priority for r in fired) > max(r.priority for r in standing)
    assert all(r.priority >= 90 for r in fired)


def test_every_row_carries_a_reason_and_a_known_skill():
    for r in ROUTES:
        assert r.why and len(r.why) > 20, r.rule
        assert r.skill in ROUTED_SKILLS
        assert r.skill.startswith("cv3d-")


def test_route_finding_patterns_are_real_kinds_or_families():
    families = {k.split("/", 1)[0] for k in (finding_kind(g, m, s) or ""
                                             for g, m, s in ((r["gate"], r["message"], r["severity"]) for r in GOLDEN))}
    families |= {"shader"}  # shader_preflight rows exist in the fixture as source-derived
    for r in ROUTES:
        for pat in r.findings:
            assert pat.split("/", 1)[0] in families, f"{r.rule} routes on unknown family {pat!r}"


def test_matches_finding_supports_exact_and_family_patterns():
    row = Route("X", "cv3d-part-contact", 95, findings=("connectivity/*", "joint_sweep/link_overlap"))
    assert row.matches_finding(["connectivity/floating_part"]) == "connectivity/floating_part"
    assert row.matches_finding(["joint_sweep/link_overlap"]) == "joint_sweep/link_overlap"
    assert row.matches_finding(["joint_sweep/disconnected"]) is None
    assert row.matches_finding([]) is None
