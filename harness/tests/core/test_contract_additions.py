"""Registry, finding, agent-job, and run-option contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codeverse3d.contracts import (
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
from codeverse3d.contracts.artifacts import GateFinding, RenderSet, RenderView, Severity


# ------------------------------------------------------------------ registries
def test_registries_cover_their_enums_and_are_frozen():
    from codeverse3d.conventions import LANGUAGE_FRAME, Frame

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
    from codeverse3d.orchestrator import hash_inputs
    from codeverse3d.tracks.lifecycle import plan_stage_inputs

    plain = Spec(id="x", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="a chair")
    opted = plain.model_copy(update={"options": RunOptions(candidates=4, texture=True)})
    assert hash_inputs(plan_stage_inputs(plain)) == hash_inputs(plan_stage_inputs(opted))


def test_records_written_before_the_2026_08_30_field_retirements_still_load():
    """RenderSet.turntable (360 records carry the key, none non-null), SkillRead.
    first_seen_turn (an api-agent leftover) and AgentJob.images (dead once image staging
    moved to .3dcode/images/) were deleted.  Every model here ignores unknown keys, so the
    stored runs on disk must keep re-reading — that is the whole licence for the delete."""
    from codeverse3d.contracts.agent import AgentJob
    from codeverse3d.contracts.run import RoundRecord, SkillRead

    rs = RenderSet.model_validate({"renderer": "blender", "turntable": "renders/turntable.mp4",
                                   "contact_sheet": "renders/sheet.png"})
    assert rs.contact_sheet == "renders/sheet.png" and not hasattr(rs, "turntable")
    sk = SkillRead.model_validate({"name": "bpy_modifiers", "surfaced": True, "first_seen_turn": None})
    assert sk.surfaced and not hasattr(sk, "first_seen_turn")
    job = AgentJob.model_validate({"workspace": "/w", "prompt": "p",
                                   "images": [{"path": "renders/sheet.png", "label": "sheet"}]})
    assert job.prompt == "p" and not hasattr(job, "images")
    # and the field this batch ADDED round-trips through the round journal
    rec = RoundRecord.model_validate({"index": 1, "kind": "refine",
                                      "pairwise": {"a": "r00", "b": "r01", "winner": "a", "confidence": 0.9}})
    assert rec.pairwise is not None and rec.pairwise.accepted is False
    assert RoundRecord(index=0, kind="baseline").pairwise is None
