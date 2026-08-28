"""Routing over the WHOLE input space, against the REAL library — invariants, not examples.

``test_router`` checks the rows one at a time with a synthetic library.  This file runs
the router over the full cross product of the inputs a run can actually present — every
track x language x round kind x plan-signal combination, and every gate finding kind the
corpus produces — and asserts the four properties that must hold for all of them:

  determinism   the same input always gives the same ordered list
  the cap       no input ever exceeds ``max_skills``
  totality      a junk input degrades to an empty list, never an exception
  relevance     a gate-fired sheet outranks a standing one, and the routed set is
                explainable — every selection names the row that put it there

The library is the shipped one, not a fixture: the property that matters is that THESE
fourteen bundles route sanely, and a bundle whose evidence is inherited-unverified must
stay out unless the switch says otherwise.
"""

from __future__ import annotations

import itertools
import random

import pytest

from codeverse.skills import all_skills, bundle_dirs, select, skills_dir
from codeverse.skills.model import EVIDENCE_INHERITED
from codeverse.skills.registry import QUIET_KINDS, ROUTED_SKILLS, ROUTES, SIGNAL_KEYS

pytestmark = pytest.mark.skipif(not bundle_dirs(), reason=f"no bundles in {skills_dir()} yet")

LIBRARY = all_skills()

TRACKS = ("static_object", "articulated_object", "scene", "graphics")
LANGUAGES = ("blender", "cadquery", "threejs", "urdf_blender", "scene_threejs",
             "glsl_shader", "opengl_python")
KINDS = ("baseline", "part", "detail", "refine", "rebuild", "repair", "env", "zone",
         "compose", "asset", "asset_fix", "reference")
#: the boolean plan signals the table may test (n_parts / joint_types are derived)
FLAGS = tuple(k for k in SIGNAL_KEYS if k not in ("n_parts", "joint_types"))
JUNK = ("", "  ", "no_such_track", "STATIC_OBJECT", "static object", "1", "../etc")

LIVE_KINDS = sorted({k for row in ROUTES for k in row.findings if not k.endswith("*")} |
                    {"connectivity/interpenetration", "connectivity/floating_part",
                     "connectivity/stray_islands", "contract/part_bbox", "contract/overall_bbox",
                     "joint_sweep/link_overlap", "motion_direction/wrong_axis",
                     "scene_frames/dark_or_flat", "gl_frames/motion_or_detail",
                     "lint/part_not_imported", "shader/compile_or_binding"})


def _signal_sets():
    """Every boolean combination, each with the n_parts it implies."""
    for bits in itertools.product((False, True), repeat=len(FLAGS)):
        sig = dict(zip(FLAGS, bits, strict=True))
        sig["n_parts"] = 6 if sig["multi_part"] else 1
        sig["joint_types"] = ["revolute"] if sig["has_joints"] else []
        yield sig


BASE_INPUTS = [(t, lang, kind) for t in TRACKS for lang in LANGUAGES for kind in KINDS]


@pytest.mark.parametrize("track", TRACKS)
def test_the_cap_and_the_ordering_hold_over_the_whole_input_space(track: str):
    seen = 0
    for language, kind in itertools.product(LANGUAGES, KINDS):
        for sig in _signal_sets():
            got = select(track, language, kind, signals=sig, library=LIBRARY, max_skills=5)
            seen += 1
            assert len(got) <= 5, f"{track}/{language}/{kind} routed {len(got)}"
            assert len({s.name for s in got}) == len(got), "a skill was attached twice"
            prios = [s.priority for s in got]
            assert prios == sorted(prios, reverse=True), f"{track}/{language}/{kind} is out of order"
            for s in got:
                assert s.rules and s.reason, f"{s.name} was attached with no rule to point at"
    assert seen == len(LANGUAGES) * len(KINDS) * 2 ** len(FLAGS)


@pytest.mark.parametrize("track", TRACKS)
def test_routing_is_deterministic_over_the_whole_input_space(track: str):
    for language, kind in itertools.product(LANGUAGES, KINDS):
        sig = {"multi_part": True, "has_instances": True, "has_custom_shader": True, "n_parts": 4}
        a = [s.name for s in select(track, language, kind, signals=sig, library=LIBRARY)]
        b = [s.name for s in select(track, language, kind, signals=sig, library=LIBRARY)]
        assert a == b


@pytest.mark.parametrize("finding", LIVE_KINDS)
def test_every_corpus_finding_kind_routes_sanely_from_every_session(finding: str):
    """A gate finding must never crash the router, never blow the cap, and never
    silently outrank nothing — where a row answers it, it must come back at >= 90."""
    answered_somewhere = False
    for track, language, kind in BASE_INPUTS:
        got = select(track, language, kind, findings=[finding], library=LIBRARY, max_skills=5)
        assert len(got) <= 5
        fired = [s for s in got if s.gate_fired]
        for s in fired:
            assert s.priority >= 90, f"{s.name} answered {finding} at priority {s.priority}"
            assert finding in s.reason or any(finding.startswith(p.rstrip("*"))
                                              for r in ROUTES if r.rule in s.rules for p in r.findings)
        answered_somewhere = answered_somewhere or bool(fired)
    assert answered_somewhere, f"{finding} is classified but no row anywhere answers it"


def test_a_quiet_kind_stays_quiet_until_a_gate_fires():
    for track, language in itertools.product(TRACKS, LANGUAGES):
        for kind in QUIET_KINDS:
            sig = {"multi_part": True, "has_instances": True, "has_custom_shader": True}
            assert select(track, language, kind, signals=sig, library=LIBRARY) == []


@pytest.mark.parametrize("bad", JUNK)
def test_junk_inputs_degrade_to_empty_and_never_raise(bad: str):
    combos = [(bad, "blender", "baseline"), ("static_object", bad, "baseline"),
              ("static_object", "blender", bad), (bad, bad, bad)]
    for track, language, kind in combos:
        got = select(track, language, kind, signals={"multi_part": True}, library=LIBRARY)
        assert isinstance(got, list) and len(got) <= 5
        for s in got:
            # a wildcard row may legitimately fire on a junk track; it may never invent a skill
            assert s.name in ROUTED_SKILLS


def test_none_and_broken_inputs_are_survivable():
    for signals in (None, {}, {"multi_part": None}, {"nonsense": object()}):
        assert isinstance(select("static_object", "blender", "baseline",
                                 signals=signals, library=LIBRARY), list)
    for findings in ((), None, [], ["not/a/real/kind"], ["", None]):
        assert isinstance(select("static_object", "blender", "repair",
                                 findings=findings, library=LIBRARY), list)
    assert select("static_object", "blender", "baseline", library={}) == []


def test_every_track_language_pair_a_run_can_present_routes_something():
    """A pair the harness actually drives must not come back empty at baseline, or the
    library has a hole the read-rate metric would report as apathy."""
    real = [("static_object", "blender"), ("static_object", "cadquery"), ("static_object", "threejs"),
            ("articulated_object", "urdf_blender"), ("scene", "scene_threejs"),
            ("graphics", "glsl_shader"), ("graphics", "opengl_python")]
    sig = {"multi_part": True, "n_parts": 5}
    for track, language in real:
        got = select(track, language, "baseline", signals=sig, library=LIBRARY,
                     allow_unverified=True)
        assert got, f"{track}/{language} routes nothing at baseline"


def test_an_unverified_bundle_is_off_by_default_everywhere():
    thin = {n for n, s in LIBRARY.items() if s.evidence == EVIDENCE_INHERITED}
    if not thin:
        pytest.skip("every shipped bundle is measured or mixed")
    for track, language, kind in BASE_INPUTS:
        got = {s.name for s in select(track, language, kind, signals={"multi_part": True},
                                      library=LIBRARY, findings=LIVE_KINDS)}
        assert not (got & thin), f"{sorted(got & thin)} routed without CV3D_SKILLS_UNVERIFIED"


def test_a_random_walk_of_mixed_findings_never_breaks_an_invariant():
    rng = random.Random(20260825)
    for _ in range(2000):
        track = rng.choice(TRACKS + JUNK)
        language = rng.choice(LANGUAGES + JUNK)
        kind = rng.choice(KINDS + JUNK)
        sig = {k: rng.random() < 0.5 for k in FLAGS}
        sig["n_parts"] = rng.randint(0, 30)
        findings = rng.sample(LIVE_KINDS, rng.randint(0, 4))
        cap = rng.randint(0, 7)
        got = select(track, language, kind, signals=sig, findings=findings,
                     library=LIBRARY, max_skills=cap)
        assert len(got) <= cap
        assert [s.priority for s in got] == sorted((s.priority for s in got), reverse=True)
