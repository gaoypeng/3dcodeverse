"""Structural validation of the v2 prompt batteries (offline, no runs).

The v2 batteries drop the saturated easy tier and encode their difficulty
contracts directly in ``must_have``.  These tests pin those contracts:

* every file parses through ``bench.run_bench.Battery.load`` (the real loader);
* every prompt has a tier, a category, a non-empty ``must_have`` and a
  snake_case id containing its tier token;
* static_objects_v2 / compare_v2: 8-16 must_have parts per prompt, at least
  half carrying a measurable number, and most prompts with ``dimensions_m``;
* articulated_v2: every prompt names >= 3 quantified motions for the
  pose-sweep judge;
* scenes_v2: every scene states a luminance band and a camera brief;
* compare_v2 prompts are drawn verbatim from static_objects_v2.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench.run_bench import Battery  # noqa: E402

PROMPTS = REPO / "bench" / "prompts"

V2_FILES = {
    "static_objects_v2": PROMPTS / "static_objects_v2.yaml",
    "articulated_v2": PROMPTS / "articulated_v2.yaml",
    "scenes_v2": PROMPTS / "scenes_v2.yaml",
    "graphics_v2": PROMPTS / "graphics_v2.yaml",
    "compare_v2": PROMPTS / "compare_v2.yaml",
}

EXPECTED_COUNTS = {
    "static_objects_v2": 20,
    "articulated_v2": 14,
    "scenes_v2": 10,
    "graphics_v2": 10,
    "compare_v2": 8,
}

ID_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)+$")
NUMBER_RE = re.compile(r"\d")
MOTION_RE = re.compile(
    r"\b(slid(?:e|es|ing)|swing(?:s|ing)?|rotat(?:e|es|ing)|swivel(?:s)?|"
    r"pivot(?:s)?|open(?:s|ed|ing)?|fold(?:s|ed|ing)?|travel(?:s)?|"
    r"advanc(?:e|es)|rais(?:e|es)|lower(?:s)?|tilt(?:s)?|extend(?:s)?|"
    r"retract(?:s)?|splay(?:s|ing)?|pan|lift(?:s)?|drop(?:s)?|hinge(?:d)?|"
    r"revolute|prismatic|continuous(?:ly)?)\b"
)


@pytest.fixture(scope="module", params=sorted(V2_FILES), ids=sorted(V2_FILES))
def battery(request) -> Battery:
    b = Battery.load(V2_FILES[request.param])
    assert b.name == request.param
    return b


def test_all_v2_files_exist():
    missing = [str(p) for p in V2_FILES.values() if not p.is_file()]
    assert not missing


def test_battery_schema(battery: Battery):
    assert len(battery.prompts) == EXPECTED_COUNTS[battery.name]
    ids = [p.id for p in battery.prompts]
    assert len(ids) == len(set(ids)), "duplicate prompt ids"
    for p in battery.prompts:
        assert p.tier in {"medium", "hard"}, f"{p.id}: v2 has no easy tier"
        assert p.category, f"{p.id}: category required"
        assert p.must_have, f"{p.id}: must_have required"
        assert ID_RE.match(p.id), f"{p.id}: not snake_case"
        tier_token = {"medium": "med"}.get(p.tier, p.tier)
        assert f"_{tier_token}_" in p.id, f"{p.id}: id must contain its tier token"
        for item in p.must_have:
            assert item.strip(), f"{p.id}: empty must_have item"
        for banned in ("ornate", "detailed"):
            assert banned not in p.prompt.lower(), f"{p.id}: vague word '{banned}'"


def _load(name: str) -> Battery:
    return Battery.load(V2_FILES[name])


@pytest.mark.parametrize("name", ["static_objects_v2", "compare_v2"])
def test_static_prompts_have_dense_dimensioned_checklists(name: str):
    b = _load(name)
    with_dims = 0
    for p in b.prompts:
        n = len(p.must_have)
        assert 8 <= n <= 16, f"{p.id}: {n} must_have items, want 8-16 nameable parts"
        numbered = sum(1 for item in p.must_have if NUMBER_RE.search(item))
        assert numbered * 2 >= n, f"{p.id}: under half of must_have items carry a number"
        if p.dimensions_m:
            with_dims += 1
    assert with_dims * 2 >= len(b.prompts), f"{name}: under half the prompts set dimensions_m"


def test_static_v2_has_controls_and_hard_axes():
    b = _load("static_objects_v2")
    controls = [p for p in b.prompts if "control" in p.tags]
    assert len(controls) == 4 and all(p.tier == "medium" for p in controls)
    hard = [p for p in b.prompts if p.tier == "hard"]
    assert len(hard) == 16
    axes = {t for p in b.prompts for t in p.tags if t.startswith("axis:")}
    assert {"axis:decomposition", "axis:thin_features", "axis:curved_organic",
            "axis:arch_detail", "axis:multi_material"} <= axes


def test_articulated_prompts_name_quantified_motions():
    b = _load("articulated_v2")
    for p in b.prompts:
        motions = [i for i in p.must_have if MOTION_RE.search(i.lower())]
        assert len(motions) >= 3, f"{p.id}: fewer than 3 motion items for the pose sweep"
        quantified = [i for i in motions if NUMBER_RE.search(i)]
        assert len(quantified) >= 2, f"{p.id}: motions lack quantities (range / travel)"


def test_scene_prompts_state_composition_contracts():
    b = _load("scenes_v2")
    for p in b.prompts:
        assert len(p.must_have) >= 10, f"{p.id}: composition contract too thin"
        joined = " ".join(p.must_have).lower()
        assert "zones" in joined, f"{p.id}: no zone list"
        assert "luminance" in joined, f"{p.id}: no measurable lighting band"
        assert "camera" in joined, f"{p.id}: no camera brief"
        counted = [i for i in p.must_have if re.search(r"at least \d+|\b\d+ zones\b", i.lower())]
        assert len(counted) >= 4, f"{p.id}: too few counted asset types"


def test_graphics_v2_opengl_prompts_carry_language_override():
    """The ogl_* rows must run under opengl_python; the loader forwards the
    per-prompt override into the Spec (BenchPrompt.language or battery.language)."""
    from codeverse.contracts.common import Backends, Language

    from bench.run_bench import build_spec

    b = _load("graphics_v2")
    assert b.language == Language.GLSL_SHADER
    backends = Backends()
    for prompt in b.prompts:
        if prompt.id.startswith("ogl_"):
            assert prompt.language == Language.OPENGL_PYTHON, f"{prompt.id}: missing language override"
        else:
            assert prompt.language is None, f"{prompt.id}: unexpected language override"
        spec = build_spec(b, prompt, backends=backends, rounds=1, max_usd=1, max_minutes=1, tag0="t")
        want = Language.OPENGL_PYTHON if prompt.id.startswith("ogl_") else Language.GLSL_SHADER
        assert spec.language == want, f"{prompt.id}: spec language {spec.language} != {want}"


def test_compare_v2_is_a_subset_of_static_v2():
    static = {p.id.split("_", 1)[1]: p for p in _load("static_objects_v2").prompts}
    for p in _load("compare_v2").prompts:
        key = p.id.split("_", 1)[1]  # cmp_hard_violin -> hard_violin == prefix-stripped static key
        src = static.get(key)
        assert src is not None, f"{p.id}: no matching static_objects_v2 prompt"
        assert p.prompt == src.prompt, f"{p.id}: prompt text drifted from static_objects_v2"
        assert p.must_have == src.must_have, f"{p.id}: must_have drifted"
        assert p.dimensions_m == src.dimensions_m, f"{p.id}: dimensions drifted"
