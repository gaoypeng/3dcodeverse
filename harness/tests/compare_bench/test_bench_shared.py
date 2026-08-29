"""Shared bench plumbing: select_prompts / build_spec / settings-routed backends."""

from __future__ import annotations

from types import SimpleNamespace

import bench.compare_backends as cb
import bench.run_bench as rb
from bench.run_bench import Battery, BenchOptions, build_spec, select_prompts, spec_for
from tests.compare_bench.conftest import BATTERY


def _battery() -> Battery:
    return Battery.load(BATTERY)


def test_select_prompts_ids_tiers_limit():
    b = _battery()
    assert select_prompts(b) == b.prompts
    only = select_prompts(b, ids=[b.prompts[0].id])
    assert [p.id for p in only] == [b.prompts[0].id]
    tiers = {p.tier for p in b.prompts}
    tier = sorted(tiers)[0]
    assert all(p.tier == tier for p in select_prompts(b, tiers=[tier]))
    assert len(select_prompts(b, limit=1)) == 1
    assert select_prompts(b, ids=["nope"]) == []


def test_select_prompts_is_single_owner_for_both_drivers():
    """compare_backends deleted its private copy; both drivers filter identically."""
    assert cb.select_prompts is select_prompts and cb.default_run_track is rb.default_run_track


def test_build_spec_tags_and_budget():
    b = _battery()
    item = b.prompts[0]
    spec = build_spec(b, item, backends=rb.get_settings().backends(), rounds=2,
                      max_minutes=10, tag0="compare", extra_tags=("harness",))
    assert spec.tags[:4] == ["compare", b.name, item.tier, item.category] and "harness" in spec.tags
    assert spec.budget.max_rounds == 2 and not hasattr(spec.budget, "max_usd")
    assert spec.id == f"{b.name}/{item.id}"


def test_spec_for_routes_through_settings_backends():
    b = _battery()
    s = rb.get_settings()
    spec = spec_for(b, b.prompts[0], BenchOptions(generator="gemini-cli:gemini-3.7-flash"))
    assert spec.backends.generator == "gemini-cli:gemini-3.7-flash"
    # unset roles fall through to the settings defaults, not the Backends() literals
    assert spec.backends.planner == s.default_planner and spec.backends.judge == s.default_judge
    assert spec.backends.captioner == s.default_captioner


# ------------------------------------------------------- the harness arm is not blender-only
def test_the_harness_arm_looks_for_each_languages_own_entry_file():
    """``bench/_oneshot.MODEL_FILE`` is ``src/model.py`` because the ONE-SHOT arms are a
    blender-only comparison.  The harness arm is not, and gating it on that constant made
    every glsl / three.js / scene / moderngl cell ``no_code`` **0.0** while the run itself
    came back ``passed`` — measured 2026-08-25 on a glsl_shader A/A whose control wrote
    ``src/shader.frag`` and was scored zero for it.

    Four of the seven languages were affected, which is why no graphics or three.js bundle
    has ever had a readable A/B: both arms scored 0.0 and the rig called that "no effect".
    """
    from bench.compare_backends import entry_of
    from codeverse.contracts.common import ENTRY_FILE, Language

    for language, entry in ENTRY_FILE.items():
        spec = SimpleNamespace(language=language)
        assert entry_of(spec) == entry, f"{language.value} delivers {entry}"

    # the four that the hardcoded constant got wrong, named so the regression is legible
    assert {lang.value for lang, e in ENTRY_FILE.items() if e != "src/model.py"} == {
        "threejs", "scene_threejs", "glsl_shader", "opengl_python"}
    assert entry_of(SimpleNamespace(language=Language.GLSL_SHADER)) == "src/shader.frag"


def test_every_language_in_the_enum_has_an_entry_file():
    """``entry_of`` indexes ``ENTRY_FILE`` directly, so a new language without a row would
    raise KeyError deep inside a paid cell instead of failing here."""
    from codeverse.contracts.common import ENTRY_FILE, Language

    missing = [lang.value for lang in Language if lang not in ENTRY_FILE]
    assert not missing, f"no ENTRY_FILE row for {missing}"
