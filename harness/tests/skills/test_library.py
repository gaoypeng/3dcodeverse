"""Corpus invariants for the shipped skill bundles and their numeric claims."""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

import pytest

from codeverse.addons.skill_targets import check_claims, claim_bases, load_claims
from codeverse.skills import bundle_dirs, iter_skills, skills_dir, validate_bundle
from codeverse.skills.registry import ROUTES

BUNDLES = bundle_dirs()
SKILLS = list(iter_skills()) if BUNDLES else []
pytestmark = pytest.mark.skipif(not BUNDLES, reason=f"no bundles in {skills_dir()} yet (the Author phase writes them)")

MAX_FENCE_LINES = 20
#: law 2 of the contract: `conventions.py` is the only place a frame/unit rule is stated.
#: A skill that restates one gives the agent two truths to choose between.
CONTRACT_RESTATEMENTS = (
    r"\bZ is UP\b", r"\bY is UP\b", r"-Y is the FRONT", r"\+Z is the FRONT",
    r"units are meters", r"\bY-up\b.*\bframe\b",
)
MEASURED_PERCENTAGE = re.compile(
    r"\d{1,3}(?:\.\d)?\s?%[^.\n]{0,40}\b(?:of runs|runs|corpus|battery|of the corpus)\b"
    r"|\b(?:runs|corpus|battery|fired|deviates)\b[^.\n]{0,40}\d{1,3}(?:\.\d)?\s?%"
)
PROVENANCE = re.compile(
    r"bench/out|battery|batteries|\bn\s?=\s?\d+|20\d\d-\d\d-\d\d|\bof\s+\d+\s+runs\b"
    r"|\b\d+\s+(?:graded|run|runs|records?)\b"
)


def test_every_bundle_is_valid_and_its_claims_are_current():
    """One corpus pass keeps structure, brevity, evidence and pinned values honest."""
    for bundle in BUNDLES:
        errors = validate_bundle(bundle)
        assert not errors, f"{bundle.name}: {errors}"

    for skill in SKILLS:
        for fence in re.findall(r"^```.*?^```", skill.body, re.S | re.M):
            lines = len(fence.splitlines()) - 2
            assert lines <= MAX_FENCE_LINES, (
                f"{skill.name}: a {lines}-line code fence belongs in the cookbook"
            )
        for pattern in CONTRACT_RESTATEMENTS:
            assert not re.search(pattern, skill.body, re.I), (
                f"{skill.name}: {pattern!r} belongs in conventions.py only"
            )
        errors = check_claims(skill)
        assert not errors, f"{skill.name}: {errors}"
        if skill.evidence != "measured":
            assert skill.metadata.get("evidence_note"), (
                f"{skill.name}: non-measured evidence needs a note"
            )
        verified = dt.date.fromisoformat(str(skill.metadata.get("verified", "")))
        assert verified <= dt.date.today(), f"{skill.name}: verified {verified} is in the future"
        for match in MEASURED_PERCENTAGE.finditer(skill.body):
            window = skill.body[max(0, match.start() - 300): match.end() + 300]
            assert PROVENANCE.search(window), (
                f"{skill.name} gives {match.group(0)!r} without nearby provenance"
            )


def test_no_two_skills_anywhere_point_one_claim_key_at_different_numbers():
    """Compare pre-scale values so equivalent units do not look contradictory."""
    by_key: dict[str, dict[str, tuple[str, object]]] = defaultdict(dict)
    for s in SKILLS:
        for key, base in claim_bases(s.name).items():
            by_key[key][s.name] = base
    for key, owners in sorted(by_key.items()):
        targets = {t for t, _ in owners.values()}
        values = {repr(v) for _, v in owners.values()}
        assert len(targets) == 1, f"{key} is pinned to {sorted(targets)} by {sorted(owners)}"
        assert len(values) == 1, f"{key} resolves to {sorted(values)} across {sorted(owners)}"


def test_co_routing_skills_render_a_shared_number_the_same_way():
    """A weaker but still useful rule for skills that can land in ONE session together:
    if they chose the same units for a shared key, the sentence must read the same."""
    rows = {s.name: {r["key"]: r for r in load_claims(s.name) if r.get("key")} for s in SKILLS}
    for a in sorted(rows):
        for b in sorted(rows):
            if a >= b or not _can_co_route(a, b):
                continue
            for key in sorted(set(rows[a]) & set(rows[b])):
                ra, rb = rows[a][key], rows[b][key]
                if (ra.get("scale"), ra.get("format")) != (rb.get("scale"), rb.get("format")):
                    continue
                assert ra.get("text") == rb.get("text"), f"{a} and {b} disagree about {key}"


def _can_co_route(a: str, b: str) -> bool:
    """Two skills can co-route when some (track, language) can name both."""
    def reach(name: str) -> set[tuple[str, str]]:
        return {
            (track, language)
            for route in ROUTES
            if route.skill == name
            for track in route.tracks or ("*",)
            for language in route.languages or ("*",)
        }

    ra, rb = reach(a), reach(b)
    return any(
        (t1 == t2 or "*" in (t1, t2)) and (l1 == l2 or "*" in (l1, l2))
        for t1, l1 in ra
        for t2, l2 in rb
    )
