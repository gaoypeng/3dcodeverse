"""Live Gemini checks (need keys + network): ``pytest -m live tests/models``."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.chat import (
    ChatMessage,
    ChatRequest,
    ImagePart,
)
from codeverse3d.contracts.plan import StaticPlan
from codeverse3d.models import get_chat_model

pytestmark = pytest.mark.live

MODEL = "gemini:gemini-3.7-flash"


@pytest.fixture(scope="module")
def model():
    from codeverse3d.config import get_settings

    if not get_settings().gemini_api_keys:
        pytest.skip("no gemini keys")
    return get_chat_model(MODEL)


def test_text(model):
    r = model.generate(
        ChatRequest(
            messages=[ChatMessage.user("Reply with exactly: pong")],
            thinking="off",
            max_output_tokens=200,
        )
    )
    assert "pong" in r.text.lower()
    assert r.usage.input_tokens > 0 and r.usage.output_tokens > 0 and r.usage.cost_usd > 0
    assert r.finish_reason == "STOP"


def test_structured_static_plan(model):
    r = model.generate(
        ChatRequest(
            messages=[
                ChatMessage.user(
                    "Plan 'a simple wooden stool' (round seat, 4 legs, ring stretcher). Z-up, meters, stands on ground. PascalCase part names; attach_to names another part."
                )
            ],
            system="You are a 3D planner. Output JSON matching the schema only.",
            response_schema=StaticPlan.model_json_schema(),
            temperature=0.4,
            thinking="medium",
            max_output_tokens=8000,
            label="planner",
        )
    )
    plan = StaticPlan.model_validate(r.parsed)
    assert len(plan.parts) >= 3 and plan.overall_bbox.extents[2] > 0
    assert r.usage.thoughts_tokens >= 0 and r.usage.cost_usd > 0


def test_vision(model, tmp_path):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (128, 128), "white")
    ImageDraw.Draw(img).ellipse((24, 24, 104, 104), fill="red")
    p = tmp_path / "red.png"
    img.save(p)
    r = model.generate(
        ChatRequest(
            messages=[
                ChatMessage.user(
                    "What colour is the shape? One word.",
                    images=[ImagePart(path=str(p), label="probe")],
                )
            ],
            thinking="off",
            max_output_tokens=200,
        )
    )
    assert "red" in r.text.lower()
