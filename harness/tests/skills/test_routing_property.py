"""Routing invariants over the shipped library's complete input space."""

from __future__ import annotations

import pytest

from codeverse3d.skills import all_skills, bundle_dirs, select, skills_dir
from codeverse3d.skills.registry import ROUTES, plan_signals

pytestmark = pytest.mark.skipif(not bundle_dirs(), reason=f"no bundles in {skills_dir()} yet")

LIBRARY = all_skills()

TRACKS = ("static_object", "articulated_object", "scene", "graphics")
LANGUAGES = ("blender", "cadquery", "threejs", "urdf_blender", "scene_threejs",
             "glsl_shader", "opengl_python")
KINDS = ("baseline", "part", "refine", "rebuild", "repair", "env", "zone",
         "asset", "asset_fix", "reference")
#: every plan signal plan_signals() produces — a route requiring any other can never fire
SIGNAL_KEYS = tuple(plan_signals(None))

LIVE_KINDS = sorted({k for row in ROUTES for k in row.findings if not k.endswith("*")} |
                    {"connectivity/penetration", "connectivity/floating", "connectivity/untyped",
                     "contract/part_bbox", "contract/orientation", "joint_sweep/penetration",
                     "connectivity/stray_islands", "scene_frames/camera_low", "scene_frames/hero_unseen",
                     "gl_frames/static", "shader_preflight/no_fog"})


BASE_INPUTS = [(t, lang, kind) for t in TRACKS for lang in LANGUAGES for kind in KINDS]


def test_every_signal_a_route_requires_is_in_the_input_space():
    """R26/R27 test wants_water / wants_night, which SIGNAL_KEYS once lacked: a route that
    requires a signal plan_signals() never sets is dead."""
    for row in ROUTES:
        assert set(row.requires_all) | set(row.requires_any) <= set(SIGNAL_KEYS), row.rule


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
