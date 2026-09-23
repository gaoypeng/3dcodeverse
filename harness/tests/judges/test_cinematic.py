"""The offline-frame review (``judges.cinematic``): evidence first, arithmetic in code."""
from pathlib import Path

import pytest
from PIL import Image, UnidentifiedImageError

from codeverse3d.contracts.artifacts import RenderSet, RenderView
from codeverse3d.contracts.chat import ImagePart
from codeverse3d.judges.cinematic import review_frames
from codeverse3d.judges.rubrics import load_rubric
from tests.orchestrator_tracks.fakes import FakeChatModel


def _verdict(defect: str = "") -> dict:
    rubric = load_rubric("cinematic_v1")
    return {
        "summary": "Evidence-bound review.",
        "criteria": {c.id: {"score": .9, "evidence": "Hero"} for c in rubric.criteria},
        "defects": {d.id: {"present": d.id == defect, "evidence": "Hero"} for d in rubric.defects},
    }


def test_missing_or_corrupt_pixels_never_call_the_model(tmp_path: Path):
    model = FakeChatModel(default=_verdict())
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="at least one"):
        review_frames("scene", RenderSet(), chat_model=model)
    with pytest.raises(FileNotFoundError):
        review_frames("scene", RenderSet(views=[RenderView(name="Hero", path=str(tmp_path / "absent.png"))]),
                      chat_model=model)
    with pytest.raises(UnidentifiedImageError):
        review_frames("scene", RenderSet(views=[RenderView(name="Hero", path=str(corrupt))]), chat_model=model)
    assert not model.requests


def test_selected_views_renderer_provenance_and_the_primitive_cap(tmp_path: Path):
    image = tmp_path / "frame.png"
    Image.new("RGB", (8, 8), "white").save(image)
    renders = RenderSet(renderer="Blender Cycles CUDA", views=[
        RenderView(name="Hidden", path="missing.png", judge=False),
        RenderView(name="Hero", path=str(image), judge=True),
        RenderView(name="Detail", path=str(image), judge=True),
    ])
    model = FakeChatModel(default=_verdict())
    result = review_frames("scene", renders, chat_model=model, max_views=1)
    assert result.overall == pytest.approx(.9) and result.judge_backend == model.id
    request = model.requests[0]
    assert [p.label for p in request.messages[0].parts if isinstance(p, ImagePart)] == ["Hero"]
    assert "Blender Cycles CUDA" in request.messages[0].text and "Three.js" not in request.messages[0].text
    capped = review_frames("scene", RenderSet(views=[RenderView(name="Hero", path=str(image))]),
                           chat_model=FakeChatModel(default=_verdict("primitive_assembly")))
    assert capped.overall == pytest.approx(.68) and not capped.passed
