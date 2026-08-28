"""What the library costs, measured against the SHIPPED bundles and capped.

WHY a file of its own, and why it uses the real library: ``test_materialize_prompting``
prices a synthetic fixture whose descriptions are one line long.  The real ones use the
spec's 1024-char allowance, because a description is the only text a CLI matches on —
and quoting all fourteen of them cost 780 tokens for a five-skill session, against a
300-token design budget, until the index started quoting the first clause instead.
A cost regression is invisible in a fixture and expensive in production, so the numbers
below are re-measured over every session the router can produce.

docs/COST.md §4: 39.3% of the agent bill is spent from turn 20 onwards and 19 of 33 long
sessions ended `budget`.  Everything here is per turn, not per session.
"""

from __future__ import annotations

import itertools

import pytest

from codeverse.skills import all_skills, bundle_dirs, select, skills_dir
from codeverse.skills.loader import BODY_MAX_TOKENS
from codeverse.skills.prompting import NATIVE_LOADERS, index_block, index_tokens

pytestmark = pytest.mark.skipif(not bundle_dirs(), reason=f"no bundles in {skills_dir()} yet")

LIBRARY = all_skills()

#: the routed index (an unclassified, loaderless backend) is the only index we write
API_INDEX_TOKENS_MAX = 400
API_INDEX_BYTES_MAX = 2048
#: a native loader gets one sentence — its own loader writes the real index
NATIVE_INDEX_TOKENS_MAX = 60

TRACKS = ("static_object", "articulated_object", "scene", "graphics")
LANGUAGES = ("blender", "cadquery", "threejs", "urdf_blender", "scene_threejs",
             "glsl_shader", "opengl_python")
KINDS = ("baseline", "part", "detail", "refine", "rebuild", "repair", "env", "zone", "compose")
#: one of everything, so the cap is always the binding constraint
LOUD_SIGNALS = {"multi_part": True, "has_instances": True, "has_symmetry": True,
                "has_joints": True, "has_custom_shader": True, "has_assemblies": True, "n_parts": 9}
ALL_FINDINGS = ["connectivity/interpenetration", "contract/part_bbox", "contract/instance_bbox",
                "joint_sweep/link_overlap", "motion_direction/wrong_axis", "scene_frames/dark_or_flat",
                "gl_frames/motion_or_detail", "lint/part_not_imported", "shader/compile_or_binding",
                "scene_frames/camera_placement"]


def _every_session():
    for track, language, kind in itertools.product(TRACKS, LANGUAGES, KINDS):
        for findings in ((), ALL_FINDINGS):
            for unverified in (False, True):
                yield track, language, kind, [
                    s.skill for s in select(track, language, kind, signals=LOUD_SIGNALS,
                                            findings=findings, library=LIBRARY, max_skills=5,
                                            allow_unverified=unverified)]


def test_the_api_agent_index_stays_inside_its_budget_with_the_whole_library_installed():
    worst = max(_every_session(), key=lambda row: index_tokens(row[3], "api-agent"))
    tokens = index_tokens(worst[3], "api-agent")
    assert tokens <= API_INDEX_TOKENS_MAX, f"{worst[:3]} costs {tokens} tokens of message 0"
    assert len(index_block(worst[3], "unknown-backend").encode()) <= API_INDEX_BYTES_MAX


@pytest.mark.parametrize("kind", NATIVE_LOADERS)
def test_a_native_loader_is_never_handed_a_second_index(kind: str):
    for track, language, round_kind, skills in _every_session():
        text = index_block(skills, kind)
        assert index_tokens(skills, kind) <= NATIVE_INDEX_TOKENS_MAX
        for s in skills:
            assert s.name not in text, f"{kind} would index {s.name} twice ({track}/{language}/{round_kind})"


def test_no_body_can_blow_a_session_on_its_own():
    for name, skill in sorted(LIBRARY.items()):
        assert skill.body_tokens <= BODY_MAX_TOKENS, f"{name}: {skill.body_tokens} tokens"


def test_reading_the_whole_routed_set_is_priced_and_bounded():
    """The worst thing an agent can do is read all five. It must still be affordable."""
    worst = max(_every_session(), key=lambda row: sum(s.body_tokens for s in row[3]))
    total = sum(s.body_tokens for s in worst[3])
    assert total <= 5 * BODY_MAX_TOKENS
    # ~$0.0021/1k in, gemini-3.7-flash: five bodies is cents, not dollars
    assert total * 2.1e-6 < 0.03, f"{worst[:3]} would cost ${total * 2.1e-6:.4f} to read fully"
