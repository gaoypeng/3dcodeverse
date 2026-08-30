"""Registry, finding, agent-job, and run-option contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codeverse.contracts import (
    ENTRY_FILE,
    LANGUAGE_LABEL,
    TRACK_INFO,
    TRACK_LANGUAGES,
    Language,
    RunOptions,
    Spec,
    Track,
    code_file,
)
from codeverse.contracts.artifacts import GateFinding, RenderSet, RenderView, Severity


# ------------------------------------------------------------------ registries
def test_registries_cover_their_enums_and_are_frozen():
    from codeverse.conventions import LANGUAGE_FRAME, Frame

    assert set(TRACK_INFO) == set(Track)
    assert TRACK_INFO[Track.STATIC_OBJECT].rubric == "static_object_v1"
    assert TRACK_INFO[Track.ARTICULATED_OBJECT].rubric == "articulated_v1"
    assert TRACK_INFO[Track.SCENE].rubric == "scene_v1"
    assert TRACK_INFO[Track.GRAPHICS].rubric == "shader_v2"
    with pytest.raises(ValidationError):
        TRACK_INFO[Track.SCENE].rubric = "other"  # frozen
    assert set(ENTRY_FILE) == set(Language)
    assert set(LANGUAGE_LABEL) == set(Language)
    assert set(TRACK_LANGUAGES) == set(Track)
    assert all(e.startswith("src/") for e in ENTRY_FILE.values())
    assert code_file(Language.BLENDER) == "code.py"
    assert code_file(Language.THREEJS) == "code.js"
    assert code_file(Language.GLSL_SHADER) == "code.frag"
    assert set(LANGUAGE_FRAME) == {lang.value for lang in Language}
    assert LANGUAGE_FRAME["glsl_shader"] is Frame.Y_UP_POS_Z_FRONT
    assert LANGUAGE_FRAME["opengl_python"] is Frame.Y_UP_POS_Z_FRONT


# ------------------------------------------------------------------ as_line
def _finding(**kw) -> GateFinding:
    base = dict(gate="contract", severity=Severity.ERROR, target="leg_1", message="leg floats", fix_hint="drop z by 0.02")
    base.update(kw)
    return GateFinding(**base)


def test_as_line_formats_defaults_flags_and_empty_fields():
    assert _finding().as_line() == "leg floats FIX: drop z by 0.02"
    assert _finding(fix_hint="").as_line() == "leg floats"
    f = _finding()
    assert f.as_line(with_gate=True, with_hint=False) == "GATE contract: leg floats"
    assert f.as_line(with_severity=True, with_hint=False) == "[error] leg floats"
    assert f.as_line(with_target=True, with_hint=False) == "leg floats [leg_1]"
    assert (f.as_line(with_gate=True, with_severity=True, with_target=True)
            == "GATE contract: [error] leg floats [leg_1] FIX: drop z by 0.02")
    assert not f.as_line().startswith("- ")
    assert _finding(target=None).as_line(with_target=True, with_hint=False) == "leg floats"


# ------------------------------------------------------------------ render flags
def test_render_view_judge_flag_defaults_none():
    v = RenderView(name="front", path="front.png")
    assert v.judge is None
    assert RenderView(name="front", path="front.png", judge=True).judge is True
    assert RenderSet().out_dir == ""


# ------------------------------------------------------------------ RunOptions
def test_spec_options_default_legacy_roundtrip_and_validation():
    s = Spec(id="x", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="a chair")
    assert s.options == RunOptions()
    d = s.model_dump(mode="json")
    d.pop("options")
    s2 = Spec(**d)  # old spec.json without options still parses
    assert s2.options.candidates is None and s2.options.texture is False
    s = Spec(id="x", track=Track.SCENE, language=Language.SCENE_THREEJS, prompt="p",
             options=RunOptions(candidates=3, texture=True))
    s2 = Spec(**s.model_dump(mode="json"))
    assert s2.options == RunOptions(candidates=3, texture=True)
    with pytest.raises(ValidationError):
        RunOptions(candidates=0)  # ge=1


def test_spec_options_do_not_change_the_plan_stage_hash():
    from codeverse.orchestrator import hash_inputs
    from codeverse.tracks.lifecycle import plan_stage_inputs

    plain = Spec(id="x", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="a chair")
    opted = plain.model_copy(update={"options": RunOptions(candidates=4, texture=True)})
    assert hash_inputs(plan_stage_inputs(plain)) == hash_inputs(plan_stage_inputs(opted))
