"""Batch-1 contract additions: registries, as_line, typed AgentJob, RunOptions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codeverse.contracts import (
    ENTRY_FILE,
    LANGUAGE_LABEL,
    TRACK_INFO,
    TRACK_LANGUAGES,
    AgentJob,
    ApiAgentOptions,
    Language,
    RunOptions,
    Spec,
    Track,
    code_file,
)
from codeverse.contracts.artifacts import GateFinding, RenderSet, RenderView, Severity


# ------------------------------------------------------------------ registries
def test_track_info_covers_every_track_and_is_frozen():
    assert set(TRACK_INFO) == set(Track)
    assert TRACK_INFO[Track.STATIC_OBJECT].rubric == "static_object_v1"
    assert TRACK_INFO[Track.ARTICULATED_OBJECT].rubric == "articulated_v1"
    assert TRACK_INFO[Track.SCENE].rubric == "scene_v1"
    assert TRACK_INFO[Track.GRAPHICS].rubric == "shader_v1"
    with pytest.raises(ValidationError):
        TRACK_INFO[Track.SCENE].rubric = "other"  # frozen


def test_entry_file_and_labels_cover_every_language():
    assert set(ENTRY_FILE) == set(Language)
    assert set(LANGUAGE_LABEL) == set(Language)
    assert all(e.startswith("src/") for e in ENTRY_FILE.values())
    assert code_file(Language.BLENDER) == "code.py"
    assert code_file(Language.THREEJS) == "code.js"
    assert code_file(Language.GLSL_SHADER) == "code.frag"


def test_registries_agree_with_legacy_flywheel_tables():
    """Drift guard while the legacy tables still exist (they adopt these later)."""
    import codeverse.flywheel.sample as sample

    legacy_entry = getattr(sample, "ENTRY_BY_LANGUAGE", None)
    if legacy_entry is not None:
        assert legacy_entry == ENTRY_FILE
    legacy_code = getattr(sample, "CODE_FILE_BY_LANGUAGE", None)
    if legacy_code is not None:
        assert legacy_code == {lang: code_file(lang) for lang in Language}
    legacy_label = getattr(sample, "TYPE_LABEL", None)
    if legacy_label is not None:
        assert legacy_label == {t: TRACK_INFO[t].label for t in Track}


def test_language_frame_covers_every_language():
    from codeverse.conventions import LANGUAGE_FRAME, Frame

    assert set(LANGUAGE_FRAME) == {lang.value for lang in Language}
    assert LANGUAGE_FRAME["glsl_shader"] is Frame.Y_UP_POS_Z_FRONT
    assert LANGUAGE_FRAME["opengl_python"] is Frame.Y_UP_POS_Z_FRONT


# ------------------------------------------------------------------ as_line
def _finding(**kw) -> GateFinding:
    base = dict(gate="contract", severity=Severity.ERROR, target="leg_1", message="leg floats", fix_hint="drop z by 0.02")
    base.update(kw)
    return GateFinding(**base)


def test_as_line_default_is_message_plus_fix():
    assert _finding().as_line() == "leg floats FIX: drop z by 0.02"
    assert _finding(fix_hint="").as_line() == "leg floats"


def test_as_line_flag_combinations():
    f = _finding()
    assert f.as_line(with_gate=True, with_hint=False) == "GATE contract: leg floats"
    assert f.as_line(with_severity=True, with_hint=False) == "[error] leg floats"
    assert f.as_line(with_target=True, with_hint=False) == "leg floats [leg_1]"
    assert (f.as_line(with_gate=True, with_severity=True, with_target=True)
            == "GATE contract: [error] leg floats [leg_1] FIX: drop z by 0.02")
    assert not f.as_line().startswith("- ")


def test_as_line_skips_empty_target():
    assert _finding(target=None).as_line(with_target=True, with_hint=False) == "leg floats"


# ------------------------------------------------------------------ render flags
def test_render_view_judge_flag_defaults_none():
    v = RenderView(name="front", path="front.png")
    assert v.judge is None
    assert RenderView(name="front", path="front.png", judge=True).judge is True
    assert RenderSet().out_dir == ""


# ------------------------------------------------------------------ AgentJob
def test_agent_job_legacy_extra_is_lifted_and_kept():
    extra = {"round": 2, "kind": "refine", "language": "blender", "track": "static_object",
             "files_hint": ["src/a.py"], "mcp_command": ["python", "-m", "x"],
             "max_usd": 1.5, "temperature": 0.7, "thinking": "high", "allow_shell": False}
    j = AgentJob(workspace="w", prompt="p", extra=dict(extra))
    assert (j.round, j.kind, j.language, j.track) == (2, "refine", "blender", "static_object")
    assert j.files_hint == ["src/a.py"]
    assert j.mcp_command == ["python", "-m", "x"]
    assert j.api == ApiAgentOptions(max_usd=1.5, temperature=0.7, thinking="high", allow_shell=False)
    assert j.extra == extra  # untouched: legacy readers see what they were given


def test_agent_job_explicit_fields_beat_extra():
    j = AgentJob(workspace="w", prompt="p", round=5, api=ApiAgentOptions(max_usd=9.0),
                 extra={"round": 2, "max_usd": 1.0})
    assert j.round == 5
    assert j.api.max_usd == 9.0


def test_agent_job_defaults_and_roundtrip():
    j = AgentJob(workspace="w", prompt="p")
    assert j.round == 0 and j.kind == "" and j.files_hint == [] and j.mcp_command is None
    assert j.api == ApiAgentOptions()
    j2 = AgentJob(workspace="w", prompt="p", extra={"round": 3, "temperature": 0.9})
    assert AgentJob(**j2.model_dump()) == j2  # dump/construct stable


# ------------------------------------------------------------------ RunOptions
def test_spec_options_default_and_legacy_json():
    s = Spec(id="x", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="a chair")
    assert s.options == RunOptions()
    d = s.model_dump(mode="json")
    d.pop("options")
    s2 = Spec(**d)  # old spec.json without options still parses
    assert s2.options.candidates is None and s2.options.texture is False


def test_spec_options_roundtrip_and_validation():
    s = Spec(id="x", track=Track.SCENE, language=Language.SCENE_THREEJS, prompt="p",
             options=RunOptions(candidates=3, texture=True))
    s2 = Spec(**s.model_dump(mode="json"))
    assert s2.options == RunOptions(candidates=3, texture=True)
    with pytest.raises(ValidationError):
        RunOptions(candidates=0)  # ge=1


def test_spec_options_do_not_change_the_plan_stage_hash():
    from codeverse.orchestrator.runner import hash_inputs
    from codeverse.tracks.lifecycle import plan_stage_inputs

    plain = Spec(id="x", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="a chair")
    opted = plain.model_copy(update={"options": RunOptions(candidates=4, texture=True)})
    assert hash_inputs(plan_stage_inputs(plain)) == hash_inputs(plan_stage_inputs(opted))


def test_track_languages_still_the_gate():
    assert set(TRACK_LANGUAGES) == set(Track)
    with pytest.raises(ValidationError):
        Spec(id="x", track=Track.SCENE, language=Language.BLENDER, prompt="p")
