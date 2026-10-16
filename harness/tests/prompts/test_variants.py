"""The variant layer (D105): selection, the prompt overlay, caches, activation, knobs, the record."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jinja2 import FileSystemLoader, TemplateNotFound

from codeverse3d import prompts
from codeverse3d.config import Settings, get_settings
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.spec import RunOptions
from codeverse3d.prompts import variants as V
from codeverse3d.prompts.variants import (
    VARIANTS_DIR,
    activate,
    active_variant,
    apply_variant_knobs,
    knobs_applied,
    list_variants,
    resolve_variant,
    variant_hash,
    variant_info,
)
from tests.prompts.conftest import PROMPT_FILES, PROMPTS_DIR, is_template

TV_TOML = ('[variant]\nname = "tv"\nversion = 3\ngenerators = ["agy:gemini-4-argon"]\n'
           'description = "a test variant"\n\n[knobs]\nskills_max = 4\nskills_only = "c3d-part-contact"\n')


def _clear() -> None:
    prompts._load.cache_clear()
    prompts._env_for.cache_clear()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_process_variant(monkeypatch):
    """No variant from this machine's environment, and no cached overlay from another test."""
    monkeypatch.delenv("C3D_VARIANT", raising=False)
    monkeypatch.delenv("C3D_VARIANTS", raising=False)
    _clear()
    yield
    _clear()


@pytest.fixture
def overlay(tmp_path: Path, monkeypatch) -> Path:
    """A tmp ``VARIANTS_DIR`` holding ``tv``: a system.md that shadows blender's, a template only the
    variant ships, and a role template that includes a BASE file (the ChoiceLoader fall-through)."""
    root = tmp_path / "variants"
    shutil.copytree(VARIANTS_DIR / "argon", root / "argon")   # the shipped one stays known beside tv
    tv = root / "tv"
    for d in ("blender", "tracks", "system"):
        (tv / d).mkdir(parents=True)
    (tv / "variant.toml").write_text(TV_TOML)
    (tv / "README.md").write_text("docs only\n")
    (tv / "blender" / "system.md").write_text("# OVERLAY SYSTEM\n")
    (tv / "tracks" / "only_in_variant.j2").write_text("overlay {{ x }}\n")
    (tv / "system" / "role_repair.j2").write_text("OVERLAY ROLE {{ contract }}\n{% include 'system/singleshot_format.md' %}")
    monkeypatch.setattr(V, "VARIANTS_DIR", root)
    _clear()
    return root


# --------------------------------------------------------------------------- selection
def test_resolution_order_including_the_served_model_lookup(overlay):
    """settings.variant > variants[id as typed] > variants['<kind>:<served model>'] > base; plain lookups."""
    table = {"agy:gemini-4-argon": "tv", "agy:gemini-4-argon-low": "argon", "claude-code:claude-opus-5": "tv"}
    assert resolve_variant(Settings(variant="argon", variants=table), "agy:gemini-4-argon") == "argon"
    assert resolve_variant(Settings(variants=table), "agy:gemini-4-argon-low") == "argon", "the id as typed wins"
    assert resolve_variant(Settings(variants=table), "agy:gemini-4-argon-high") == "tv", "then the served model"
    assert resolve_variant(Settings(variants=table), "agy:gemini-4-argon") == "tv"
    assert resolve_variant(Settings(variants=table), "claude-code:claude-opus-5") == "tv"
    assert resolve_variant(Settings(variants=table), "codex:gpt-6.1-sol") == ""
    assert resolve_variant(Settings(variants=table), "single-shot:gemini:gemini-4-argon") == "", "no served spelling"
    assert resolve_variant(Settings(), "agy:gemini-4-argon") == ""
    assert resolve_variant(Settings(variant="  tv "), "x") == "tv"
    assert V.served_id("agy:gemini-4-argon-medium") == "agy:gemini-4-argon" and V.served_id("nocolon") == ""


def test_an_unknown_name_raises_and_names_the_known_ones(overlay):
    for call in (lambda: resolve_variant(Settings(variant="bogus"), "agy:x"),
                 lambda: resolve_variant(Settings(variants={"agy:x": "bogus"}), "agy:x"),
                 lambda: variant_info("bogus"), lambda: variant_hash("bogus")):
        with pytest.raises(ValueError, match=r"unknown variant 'bogus' \(known: argon, tv\)"):
            call()
    with pytest.raises(ValueError, match="unknown variant"), activate("bogus"):
        pass
    with pytest.raises(ValueError, match="plain directory name"):
        V.variant_root("../escape")
    assert list(list_variants()) == ["argon", "tv"] and list_variants()["tv"].version == 3


@pytest.mark.parametrize("toml, problem", [
    ('[variant]\nname = "other"\nversion = 1\n', "must equal the directory name"),
    ('[variant]\nname = "tv"\nversion = 0\n', "version must be an integer >= 1"),
    ('[variant]\nname = "tv"\nversion = "1"\n', "version must be an integer"),
    ('[variant]\nname = "tv"\ngenerators = "agy:x"\n', "generators must be a list"),
    ('[variant]\nname = "tv"\n[knobs]\nrounds = 3\n', "is not a variant knob"),
    ('[variant]\nname = "tv"\n[knobs]\nskills_max = "four"\n', "skills_max must be a int >= 0"),
    ('[variant]\nname = "tv"\n[knobs]\nskills_max = true\n', "skills_max must be a int"),
    ('[variant]\nname = "tv"\n[knobs]\nskills_only = 4\n', "skills_only must be a str"),
    ('[variant]\nname = "tv"\n[extra]\nx = 1\n', "unknown table"),
    ('[variant]\nname = "tv"\nmodel = "x"\n', "unknown \\[variant\\] key"),
    ('name = "tv"\n', "missing the \\[variant\\] table"),
    ('[variant\nname = "tv"\n', "not valid TOML"),
])
def test_variant_toml_is_type_checked(overlay, toml, problem):
    (overlay / "tv" / "variant.toml").write_text(toml)
    with pytest.raises(ValueError, match=problem):
        variant_info("tv")


# --------------------------------------------------------------------------- the prompt overlay
def test_an_overlay_file_shadows_the_base_and_a_missing_one_falls_back(overlay):
    base_system = (PROMPTS_DIR / "blender" / "system.md").read_text()
    base_contract = (PROMPTS_DIR / "blender" / "contract.md").read_text()
    with activate("tv"):
        assert prompts.load_text("blender/system.md") == "# OVERLAY SYSTEM\n"
        assert prompts.load_text("blender/contract.md") == base_contract, "no overlay copy: the base file"
        assert prompts.prompt_root("blender/system.md") == overlay / "tv" / "blender" / "system.md"
        assert prompts.prompt_root("blender/contract.md") == PROMPTS_DIR / "blender" / "contract.md"
        assert prompts.render("tracks/only_in_variant.j2", x=1) == "overlay 1\n"
        rendered = prompts.render("system/role_repair.j2", contract="C")
        assert rendered.startswith("OVERLAY ROLE C\n")
        assert (PROMPTS_DIR / "system" / "singleshot_format.md").read_text().strip() in rendered, "a base include"
        # the catalog resolves through the same loader: callers do not change
        from codeverse3d.prompts.catalog import language_text

        assert language_text(Language.BLENDER, "system.md") == "# OVERLAY SYSTEM\n"
        assert language_text(Language.BLENDER, "contract.md") == base_contract
        with pytest.raises(FileNotFoundError):
            prompts.load_text("blender/nowhere.md")
    assert prompts.load_text("blender/system.md") == base_system
    with pytest.raises(TemplateNotFound):
        prompts.render("tracks/only_in_variant.j2", x=1)


def test_base_rendering_is_untouched_and_byte_identical_under_an_empty_overlay():
    """The base environment is the single FileSystemLoader it always was (the manifest pins every
    payload); the shipped argon overlay is empty, so under it every static prompt file reads the same."""
    assert isinstance(prompts._env.loader, FileSystemLoader) and prompts._env.loader.searchpath == [str(PROMPTS_DIR)]
    assert prompts._env_for("") == (prompts._env, prompts._lenient_env)
    static = [rel for rel in PROMPT_FILES if not is_template(rel)]
    assert static
    base = {rel: (prompts.load_text(rel), prompts.render(rel)) for rel in static}
    with activate("argon"):
        assert active_variant() == "argon"
        for rel in static:
            assert (prompts.load_text(rel), prompts.render(rel)) == base[rel], rel
            assert prompts.prompt_root(rel) == PROMPTS_DIR / rel


def test_caches_are_isolated_per_variant_in_process(overlay):
    base = prompts.load_text("blender/system.md")
    assert base != "# OVERLAY SYSTEM\n"
    with activate("tv"):
        assert prompts.load_text("blender/system.md") == "# OVERLAY SYSTEM\n"
        strict, lenient = prompts._env_for("tv")
        assert strict is not prompts._env and lenient is not prompts._lenient_env
        assert prompts._env_for("tv") == (strict, lenient), "one environment per variant"
    assert prompts.load_text("blender/system.md") == base
    with activate("tv"):
        assert prompts.load_text("blender/system.md") == "# OVERLAY SYSTEM\n"


def test_activate_nests_and_falls_back_to_the_process_switch(overlay, monkeypatch):
    assert active_variant() == ""
    with activate("argon"):
        assert active_variant() == "argon"
        with activate("tv"):
            assert active_variant() == "tv"
        assert active_variant() == "argon"
        with activate(""):
            assert active_variant() == "", "'' inside a block is the base, whatever the process says"
    assert active_variant() == ""
    monkeypatch.setenv("C3D_VARIANT", "argon")
    get_settings.cache_clear()
    assert active_variant() == "argon", "outside any block: get_settings().variant"
    with activate(""):
        assert active_variant() == ""
    assert active_variant() == "argon"
    monkeypatch.setenv("C3D_VARIANT", "../escape")
    get_settings.cache_clear()
    assert active_variant() == "", "a name that is not a plain directory name is the base, never a path"


def test_an_exception_inside_the_block_still_resets(overlay):
    with pytest.raises(RuntimeError), activate("tv"):
        raise RuntimeError("boom")
    assert active_variant() == ""


# --------------------------------------------------------------------------- provenance
def test_variant_hash_covers_both_overlays_and_ignores_readmes(overlay, tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    (skills / "variants" / "tv").mkdir(parents=True)
    (skills / "variants" / "tv" / "routes.toml").write_text("")
    monkeypatch.setenv("C3D_SKILLS_DIR", str(skills))
    get_settings.cache_clear()
    assert variant_hash("") == ""
    h0 = variant_hash("tv")
    assert len(h0) == 12 and int(h0, 16) >= 0
    (overlay / "tv" / "README.md").write_text("edited docs\n")
    assert variant_hash("tv") == h0, "README.md is documentation, not prompt material"
    (overlay / "tv" / "blender" / "system.md").write_text("# OVERLAY SYSTEM v2\n")
    h1 = variant_hash("tv")
    assert h1 != h0
    (skills / "variants" / "tv" / "routes.toml").write_text('[[boost]]\nskill = "c3d-part-contact"\ndelta = 5\n')
    h2 = variant_hash("tv")
    assert h2 not in (h0, h1), "the skills half counts too"
    (overlay / "tv" / "variant.toml").write_text(TV_TOML.replace("version = 3", "version = 4"))
    assert variant_hash("tv") not in (h0, h1, h2), "a version bump moves it"
    assert V.variant_block("tv") == {"name": "tv", "version": 4, "hash": variant_hash("tv")}
    assert V.variant_block("") == {"name": "", "version": 0, "hash": ""}
    assert V.variant_block("gone") == {"name": "gone", "version": 0, "hash": ""}


def test_the_argon_variant_ships_an_empty_overlay_at_version_2():
    """Iteration 1 (2026-10-15) shadowed the two zone briefs and boosted the Blender placement skill;
    D106 withdrew both (the briefs carried a template; the boost amplified it) and iterations 2-4 ran
    on the EMPTY overlay — iteration 4 was accepted as v2 (2026-10-16, ledger argon_scene): the gain
    was harness code (D106-D108), so the overlay ships only its README and variant.toml."""
    from codeverse3d.skills import variant_dir, variants_table

    info = variant_info("argon")
    assert info.name == "argon" and info.version == 2 and info.generators == ("agy:gemini-4-argon",) and info.knobs == {}
    assert info.description.startswith("gemini-4-argon on the scene track (threejs + blender)")
    assert "argon" in list_variants()
    assert sorted(p.name for p in (VARIANTS_DIR / "argon").iterdir()) == ["README.md", "variant.toml"]
    assert sorted(p.name for p in variant_dir("argon").iterdir()) == ["README.md", "routes.toml"]
    table = variants_table("argon")
    assert table.variant == "argon" and not table.routes and not table.disabled and not table.boosts
    assert len(variant_hash("argon")) == 12


# --------------------------------------------------------------------------- knobs
def test_knobs_apply_as_defaults_and_a_user_stated_field_wins(overlay, monkeypatch):
    info = variant_info("tv")
    assert info.knobs == {"skills_max": 4, "skills_only": "c3d-part-contact"}
    monkeypatch.delenv("C3D_SKILLS_MAX", raising=False)
    monkeypatch.delenv("C3D_SKILLS_ONLY", raising=False)
    s = Settings()
    before = set(s.model_fields_set)
    assert apply_variant_knobs(s, info) == {"skills_max": 4, "skills_only": "c3d-part-contact"}
    assert (s.skills_max, s.skills_only) == (4, "c3d-part-contact")
    assert set(s.model_fields_set) == before, "a knob is a default, never a statement"
    # stated by the user: an explicit kwarg (config.yaml) and the environment both win
    s = Settings(skills_max=2)
    assert apply_variant_knobs(s, info) == {"skills_only": "c3d-part-contact"} and s.skills_max == 2
    monkeypatch.setenv("C3D_SKILLS_MAX", "3")
    monkeypatch.setenv("C3D_SKILLS_ONLY", "c3d-scene-water")
    s = Settings()
    assert apply_variant_knobs(s, info) == {} and (s.skills_max, s.skills_only) == (3, "c3d-scene-water")
    # re-application and a later variant still see an unstated field
    monkeypatch.delenv("C3D_SKILLS_MAX", raising=False)
    monkeypatch.delenv("C3D_SKILLS_ONLY", raising=False)
    s = Settings()
    apply_variant_knobs(s, info)
    other = info.model_copy(update={"knobs": {"skills_max": 7}})
    assert apply_variant_knobs(s, other) == {"skills_max": 7} and s.skills_max == 7


def test_knobs_applied_restores_the_values_and_the_field_set_even_when_the_block_raises(overlay, monkeypatch):
    """The block form the run uses: the knobs hold inside it and nothing of them survives it — not the
    values, not the field-set membership that would make a leaked value look like a default."""
    monkeypatch.delenv("C3D_SKILLS_MAX", raising=False)
    monkeypatch.delenv("C3D_SKILLS_ONLY", raising=False)
    info = variant_info("tv")
    default_max = Settings().skills_max
    s = Settings(skills_only="c3d-scene-water")          # stated by the user: never touched, stays stated
    with knobs_applied(s, info) as applied:
        assert applied == {"skills_max": 4} and (s.skills_max, s.skills_only) == (4, "c3d-scene-water")
    assert (s.skills_max, s.skills_only) == (default_max, "c3d-scene-water")
    assert set(s.model_fields_set) & V.KNOB_FIELDS == {"skills_only"}
    s = Settings()
    with pytest.raises(RuntimeError), knobs_applied(s, info):
        assert (s.skills_max, s.skills_only) == (4, "c3d-part-contact")
        raise RuntimeError("boom")
    assert (s.skills_max, s.skills_only) == (default_max, "") and not set(s.model_fields_set) & V.KNOB_FIELDS
    # nested on ONE object (the singleton is also the injected Settings): the outer block's prior wins
    with knobs_applied(s, info), knobs_applied(s, info):
        assert s.skills_max == 4
    assert s.skills_max == default_max and not set(s.model_fields_set) & V.KNOB_FIELDS
    with knobs_applied(s, None) as applied:
        assert applied == {} and s.skills_max == default_max, "no variant: nothing applied, nothing to undo"


# --------------------------------------------------------------------------- the run
def test_plan_stage_key_is_unchanged_without_a_variant_and_names_one_when_set(overlay):
    from codeverse3d.tracks.lifecycle import plan_stage_inputs, plan_stage_key
    from tests.orchestrator_tracks.conftest import make_spec

    spec = make_spec()
    base = plan_stage_key(spec, Track.STATIC_OBJECT)
    assert base == {"spec": plan_stage_inputs(spec), "track": "static_object"}, "the default key never moves"
    with activate("tv"):
        assert plan_stage_key(spec, Track.STATIC_OBJECT) == {**base, "variant": "tv"}
    frozen = make_spec(options=RunOptions(variant="argon"))
    assert plan_stage_key(frozen, Track.STATIC_OBJECT) == {**base, "variant": "argon"}
    with activate("tv"):
        assert plan_stage_key(frozen, Track.STATIC_OBJECT)["variant"] == "argon", "the spec's frozen choice wins"


def _writer(job: Any, ws: Any) -> dict[str, str]:
    return {"src/object.js": f"// {job.label} r{job.round}\nexport function build(THREE) {{ return new THREE.Group(); }}\n"}


def _run(tmp_path: Path, monkeypatch, *, options: RunOptions | None = None, env: dict[str, str] | None = None,
         settings: Settings | None = None, on_generate: Callable[[Settings], None] | None = None) -> tuple[Any, Any, Settings]:
    """One judged baseline of the static track through the fakes, under ``env``.  With ``settings`` the
    run reuses that object AND the process singleton as they are (no cache clear): the second run of
    one process, the way a manifest rig or a bench cell follows the run before it.  ``on_generate``
    is called with the injected Settings every time the fake agent generates — a probe INSIDE the run."""
    from codeverse3d.tracks.planner import plan_example
    from codeverse3d.tracks.static_object import StaticObjectTrack
    from codeverse3d.workspace import Workspace
    from tests.orchestrator_tracks.conftest import make_spec
    from tests.orchestrator_tracks.fakes import (
        FakeAgent,
        FakeJudge,
        FakeRuntime,
        FakeServices,
        _planner,
    )

    monkeypatch.setenv("C3D_PLAN_BRIEF", "off")   # one canned plan per planner request
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    spec = make_spec(max_rounds=0, **({"options": options} if options is not None else {}))
    if settings is None:
        get_settings.cache_clear()
        settings = Settings(runs_dir=tmp_path / "runs", cache_dir=tmp_path / "cache")
    injected = settings

    def writer(job: Any, ws: Any) -> dict[str, str]:
        if on_generate is not None:
            on_generate(injected)
        return _writer(job, ws)

    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.6,)), agent=FakeAgent(writer),
                              planner_model=_planner(plan_example(Track.STATIC_OBJECT)), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS))
    ws = Workspace(tmp_path / "runs" / "r")
    return track.run(spec, ws), ws, settings


def _events(ws: Any) -> list[dict[str, Any]]:
    from codeverse3d.proc import EventLog

    return EventLog(ws.events_path).read()


def test_a_run_records_the_variant_it_activated(tmp_path, monkeypatch):
    from codeverse3d.orchestrator import hash_inputs
    from codeverse3d.proc import read_jsonl_lenient
    from codeverse3d.record.telemetry import settings_snapshot
    from codeverse3d.tracks.lifecycle import plan_stage_key

    rec, ws, _ = _run(tmp_path, monkeypatch, options=RunOptions(variant="argon"))
    h = variant_hash("argon")
    assert rec.environment["variant"] == "argon"
    assert rec.extra["variant"] == {"name": "argon", "version": 2, "hash": h}
    assert rec.prompt_hashes["variant"] == h and "generate" in rec.prompt_hashes
    snap = settings_snapshot(rec)
    assert (snap.variant, snap.variant_version, snap.variant_hash) == ("argon", 2, h)
    events = _events(ws)
    assert next(e for e in events if e["event"] == "run.start")["variant"] == "argon"
    active = next(e for e in events if e["event"] == "variant.active")
    assert (active["name"], active["version"], active["hash"], active["knobs"]) == ("argon", 2, h, {})
    attached = [e for e in events if e["event"] == "skills.attached"]
    assert attached and {e["variant"] for e in attached} == {"argon"}
    rows = read_jsonl_lenient(ws.root / "telemetry" / "skills.jsonl")
    assert rows and {r["variant"] for r in rows} == {"argon"}
    # the plan was cached under a key that names the variant
    stored = json.loads((ws.root / "stages" / "plan.json").read_text())["inputs_hash"]
    with activate("argon"):
        assert stored == hash_inputs(plan_stage_key(rec.spec, Track.STATIC_OBJECT))
    assert stored != hash_inputs(plan_stage_key(rec.spec.model_copy(update={"options": RunOptions()}), Track.STATIC_OBJECT))
    assert active_variant() == "", "the activation ends with the run"


def test_a_base_run_records_no_variant(tmp_path, monkeypatch):
    from codeverse3d.record.telemetry import settings_snapshot

    rec, ws, _ = _run(tmp_path, monkeypatch)
    assert rec.environment["variant"] == "" and "variant" not in rec.prompt_hashes
    assert rec.extra["variant"] == {"name": "", "version": 0, "hash": ""}
    snap = settings_snapshot(rec)
    assert (snap.variant, snap.variant_version, snap.variant_hash) == ("", 0, "")
    events = _events(ws)
    assert next(e for e in events if e["event"] == "run.start")["variant"] == ""
    assert not [e for e in events if e["event"] == "variant.active"]
    assert {e["variant"] for e in events if e["event"] == "skills.attached"} == {""}


def test_a_run_honours_the_process_switch_when_the_spec_says_nothing(tmp_path, monkeypatch):
    """What the manifest scenario ``scene.scene_threejs.agent.variant_argon`` relies on: a direct
    ``track.run`` under ``C3D_VARIANT`` (an A/B arm's environment) runs the variant — and the spec on
    disk still says nothing (the CLI is what freezes a choice; the record names what ran)."""
    from codeverse3d.contracts.spec import Spec

    rec, ws, _ = _run(tmp_path, monkeypatch, env={"C3D_VARIANT": "argon"})
    assert rec.environment["variant"] == "argon" and rec.extra["variant"]["name"] == "argon"
    assert Spec.model_validate_json(ws.spec_path.read_text()).options.variant == ""
    with pytest.raises(ValueError, match="unknown variant 'bogus'"):
        _run(tmp_path / "again", monkeypatch, env={"C3D_VARIANT": "bogus"})


def _knobs(s: Settings) -> tuple[int, str, int, str]:
    """What the injected Settings and the process singleton say right now."""
    return s.skills_max, s.skills_only, get_settings().skills_max, get_settings().skills_only


def test_a_run_applies_the_variants_knobs_to_the_process_and_the_injected_settings(tmp_path, monkeypatch, overlay):
    monkeypatch.delenv("C3D_SKILLS_MAX", raising=False)
    monkeypatch.delenv("C3D_SKILLS_ONLY", raising=False)
    seen: list[tuple[int, str, int, str]] = []
    rec, ws, settings = _run(tmp_path, monkeypatch, options=RunOptions(variant="tv"),
                             on_generate=lambda s: seen.append(_knobs(s)))
    active = next(e for e in _events(ws) if e["event"] == "variant.active")
    assert active["knobs"] == {"skills_max": 4, "skills_only": "c3d-part-contact"} and active["version"] == 3
    assert seen and set(seen) == {(4, "c3d-part-contact", 4, "c3d-part-contact")}, "held on both objects while it ran"
    assert (settings.skills_max, settings.skills_only) == (Settings().skills_max, ""), "and for the run only"
    assert rec.extra["variant"]["name"] == "tv"
    # the sessions routed through the knob: only the one bundle, and only when it applies
    attached = [e for e in _events(ws) if e["event"] == "skills.attached"]
    assert attached and all(set(e["skills"]) <= {"c3d-part-contact"} for e in attached)
    # a field the user stated survives the knob
    seen.clear()
    rec, ws, settings = _run(tmp_path / "stated", monkeypatch, options=RunOptions(variant="tv"), env={"C3D_SKILLS_MAX": "2"},
                             on_generate=lambda s: seen.append(_knobs(s)))
    assert next(e for e in _events(ws) if e["event"] == "variant.active")["knobs"] == {"skills_only": "c3d-part-contact"}
    assert seen and set(seen) == {(2, "c3d-part-contact", 2, "c3d-part-contact")}
    assert (settings.skills_max, settings.skills_only) == (2, ""), "the statement stays; the knob does not"


def test_a_variants_knobs_do_not_outlive_the_run_the_next_run_of_the_process_starts_from_the_base(tmp_path, monkeypatch, overlay):
    """The overlays are ContextVar-scoped, but the knobs are written onto Settings objects that outlive
    the run — the process singleton ``attach_skills`` reads, a rig's injected Settings.  Left there, the
    base run that followed a variant run in one process (a manifest rig, a bench cell after a variant
    cell, any script calling ``track.run`` twice) routed at most the variant's ``skills_max`` and ONLY its
    ``skills_only`` — and ``model_fields_set`` called the leaked value a default."""
    monkeypatch.delenv("C3D_SKILLS_MAX", raising=False)
    monkeypatch.delenv("C3D_SKILLS_ONLY", raising=False)
    default_max, default_only = Settings().skills_max, Settings().skills_only
    rec, ws, settings = _run(tmp_path, monkeypatch, options=RunOptions(variant="tv"))
    attached = [e for e in _events(ws) if e["event"] == "skills.attached"]
    assert attached and all(set(e["skills"]) <= {"c3d-part-contact"} for e in attached), "the knobs held during the run"
    singleton = get_settings()          # the object the run applied the knobs to: not cleared since
    for s in (settings, singleton):
        assert (s.skills_max, s.skills_only) == (default_max, default_only)
        assert not set(s.model_fields_set) & V.KNOB_FIELDS, "nothing left that looks like a user statement"
    # the base run that follows, on the same injected Settings and the same singleton
    rec, ws, same = _run(tmp_path / "base", monkeypatch, settings=settings)
    assert same is settings and get_settings() is singleton and rec.environment["variant"] == ""
    listed = {n for e in _events(ws) if e["event"] == "skills.attached" for n in e["skills"]}
    assert listed - {"c3d-part-contact"}, f"the base run routed only what the variant's knobs allowed: {listed}"
    assert (settings.skills_max, settings.skills_only) == (default_max, default_only)
