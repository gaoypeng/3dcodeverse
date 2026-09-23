"""GeminiImageModel: key rotation, the per-attempt timeout and per-image pricing (fake clients)."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

pytest.importorskip("google.genai")
from PIL import Image

from codeverse3d.models.base import ModelError
from codeverse3d.models.gemini import GeminiImageModel
from codeverse3d.models.retry import KeyPool


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _image_response() -> SimpleNamespace:
    part = SimpleNamespace(inline_data=SimpleNamespace(data=_png_bytes()))
    cand = SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"), content=SimpleNamespace(parts=[part]))
    return SimpleNamespace(prompt_feedback=None, candidates=[cand], usage_metadata=None)


class _Client:
    """Fake genai client: the shared script decides each call's outcome."""

    def __init__(self, key: str, script: list, used: list[str]):
        self._key, self._script, self._used = key, script, used
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, *, model, contents, config):
        self._used.append(self._key)
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _model(script: list, used: list[str], sleeps: list[float], *, max_attempts: int) -> tuple[GeminiImageModel, KeyPool]:
    pool = KeyPool(["key_a", "key_b"])
    m = GeminiImageModel(
        "test-image", fallback=None, pool=pool, max_attempts=max_attempts,
        sleep=sleeps.append, client_factory=lambda key: _Client(key, script, used),
    )
    return m, pool


def test_all_keys_dead_raises_the_key_error():
    used: list[str] = []
    sleeps: list[float] = []
    script = [ModelError("PERMISSION_DENIED", retryable=False, status=403),
              ModelError("PERMISSION_DENIED", retryable=False, status=403)]
    m, _pool = _model(script, used, sleeps, max_attempts=4)
    with pytest.raises(ModelError) as ei:
        m.generate_with_usage("brushed steel")
    assert ei.value.status == 403 and len(set(used)) == 2


def _captured_timeout(max_wait_s: float | None = None) -> int:
    configs: list = []

    class _Capture(_Client):
        def _generate(self, *, model, contents, config):
            configs.append(config)
            return super()._generate(model=model, contents=contents, config=config)

    used: list[str] = []
    script = [_image_response()]
    pool = KeyPool(["key_a", "key_b"])
    m = GeminiImageModel("test-image", fallback=None, pool=pool, max_attempts=1,
                         sleep=lambda s: None, client_factory=lambda key: _Capture(key, script, used))
    kw = {} if max_wait_s is None else {"max_wait_s": max_wait_s}
    m.generate_with_usage("a red cube", size=512, **kw)
    assert len(configs) == 1
    return configs[0].http_options.timeout


def test_image_budget_bounds_the_per_attempt_http_timeout():
    """A small budget clips the read timeout; no budget keeps the configured limit."""
    clipped = _captured_timeout(60.0)
    assert 20_000 <= clipped <= 60_000  # clipped to the budget, floored at 20 s
    assert _captured_timeout() == 180_000


def test_images_are_priced_by_the_generated_size():
    """A 2K request is billed at the 2K price (it was billed at the 1K rate); 512 is billed as 1K."""
    from codeverse3d.models.pricing import per_image_usd

    def cost(size: int) -> float:
        used: list[str] = []
        m, _pool = _model([_image_response()], used, [], max_attempts=1)
        m.model = "gemini-3.1-flash-image"
        m.fallback = None
        _images, usage = m.generate_with_usage("brushed steel", size=size)
        return usage.cost_usd

    assert cost(2048) == pytest.approx(per_image_usd("gemini", "gemini-3.1-flash-image", size=2048)) == pytest.approx(0.134)
    assert cost(512) == pytest.approx(0.067) and cost(1024) == pytest.approx(0.067)
