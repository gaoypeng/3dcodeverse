"""Live Gemini checks (need keys + network): ``pytest -m live tests/models``."""

from __future__ import annotations

import json

import pytest

from codeverse.contracts.chat import (
    ChatMessage,
    ChatRequest,
    ImagePart,
    TextPart,
    ToolResultPart,
    ToolSpec,
)
from codeverse.contracts.plan import StaticPlan
from codeverse.models import get_chat_model

pytestmark = pytest.mark.live

MODEL = "gemini:gemini-3.7-flash"


@pytest.fixture(scope="module")
def model():
    from codeverse.config import get_settings

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


def test_tool_roundtrip(model):
    tool = ToolSpec(
        name="measure",
        description="Measure the bounding box (meters) of the built object; returns extents x,y,z.",
        parameters={
            "type": "object",
            "properties": {"part": {"type": "string", "description": "part name or 'all'"}},
            "required": ["part"],
        },
    )
    msgs = [
        ChatMessage.user(
            "Use the measure tool on the whole object, then report its height in cm. Never guess."
        )
    ]
    r = model.generate(
        ChatRequest(messages=msgs, tools=[tool], thinking="low", max_output_tokens=2000)
    )
    assert r.tool_calls and r.tool_calls[0].name == "measure"
    call = r.tool_calls[0]
    parts = ([TextPart(text=r.text)] if r.text else []) + [call]
    msgs.append(ChatMessage(role="assistant", parts=parts))
    msgs.append(
        ChatMessage(
            role="tool",
            parts=[
                ToolResultPart(
                    call_id=call.id,
                    name="measure",
                    content=json.dumps({"extents_m": [0.35, 0.35, 0.45]}),
                )
            ],
        )
    )
    r2 = model.generate(
        ChatRequest(messages=msgs, tools=[tool], thinking="low", max_output_tokens=2000)
    )
    assert "45" in r2.text and not r2.tool_calls
