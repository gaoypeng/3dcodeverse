"""Shared fixtures: PIL-drawn renders, a Spec/JudgeInput, and a FakeChatModel."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from codeverse3d.contracts.artifacts import Measurement, PartMeasure, RenderSet, RenderView
from codeverse3d.contracts.chat import ChatRequest, ImagePart
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import AcceptanceItem
from codeverse3d.contracts.spec import Spec
from codeverse3d.judges.base import JudgeInput
from codeverse3d.judges.rubrics import Rubric
from tests.orchestrator_tracks.fakes import FakeChatModel as _FakeChatModel

VIEW_NAMES = ("front_right_high", "back_left_high", "front", "top")


def draw_chair(path: Path, *, az_hint: int = 0, size: int = 512, legs: int = 4, color=(150, 90, 40)) -> Path:
    """A crude chair silhouette so a real VLM has something to judge."""
    im = Image.new("RGB", (size, size), (235, 235, 230))
    d = ImageDraw.Draw(im)
    s = size / 512
    d.rectangle([150 * s, 260 * s, 360 * s, 290 * s], fill=color)  # seat
    d.rectangle([150 * s, 100 * s, 180 * s, 260 * s], fill=color)  # back post
    d.rectangle([330 * s, 100 * s, 360 * s, 260 * s], fill=color)
    d.rectangle([150 * s, 100 * s, 360 * s, 130 * s], fill=color)  # back rail
    xs = [160, 340] if legs == 2 else [160, 230, 280, 340][: legs]
    for x in xs:
        d.rectangle([x * s, 290 * s, (x + 20) * s, 440 * s], fill=color)
    d.text((8, 8), f"az {az_hint}", fill=(0, 0, 0))
    im.save(path)
    return path


def make_renders(tmp: Path, *, n: int = 4, sheet: bool = True) -> RenderSet:
    tmp.mkdir(parents=True, exist_ok=True)
    views = []
    for i, name in enumerate(VIEW_NAMES[:n]):
        p = draw_chair(tmp / f"view_{name}.png", az_hint=i * 90)
        views.append(RenderView(name=name, path=str(p), width=512, height=512))
    sheet_path = None
    if sheet:
        im = Image.new("RGB", (1024, 1024), (200, 200, 200))
        for i, v in enumerate(views):
            with Image.open(v.path) as tile:
                im.paste(tile.resize((512, 512)), ((i % 2) * 512, (i // 2) * 512))
        sheet_path = tmp / "sheet.png"
        im.save(sheet_path)
    return RenderSet(views=views, contact_sheet=str(sheet_path) if sheet_path else None, renderer="fake")


def make_measurement() -> Measurement:
    return Measurement(
        bbox_min=(-0.25, 0.0, -0.25), bbox_max=(0.25, 0.9, 0.25), extents=(0.5, 0.9, 0.5), center=(0, 0.45, 0),
        tri_count=1200, n_meshes=6, n_islands=1,
        parts=[PartMeasure(name="Seat", bbox_min=(-0.25, 0.42, -0.25), bbox_max=(0.25, 0.46, 0.25), tri_count=12),
               PartMeasure(name="Backrest", bbox_min=(-0.25, 0.46, -0.25), bbox_max=(0.25, 0.9, -0.2), tri_count=12)],
        ground_gap_m=0.0, footprint_offset_m=0.0, materials=2,
    )


def make_spec(**kw: Any) -> Spec:
    base = dict(id="t1", track=Track.STATIC_OBJECT, language=Language.BLENDER,
                prompt="A simple wooden dining chair with four legs and a slatted backrest.")
    base.update(kw)
    return Spec(**base)


ACCEPTANCE = [
    AcceptanceItem(id="A1", text="has exactly four legs touching the ground", how="measure", priority="must"),
    AcceptanceItem(id="A2", text="backrest is slatted (≥3 slats)", how="visual", priority="should"),
]


@pytest.fixture
def renders(tmp_path: Path) -> RenderSet:
    return make_renders(tmp_path / "renders")


@pytest.fixture
def judge_input(renders: RenderSet) -> JudgeInput:
    return JudgeInput(spec=make_spec(), renders=renders, measurement=make_measurement(), acceptance=list(ACCEPTANCE),
                      plan_summary="parts: Seat, Backrest, LegFrontLeft, LegFrontRight, LegBackLeft, LegBackRight")


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "cache"


# --------------------------------------------------------------------------- fake model
def good_reply(rubric: Rubric, acceptance_ids: list[str], score: float = 0.8, *, accept: bool = True,
               overrides: dict[str, float] | None = None) -> dict[str, Any]:
    overrides = overrides or {}
    return {
        "criteria": {c.id: {"score": overrides.get(c.id, score), "evidence": f"VIEW 1: {c.id} ok"}
                     for c in rubric.visual_criteria()},
        "summary": "A recognisable chair.",
        "strengths": ["reads as a chair"],
        "issues": [{"target": "Seat", "kind": "detail", "severity": "minor", "detail": "plain slab", "evidence": "VIEW 1"}],
        "improvement_plan": [{"target": "Seat", "kind": "geometry", "instruction": "bevel the seat edges 5 mm",
                              "priority": 1, "expected_gain": 0.03}],
        "acceptance": {aid: {"verified": accept, "evidence": "VIEW 3"} for aid in acceptance_ids},
    }


# the shared fake, with the judge tests' id, price and token counts
FakeChatModel = partial(_FakeChatModel, model="fake-1", cost=0.001, tokens=(1000, 300))


def image_parts(req: ChatRequest) -> list[ImagePart]:
    return [p for m in req.messages for p in m.parts if isinstance(p, ImagePart)]
