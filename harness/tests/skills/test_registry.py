"""Classify the recorded gate-message corpus and validate the routing table."""

from __future__ import annotations

import json
from pathlib import Path

from codeverse3d.skills.registry import ROUTES, finding_kind, finding_kinds

DATA = Path(__file__).parent / "data" / "gate_findings.json"
GOLDEN = json.loads(DATA.read_text())["findings"]


def test_every_recorded_finding_classifies_to_its_expected_kind():
    assert len(GOLDEN) >= 30
    assert {r["severity"] for r in GOLDEN} == {"warn", "error"}
    for index, row in enumerate(GOLDEN):
        got = finding_kind(row["gate"], row["message"], row["severity"])
        label = f"row {index} ({row['gate']} / {row['message'][:90]})"
        assert got is not None, f"unknown {label}"
        assert got == row["kind"], label


def test_info_findings_are_census_not_defects():
    # "all 7 parts are connected" must not attach the interpenetration sheet
    assert finding_kind("connectivity", "all 7 parts are connected (9 contacts, gap <= 2 mm)", "info") is None
    assert finding_kind("contract", "all plan parts present and within tolerance", "info") is None
    assert finding_kind("lint:blender", "named objects: ['Seat', 'Leg']", "info") is None


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


def test_route_finding_patterns_are_real_kinds_or_families():
    families = {k.split("/", 1)[0] for k in (finding_kind(g, m, s) or ""
                                             for g, m, s in ((r["gate"], r["message"], r["severity"]) for r in GOLDEN))}
    families |= {"shader"}  # shader_preflight rows exist in the fixture as source-derived
    for r in ROUTES:
        for pat in r.findings:
            assert pat.split("/", 1)[0] in families, f"{r.rule} routes on unknown family {pat!r}"


