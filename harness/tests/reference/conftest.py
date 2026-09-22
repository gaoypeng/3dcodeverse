"""Fakes for the reference package: a scripted chat model and an image model."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from codeverse3d.contracts.chat import ChatRequest, ChatResponse
from codeverse3d.contracts.common import Language, Track, Usage
from codeverse3d.contracts.spec import Constraints, Spec
from codeverse3d.models.base import ModelError


class FakeChat:
    """ChatModel stand-in routed by ``request.label`` (``by_label``), else a queue."""

    provider = "fake"
    model = "fake-1"

    def __init__(self, by_label: dict[str, list[Any]] | None = None, *, default: Any = None):
        self.by_label = {k: list(v) for k, v in (by_label or {}).items()}
        self.default = default
        self.requests: list[ChatRequest] = []
        self._lock = threading.Lock()

    @property
    def id(self) -> str:
        return "fake:fake-1"

    def generate(self, request: ChatRequest) -> ChatResponse:
        with self._lock:
            self.requests.append(request)
            reply = self.default
            for key in sorted(self.by_label, key=len, reverse=True):
                if key in (request.label or "") and self.by_label[key]:
                    reply = self.by_label[key].pop(0)
                    break
        if isinstance(reply, Exception):
            raise reply
        if reply is None:
            raise ModelError("no reply configured for " + (request.label or "?"))
        return ChatResponse(parsed=reply, text="", usage=Usage(backend="fake", model="fake-1", cost_usd=0.001))


class FakeImageModel:
    """ImageModel stand-in: paints a solid rectangle so the silhouette code has work."""

    provider = "fake"
    model = "fake-image"

    def __init__(self, *, fail: bool = False, colour=(120, 80, 40)):
        self.fail = fail
        self.colour = colour
        self.prompts: list[str] = []

    @property
    def id(self) -> str:
        return "fake-image:fake-image"

    def generate(self, prompt: str, **kw: Any) -> list[Image.Image]:
        return self.generate_with_usage(prompt, **kw)[0]

    def generate_with_usage(self, prompt: str, *, size: int = 1024, n: int = 1, seed: int | None = None,
                            reference_images=()) -> tuple[list[Image.Image], Usage]:
        self.prompts.append(prompt)
        if self.fail:
            raise ModelError("image model exploded", retryable=False)
        out = []
        for _ in range(n):
            im = Image.new("RGB", (size, size), (240, 240, 238))
            ImageDraw.Draw(im).rectangle([size * 0.3, size * 0.2, size * 0.7, size * 0.85], fill=self.colour)
            out.append(im)
        return out, Usage(backend="fake-image", model="fake-image", cost_usd=0.067 * n)


def make_spec(**kw: Any) -> Spec:
    base: dict[str, Any] = dict(
        id="t1", track=Track.STATIC_OBJECT, language=Language.BLENDER,
        prompt="a hand-crank coffee grinder with a square wooden box body and a cast-iron cone hopper",
        constraints=Constraints(dimensions_m={"width": 0.14, "depth": 0.14, "height": 0.30},
                                must_have=["a crank handle", "a front drawer"]),
    )
    base.update(kw)
    return Spec(**base)


GOOD_GATE = {
    "depicted_object": "hand-crank coffee grinder", "shows_requested_object": True, "single_object": True,
    "plain_background": True, "no_text_or_watermark": True, "is_photo_collage": False,
    "contradictions": [], "reason": "matches the brief",
}

PROMPT_PLAN = {
    "object_name": "hand-crank coffee grinder",
    "subject": "a hand-crank coffee grinder, square walnut box body, cast-iron cone hopper, steel crank",
    "views": [{"view": "three_quarter", "view_note": "crank and hopper"},
              {"view": "front", "view_note": "drawer front"}],
}


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "cache"
    d.mkdir()
    return d
