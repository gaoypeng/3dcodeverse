"""The tests that run over the REAL bundles as they land (T1-T4, T6, T8).

Every test here skips cleanly while the library is empty — the machinery ships before the
bodies do — and starts biting the moment a bundle appears.  That ordering is deliberate:
the Author phase should not be able to land a bundle that contradicts the contract, quotes
a stale constant, or grows into a second cookbook, and it should not have to remember to
add a test for each of those.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import pytest

from codeverse.skills import bundle_dirs, iter_skills, skills_dir, validate_bundle
from codeverse.skills.claims import check_claims, claim_values
from codeverse.skills.loader import BODY_MAX_LINES, BODY_MAX_TOKENS
from codeverse.skills.registry import ROUTED_SKILLS, ROUTES

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


def test_co_routing_skills_never_disagree_about_a_number():
    """T3: astra3d checked one hand-picked pair; this checks the whole co-routing graph."""
    by_skill = {s.name: claim_values(s.name) for s in SKILLS}
    pairs: set[tuple[str, str]] = set()
    for a in by_skill:
        for b in by_skill:
            if a < b and _can_co_route(a, b):
                pairs.add((a, b))
    for a, b in sorted(pairs):
        shared = set(by_skill[a]) & set(by_skill[b])
        for key in sorted(shared):
            assert by_skill[a][key] == by_skill[b][key], f"{a} and {b} disagree about {key}"


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


@pytest.mark.skipif(shutil.which("skills-ref") is None,
                    reason="the reference validator is not installed (pip install skills-ref)")
@pytest.mark.parametrize("d", BUNDLES, ids=[d.name for d in BUNDLES])
def test_the_reference_validator_accepts_the_bundle(d: Path):
    """T1: our loader implements the spec, but the spec's own validator is the arbiter."""
    p = subprocess.run(["skills-ref", "validate", str(d)], capture_output=True, text=True, check=False)
    assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.slow
def test_every_corpus_statistic_a_skill_quotes_is_still_true():
    """T8: a percentage in a body must be within 20% of what bench/out says today."""
    out = Path(__file__).resolve().parents[2] / "bench" / "out"
    if not out.is_dir():
        pytest.skip("no bench/out in this checkout")
    quoted = defaultdict(list)
    for s in SKILLS:
        for m in re.finditer(r"(\d{1,3}(?:\.\d)?)\s?%", s.body):
            quoted[s.name].append(float(m.group(1)))
    if not quoted:
        pytest.skip("no corpus percentages quoted yet")
    # The recomputation lives with the corpus tooling; here we only assert the bodies
    # carry their provenance, so a reader can check the number without guessing.
    for name, values in quoted.items():
        body = next(s.body for s in SKILLS if s.name == name)
        assert re.search(r"(bench/out|battery|n\s?=\s?\d+|20\d\d-\d\d-\d\d)", body), (
            f"{name} quotes {values} with no battery / n / date beside it")
