"""Routing invariants over the shipped library's complete input space."""

from __future__ import annotations

import itertools
import random

import pytest

from codeverse3d.skills import all_skills, bundle_dirs, select, skills_dir
from codeverse3d.skills.model import EVIDENCE_INHERITED
from codeverse3d.skills.registry import QUIET_KINDS, ROUTED_SKILLS, ROUTES, SIGNAL_KEYS

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


def test_every_signal_a_route_requires_is_in_the_input_space():
    """R26/R27 test wants_water / wants_night, which SIGNAL_KEYS (and so FLAGS) once lacked:
    the property tests below never exercised them."""
    for row in ROUTES:
        assert set(row.requires_all) | set(row.requires_any) <= set(SIGNAL_KEYS), row.rule


def test_the_cap_and_the_ordering_hold_over_the_whole_input_space():
    """Both settings of allow_unverified: R25–R28 (and the cadquery / threejs forms) name
    inherited-unverified bundles, which route only with it on."""
    expected = 2 * len(LANGUAGES) * len(KINDS) * 2 ** len(FLAGS)
    for track in TRACKS:
        seen = 0
        for unverified, language, kind in itertools.product((False, True), LANGUAGES, KINDS):
            for signals in _signal_sets():
                got = select(track, language, kind, signals=signals, library=LIBRARY, max_skills=5,
                             allow_unverified=unverified)
                seen += 1
                assert len(got) <= 5, f"{track}/{language}/{kind} routed {len(got)}"
                assert len({skill.name for skill in got}) == len(got), "duplicate skill"
                priorities = [skill.priority for skill in got]
                assert priorities == sorted(priorities, reverse=True), (
                    f"{track}/{language}/{kind} is out of order"
                )
                for skill in got:
                    assert skill.rules and skill.reason, f"{skill.name} has no routing reason"
        assert seen == expected, track


def test_routing_is_deterministic_over_the_whole_input_space():
    signals = {"multi_part": True, "has_instances": True, "has_custom_shader": True, "n_parts": 4}
    for track in TRACKS:
        for language, kind in itertools.product(LANGUAGES, KINDS):
            a = [s.name for s in select(track, language, kind, signals=signals, library=LIBRARY)]
            b = [s.name for s in select(track, language, kind, signals=signals, library=LIBRARY)]
            assert a == b, f"{track}/{language}/{kind}"


def test_every_corpus_finding_kind_routes_sanely_from_every_session():
    """A gate finding must never crash the router, never blow the cap, and never
    silently outrank nothing — where a row answers it, it must come back at >= 90."""
    for finding in LIVE_KINDS:
        answered_somewhere = False
        for track, language, kind in BASE_INPUTS:
            got = select(track, language, kind, findings=[finding], library=LIBRARY, max_skills=5)
            assert len(got) <= 5, f"{finding}: {track}/{language}/{kind}"
            fired = [skill for skill in got if skill.gate_fired]
            for skill in fired:
                assert skill.priority >= 90, (
                    f"{skill.name} answered {finding} at priority {skill.priority}"
                )
                assert finding in skill.reason or any(
                    finding.startswith(pattern.rstrip("*"))
                    for route in ROUTES
                    if route.rule in skill.rules
                    for pattern in route.findings
                ), f"{skill.name} cannot explain why it answered {finding}"
            answered_somewhere = answered_somewhere or bool(fired)
        assert answered_somewhere, f"{finding} is classified but no route answers it"


def test_a_quiet_kind_stays_quiet_until_a_gate_fires():
    for track, language in itertools.product(TRACKS, LANGUAGES):
        for kind in QUIET_KINDS:
            sig = {"multi_part": True, "has_instances": True, "has_custom_shader": True}
            assert select(track, language, kind, signals=sig, library=LIBRARY) == []


def test_junk_inputs_degrade_to_empty_and_never_raise():
    for bad in JUNK:
        combos = [(bad, "blender", "baseline"), ("static_object", bad, "baseline"),
                  ("static_object", "blender", bad), (bad, bad, bad)]
        for track, language, kind in combos:
            got = select(track, language, kind, signals={"multi_part": True}, library=LIBRARY)
            assert isinstance(got, list) and len(got) <= 5, (track, language, kind)
            assert all(skill.name in ROUTED_SKILLS for skill in got)


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
        assert not (got & thin), f"{sorted(got & thin)} routed without C3D_SKILLS_UNVERIFIED"


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
