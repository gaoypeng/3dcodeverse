"""GeminiImageModel key rotation: dead-key and free-429 semantics adopted from
``models/gemini.py`` (offline; fake clients + a 2-key pool).

Lives with the flywheel/CLI tests because this group owns
``codeverse/models/gemini_image.py``'s rotation behaviour.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

pytest.importorskip("google.genai")
from PIL import Image

from codeverse.models.base import ModelError
from codeverse.models.gemini import GeminiImageModel
from codeverse.models.retry import KeyPool


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


def test_dead_key_rotates_free_and_is_benched(monkeypatch: pytest.MonkeyPatch):
    used: list[str] = []
    sleeps: list[float] = []
    script = [ModelError("API key expired", retryable=False, status=403), _image_response()]
    m, pool = _model(script, used, sleeps, max_attempts=1)
    reports: list[tuple[str, str]] = []
    orig = pool.report
    monkeypatch.setattr(pool, "report", lambda key, outcome, **kw: (reports.append((key, outcome)), orig(key, outcome, **kw))[1])
    images, usage = m.generate_with_usage("a wooden crate texture")
    # the 403 did not consume the single attempt: a second key was tried and won
    assert len(images) == 1 and len(used) == 2 and used[0] != used[1]
    assert sleeps == []  # dead rotation never sleeps
    dead_key = used[0]
    assert (dead_key, "error") in reports  # reported at rotation time
    assert reports[-1] == (dead_key, "dead")  # benched once the sibling key succeeded
    assert (used[1], "ok") in reports


def test_429_rotation_is_free_while_untried_keys_remain():
    used: list[str] = []
    sleeps: list[float] = []
    script = [ModelError("quota", retryable=True, status=429), _image_response()]
    m, _pool = _model(script, used, sleeps, max_attempts=1)
    images, _ = m.generate_with_usage("mossy stone tiles")
    assert len(images) == 1 and len(used) == 2 and used[0] != used[1]
    assert sleeps == [0.5]  # courtesy pause only — the attempt budget was not consumed


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
    """A 2K request is billed at the 2K per-image price (it was billed at the 1K rate,
    0.067 instead of 0.134); a 512 request generates a 1K image and is billed as one."""
    from codeverse.models.pricing import per_image_usd

    def cost(size: int) -> float:
        used: list[str] = []
        m, _pool = _model([_image_response()], used, [], max_attempts=1)
        m.model = "gemini-3.1-flash-image"
        m.fallback = None
        _images, usage = m.generate_with_usage("brushed steel", size=size)
        return usage.cost_usd

    assert cost(2048) == pytest.approx(per_image_usd("gemini", "gemini-3.1-flash-image", size=2048)) == pytest.approx(0.134)
    assert cost(512) == pytest.approx(0.067) and cost(1024) == pytest.approx(0.067)
