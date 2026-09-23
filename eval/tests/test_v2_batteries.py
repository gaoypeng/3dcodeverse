"""The v2 prompt batteries: schema, the per-prompt language override, and the compare batteries drawn verbatim."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
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
    "compare_v3": PROMPTS / "compare_v3.yaml",
}

EXPECTED_COUNTS = {
    "static_objects_v2": 20,
    "articulated_v2": 14,
    "scenes_v2": 10,
    "graphics_v2": 10,
    "compare_v2": 8,
    "compare_v3": 12,
}

ID_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)+$")


@pytest.fixture(scope="module", params=sorted(V2_FILES), ids=sorted(V2_FILES))
def battery(request) -> Battery:
    b = Battery.load(V2_FILES[request.param])
    assert b.name == request.param
    return b


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


def test_graphics_v2_opengl_prompts_carry_language_override():
    """The ogl_* rows run under opengl_python: build_spec forwards the per-prompt override."""
    from bench.run_bench import build_spec
    from codeverse3d.contracts.common import Backends, Language

    b = _load("graphics_v2")
    for prompt in b.prompts:
        spec = build_spec(b, prompt, backends=Backends(), rounds=1, max_minutes=1, tag0="t")
        want = Language.OPENGL_PYTHON if prompt.id.startswith("ogl_") else Language.GLSL_SHADER
        assert spec.language == want, f"{prompt.id}: spec language {spec.language} != {want}"


def test_compare_v3_extends_compare_v2_verbatim():
    """Recorded compare_v2 cells are reused on compare_v3 runs, so the shared 8 must
    match exactly; the 4 extras widen category coverage (>=1 medium, >=3 hard)."""
    v2 = {p.id: p for p in _load("compare_v2").prompts}
    v3 = {p.id: p for p in _load("compare_v3").prompts}
    assert set(v2) <= set(v3)
    for pid, p in v2.items():
        assert v3[pid].model_dump() == p.model_dump(), f"{pid}: drifted from compare_v2"
    extra = [v3[pid] for pid in v3 if pid not in v2]
    assert len(extra) == 4
    assert sum(p.tier == "medium" for p in extra) >= 1
    assert sum(p.tier == "hard" for p in extra) >= 3
    assert not {p.category for p in extra} & {p.category for p in v2.values()}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def test_compare_v4_is_the_union_of_the_static_batteries_by_prompt_text():
    """compare_v4 = static_objects_v2 + every v1 prompt whose text is not in v2, kept VERBATIM."""
    v4 = _load_any("compare_v4")
    v2 = _load("static_objects_v2")
    v1 = _load_any("static_objects_v1")
    assert (v4.track, v4.language) == (v2.track, v2.language)
    assert len(v4.prompts) == 40 and len({p.id for p in v4.prompts}) == 40
    src = {p.id: p for p in [*v2.prompts, *v1.prompts]}
    for p in v4.prompts:
        assert p.id in src, f"{p.id}: not from static_objects_v1/v2"
        assert (p.prompt, p.must_have, p.dimensions_m) == (src[p.id].prompt, src[p.id].must_have, src[p.id].dimensions_m), p.id
        assert p.must_have, f"{p.id}: the fixed judge needs must_have"
    texts = [_norm(p.prompt) for p in v4.prompts]
    assert len(set(texts)) == len(texts), "duplicate prompt text"
    union = {_norm(p.prompt) for p in [*v2.prompts, *v1.prompts]}
    assert set(texts) == union, "v4 must cover every distinct v1/v2 prompt exactly once"
    assert {p.id for p in v2.prompts} <= {p.id for p in v4.prompts}
    tiers = {t: sum(1 for p in v4.prompts if p.tier == t) for t in ("easy", "medium", "hard")}
    assert tiers == {"easy": 8, "medium": 8, "hard": 24}, tiers


def _load_any(name: str) -> Battery:
    return _load(name) if name in V2_FILES else Battery.load(PROMPTS / f"{name}.yaml")
