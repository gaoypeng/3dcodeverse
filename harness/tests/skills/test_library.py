"""The tests that run over the REAL bundles as they land (T1-T4, T6, T8).

Every test here skips cleanly while the library is empty — the machinery ships before the
bodies do — and starts biting the moment a bundle appears.  That ordering is deliberate:
the Author phase should not be able to land a bundle that contradicts the contract, quotes
a stale constant, or grows into a second cookbook, and it should not have to remember to
add a test for each of those.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

from codeverse.skills import bundle_dirs, iter_skills, skills_dir, validate_bundle
from codeverse.skills.model import BODY_MAX_LINES, BODY_MAX_TOKENS
from codeverse.skills.registry import ROUTED_SKILLS, ROUTES
from codeverse.skills.targets import check_claims, claim_bases, load_claims

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


@pytest.mark.parametrize("d", BUNDLES, ids=[d.name for d in BUNDLES])
def test_bundle_is_spec_conformant(d: Path):
    assert validate_bundle(d) == []


@pytest.mark.parametrize("s", SKILLS, ids=[s.name for s in SKILLS])
def test_body_is_inside_the_budget(s):
    assert s.body_lines <= BODY_MAX_LINES
    assert s.body_tokens <= BODY_MAX_TOKENS


@pytest.mark.parametrize("s", SKILLS, ids=[s.name for s in SKILLS])
def test_a_skill_states_rules_and_does_not_become_a_second_cookbook(s):
    """No code fence longer than 20 lines: code lives in the cookbook, which is one
    library of truth with 3,560 lines already."""
    for fence in re.findall(r"^```.*?^```", s.body, re.S | re.M):
        n = len(fence.splitlines()) - 2
        assert n <= MAX_FENCE_LINES, f"{s.name}: a {n}-line code fence belongs in the cookbook"


@pytest.mark.parametrize("s", SKILLS, ids=[s.name for s in SKILLS])
def test_a_skill_does_not_restate_the_frame_or_unit_contract(s):
    for pat in CONTRACT_RESTATEMENTS:
        assert not re.search(pat, s.body, re.I), f"{s.name}: {pat!r} belongs in conventions.py only"


@pytest.mark.parametrize("s", SKILLS, ids=[s.name for s in SKILLS])
def test_every_pinned_number_still_matches_its_live_constant(s):
    """T2: changing PENETRATION_ERROR_M must break the test that ships the skill quoting it."""
    assert check_claims(s) == []


@pytest.mark.parametrize("s", SKILLS, ids=[s.name for s in SKILLS])
def test_an_evidence_thin_bundle_says_so(s):
    if s.evidence != "measured":
        assert s.metadata.get("evidence_note"), f"{s.name}: a non-measured bundle must say why in metadata"


def test_no_two_skills_anywhere_point_one_claim_key_at_different_numbers():
    """T3: astra3d checked one hand-picked pair by hand; this checks the whole library.

    Compared on the PRE-SCALE value, not the rendered text: "1 cm" and "0.01" metres are
    the same tolerance honestly quoted in two units, and failing that pair would have
    taught the next author to delete the claim rather than fix a contradiction.
    """
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
        out: set[tuple[str, str]] = set()
        for r in ROUTES:
            if r.skill != name:
                continue
            for t in r.tracks or ("*",):
                for lang in r.languages or ("*",):
                    out.add((t, lang))
        return out

    ra, rb = reach(a), reach(b)
    return any((t1 == t2 or "*" in (t1, t2)) and (l1 == l2 or "*" in (l1, l2))
               for t1, l1 in ra for t2, l2 in rb)


def test_every_routed_skill_has_a_bundle_once_the_library_is_complete():
    have = {d.name for d in BUNDLES}
    missing = [n for n in ROUTED_SKILLS if n not in have]
    if missing and len(have) < len(ROUTED_SKILLS):
        pytest.skip(f"library still being written: {len(have)}/{len(ROUTED_SKILLS)} bundles")
    assert missing == []


def test_no_bundle_exists_that_no_route_can_ever_attach():
    orphans = [d.name for d in BUNDLES if d.name not in ROUTED_SKILLS]
    assert orphans == [], f"unroutable bundles pay the index and never help: {orphans}"
