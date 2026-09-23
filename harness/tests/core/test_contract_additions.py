"""Run options and old-record compatibility."""

from __future__ import annotations

from codeverse3d.contracts.artifacts import RenderSet
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.spec import RunOptions, Spec


def test_spec_options_do_not_change_the_plan_stage_hash():
    from codeverse3d.orchestrator import hash_inputs
    from codeverse3d.tracks.lifecycle import plan_stage_inputs

    plain = Spec(id="x", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="a chair")
    opted = plain.model_copy(update={"options": RunOptions(candidates=4, texture=True)})
    assert hash_inputs(plan_stage_inputs(plain)) == hash_inputs(plan_stage_inputs(opted))


def test_records_written_before_the_2026_08_30_field_retirements_still_load():
    """Stored runs carry keys since retired (or lack ones added since); every model here must keep loading them."""
    from codeverse3d.contracts.agent import AgentJob
    from codeverse3d.contracts.run import RoundRecord, SkillRead

    d = Spec(id="x", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="a chair").model_dump(mode="json")
    d.pop("options")
    s2 = Spec(**d)  # old spec.json without options still parses
    assert s2.options.candidates is None and s2.options.texture is False

    rs = RenderSet.model_validate({"renderer": "blender", "turntable": "renders/turntable.mp4",
                                   "contact_sheet": "renders/sheet.png"})
    assert rs.contact_sheet == "renders/sheet.png" and not hasattr(rs, "turntable")
    sk = SkillRead.model_validate({"name": "bpy_modifiers", "surfaced": True, "first_seen_turn": None})
    assert sk.surfaced and not hasattr(sk, "first_seen_turn")
    job = AgentJob.model_validate({"workspace": "/w", "prompt": "p",
                                   "images": [{"path": "renders/sheet.png", "label": "sheet"}]})
    assert job.prompt == "p" and not hasattr(job, "images")
    rec = RoundRecord.model_validate({"index": 1, "kind": "refine",
                                      "pairwise": {"a": "r00", "b": "r01", "winner": "a", "confidence": 0.9}})
    assert rec.index == 1 and not hasattr(rec, "pairwise")
