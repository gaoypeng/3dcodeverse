"""Captioner: fake ChatModel (offline) + one optional live Gemini call."""

from __future__ import annotations

import json

import pytest

from codeverse.contracts.chat import ChatRequest, ChatResponse, ImagePart
from codeverse.contracts.common import Usage
from codeverse.flywheel.captions import CaptionError, Captions, caption_sample, validate_captions
from codeverse.flywheel.record import load_record

GOOD = {
    "detailed": "A four-legged wooden dining chair with a flat square seat and a tall slatted backrest.",
    "instruction": "Write a Blender Python script that builds a simple wooden dining chair with four legs and a slatted back.",
    "factory": "Create a box for the seat, four tapered box legs under its corners, two uprights and three horizontal slats for the back, then apply a wood material.",
}


class FakeModel:
    provider, model = "fake", "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[ChatRequest] = []

    @property
    def id(self):
        return "fake:fake"

    def supports_vision(self):
        return True

    def generate(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        data = self.replies.pop(0)
        return ChatResponse(text=json.dumps(data), parsed=data, usage=Usage(cost_usd=0.001))


def test_caption_sample_stores_and_writes(fake_run):
    ws, rec = fake_run
    m = FakeModel([GOOD])
    caps = caption_sample(ws, rec, "fake:fake", model=m)
    assert caps.detailed.startswith("A four-legged")
    req = m.requests[0]
    assert req.response_schema is not None and "Blender Python script" in req.system
    imgs = [p for p in req.messages[0].parts if isinstance(p, ImagePart)]
    assert len(imgs) == 3  # sheet + 2 views
    assert "round 1" in req.messages[0].text  # best-round code
    assert (ws.root / "captions.json").is_file()
    again = load_record(ws)
    assert again.extra["captions"]["factory"] == GOOD["factory"]
    assert again.extra["captions"]["provenance"]["captioner"] == "fake:fake"
    assert again.extra["captions"]["provenance"]["cost_usd"] == pytest.approx(0.001)


def test_caption_retry_then_fail(fake_run):
    ws, rec = fake_run
    bad = dict(GOOD, detailed="Made with bpy.ops primitives: a chair with four legs and a backrest.")
    m = FakeModel([bad, GOOD])
    caps = caption_sample(ws, rec, "fake:fake", model=m)
    assert caps.detailed == GOOD["detailed"] and len(m.requests) == 2
    assert "bpy" in m.requests[1].messages[-1].text
    m2 = FakeModel([bad, bad])
    with pytest.raises(CaptionError):
        caption_sample(ws, rec, "fake:fake", model=m2)


def test_validate_rules():
    from codeverse.contracts.common import Language

    c = Captions(**GOOD)
    assert validate_captions(c, Language.BLENDER) == []
    c2 = Captions(**dict(GOOD, instruction="Please model a wooden chair with four legs for me."))
    assert any("target language" in p for p in validate_captions(c2, Language.BLENDER))


@pytest.mark.live
def test_caption_live_gemini(fake_run):
    from codeverse.config import get_settings

    if not get_settings().gemini_api_keys:
        pytest.skip("no gemini keys")
    ws, rec = fake_run
    caps = caption_sample(ws, rec, "gemini:gemini-3.7-flash")
    assert len(caps.detailed) > 20 and "blender" in caps.instruction.lower()
