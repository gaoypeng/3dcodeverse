"""Graphics / scene with reference photos: the agent gets the photos, the judge is the likeness one."""

from __future__ import annotations

from types import SimpleNamespace

from codeverse3d.contracts.common import Track
from codeverse3d.contracts.spec import ReferenceImage
from codeverse3d.tracks.lifecycle import BaseTrack
from codeverse3d.tracks.prompting import reference_note


def _ctx(track: Track, refs: list[str], *, single_shot: bool = False) -> SimpleNamespace:
    spec = SimpleNamespace(references=[ReferenceImage(path=p, role="likeness", note="x") for p in refs],
                           backends=SimpleNamespace(judge="gemini:pro"))
    return SimpleNamespace(spec=spec, track=track, single_shot=single_shot, rubric="shader_v1",
                           policy=SimpleNamespace(judge_samples=2))


def test_graphics_note_asks_for_the_physics_not_a_silhouette(tmp_path):
    png = tmp_path / "aurora_purple_green_lake.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    note = reference_note(_ctx(Track.GRAPHICS, [str(png)]))
    assert "REFERENCE PHOTOS (1)" in note and "evenly spaced bars is not a curtain" in note
    assert "IoU" not in note and "compare_reference" not in note
    obj = reference_note(_ctx(Track.STATIC_OBJECT, [str(png)]))
    assert "IoU" in obj, "object tracks keep the silhouette contract"
    assert reference_note(_ctx(Track.GRAPHICS, [str(tmp_path / "gone.png")])) == ""


def test_make_judge_routes_photos_to_the_likeness_judge_and_a_replay_picks_the_same():
    calls: list[tuple[str, str]] = []
    services = SimpleNamespace(
        likeness_judge=lambda model, n_samples, rubric: calls.append(("likeness", rubric)) or "L",
        reference_judge=lambda model, n_samples, rubric: calls.append(("reference", rubric)) or "R",
        judge=lambda rubric, model, n_samples: calls.append(("plain", rubric)) or "P",
    )
    me = SimpleNamespace(_judge=None, services=services)
    assert BaseTrack.make_judge(me, _ctx(Track.GRAPHICS, ["/x.png"])) == "L"
    assert BaseTrack.make_judge(me, _ctx(Track.SCENE, ["/x.png"])) == "L"
    assert BaseTrack.make_judge(me, _ctx(Track.STATIC_OBJECT, ["/x.png"])) == "R"
    assert BaseTrack.make_judge(me, _ctx(Track.GRAPHICS, [])) == "P"
    assert calls[0] == ("likeness", "shader_v1")
    # a replay picks the judge the live run did: `3dcode judge` / calibration and the live run share
    # `judge_for` — a graphics or scene run with photos was re-judged by ReferenceJudge (silhouette +
    # diff pass) where the run used likeness
    from codeverse3d.cli._judge import make_judge
    from codeverse3d.judges.vlm_judge import LikenessJudge, ReferenceJudge, VlmJudge, judge_for

    assert judge_for(Track.GRAPHICS, "shader_v2", references=True) is LikenessJudge
    assert judge_for(Track.SCENE, "scene_v1", references=True) is LikenessJudge
    assert judge_for(Track.STATIC_OBJECT, "reference_v1", references=True) is ReferenceJudge
    assert judge_for(Track.STATIC_OBJECT, "reference_v1", references=False) is ReferenceJudge  # measured criteria
    assert judge_for(Track.GRAPHICS, "shader_v2", references=False) is VlmJudge
    spec = SimpleNamespace(track=Track.GRAPHICS, references=[ReferenceImage(path="/x.png", role="likeness", note="")])
    assert type(make_judge(spec, "shader_v2", "gemini:pro", 1)) is LikenessJudge
