"""``GeminiImageModel`` — text (+ reference images) → PIL images via google-genai
``generate_content(response_modalities=["IMAGE"])``.

Shares the process-wide ``KeyPool`` / client cache / error classification with
``GeminiModel`` (``models/gemini.py``), including its key-rotation semantics
(``failure_outcome``): a 429 while an untried key remains is a FREE rotation
(does not consume ``max_attempts``, 0.5 s courtesy pause); auth/permission
errors classify as ``dead`` → rotate at once and bench the key only after a
sibling key proves the request itself is fine (all keys dead → raise); 5xx /
timeouts → exponential backoff; fatal finish reasons (safety) →
``ModelError(retryable=False)``.
A *fallback* model (default ``gemini-2.5-flash-image``) is tried when the primary
model is rejected (404 / 400 "not found") or every attempt on it failed retryably.

Cost: Gemini bills generated images as output tokens (≈1.1–1.3 k tokens per 1024²
image) but the published per-image price is higher than the text-output rate, so
``Usage.cost_usd`` is ``max(estimate_cost(tokens), n_images × IMAGE_USD[model])``
(approximate; see ``IMAGE_USD``).

Protocol (``ImageModel``): ``generate(prompt, *, size, n, seed, reference_images)
-> list[PIL.Image]`` and ``generate_with_usage(...) -> (images, Usage)``.  Tests
use ``codeverse.texturing.generate.FakeImageModel`` which implements the same.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from google.genai import types
from PIL import Image

from codeverse.contracts.common import Usage
from codeverse.models.base import ModelError
from codeverse.models.gemini import (
    HTTP_TIMEOUT_FLOOR_S,
    GeminiModel,
    _default_keys,
    _retry_after_s,
    classify_exception,
    failure_outcome,
    shared_pool,
)
from codeverse.models.gemini_convert import FATAL_FINISH, clip_timeout, parse_usage
from codeverse.models.keypool import MAX_WAIT_S, KeyPool
from codeverse.models.parts import Stopwatch
from codeverse.models.pricing import estimate_cost
from codeverse.models.retry import RETRY_DEADLINE_S, rotate_with_retries

log = logging.getLogger(__name__)

DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"
FALLBACK_IMAGE_MODEL = "gemini-2.5-flash-image"

#: approximate USD per generated 1024² image (floor for cost accounting)
IMAGE_USD: dict[str, float] = {
    "gemini-3.1-flash-image": 0.067,
    "gemini-2.5-flash-image": 0.039,
}

#: requested pixel size → Gemini ``image_size`` token
_IMAGE_SIZE_TOKEN: dict[int, str] = {512: "1K", 1024: "1K", 2048: "2K", 4096: "4K"}


@runtime_checkable
class ImageModel(Protocol):
    """Text-to-image backend.  Implementations must be thread-safe."""

    model: str

    @property
    def id(self) -> str: ...

    def generate(
        self,
        prompt: str,
        *,
        size: int = 1024,
        n: int = 1,
        seed: int | None = None,
        reference_images: Sequence[Image.Image | Path | str] = (),
    ) -> list[Image.Image]: ...

    def generate_with_usage(
        self,
        prompt: str,
        *,
        size: int = 1024,
        n: int = 1,
        seed: int | None = None,
        reference_images: Sequence[Image.Image | Path | str] = (),
    ) -> tuple[list[Image.Image], Usage]: ...


def image_cost(model: str, usage: Usage, n_images: int) -> float:
    """Token-priced cost floored by the per-image price (approximate)."""
    token_cost = estimate_cost("gemini", model, usage)
    per_image = IMAGE_USD.get(model.strip().lower(), 0.0)
    return max(token_cost, per_image * max(0, n_images))


def _to_part(ref: Image.Image | Path | str) -> types.Part:
    if isinstance(ref, Image.Image):
        buf = io.BytesIO()
        ref.convert("RGB").save(buf, format="PNG")
        return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png")
    p = Path(ref)
    if not p.is_file():
        raise ModelError(f"reference image not found: {p}")
    mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return types.Part.from_bytes(data=p.read_bytes(), mime_type=mime)


def _is_model_missing(err: ModelError) -> bool:
    s = str(err).lower()
    return err.status == 404 or (err.status == 400 and ("not found" in s or "not supported" in s))


def _record(usage: Usage, n_images: int) -> None:
    """One ledger row per image batch (the image model is not a ChatModel, so
    ``models.registry`` cannot meter it).  Never raises."""
    try:
        from codeverse.cost import record_call
        from codeverse.cost.types import Role, Stage

        record_call(usage, stage=Stage.TEXTURE, role=Role.IMAGE, label="image",
                    backend="gemini-image", model=usage.model, n_calls=max(1, n_images))
    except Exception:  # noqa: BLE001 - accounting must never break a texture pass
        pass


class GeminiImageModel:
    """See module docstring.  ``model`` is the primary; ``fallback`` the second try."""

    provider = "gemini"

    def __init__(
        self,
        model: str = DEFAULT_IMAGE_MODEL,
        *,
        fallback: str | None = FALLBACK_IMAGE_MODEL,
        keys: list[str] | None = None,
        pool: KeyPool | None = None,
        timeout_s: float = 180.0,
        max_attempts: int = 4,
        base_delay: float = 1.0,
        max_delay: float = MAX_WAIT_S,
        hedge: int = 1,
        sleep: Callable[[float], None] = time.sleep,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.model = model
        self.fallback = fallback if fallback and fallback != model else None
        #: no 503 hedge by default: an image is billed per image, so a hedge that
        #: lands twice pays for two of them (the chat models default to 2)
        self.hedge = max(1, int(hedge))
        keys = list(keys) if keys is not None else _default_keys()
        if pool is None and not keys:
            raise ModelError("no Gemini API keys configured (GEMINI_API_KEYS / ~/.config/astra3d/gemini_keys.env)")
        self.pool = pool or shared_pool(keys)
        self.timeout_s = timeout_s
        self.max_attempts = max(1, max_attempts)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._sleep = sleep
        # reuse GeminiModel's cached clients (same key → same genai.Client)
        self._clients = GeminiModel(model, pool=self.pool, timeout_s=timeout_s, client_factory=client_factory)
        self.storm_gate = self._clients.storm_gate
        self._lock = threading.Lock()
        self._primary_dead = False

    @property
    def id(self) -> str:
        return f"gemini-image:{self.model}"

    # ------------------------------------------------------------------ API
    def generate(
        self,
        prompt: str,
        *,
        size: int = 1024,
        n: int = 1,
        seed: int | None = None,
        reference_images: Sequence[Image.Image | Path | str] = (),
        max_wait_s: float | None = None,
    ) -> list[Image.Image]:
        images, _ = self.generate_with_usage(prompt, size=size, n=n, seed=seed,
                                             reference_images=reference_images, max_wait_s=max_wait_s)
        return images

    def generate_with_usage(
        self,
        prompt: str,
        *,
        size: int = 1024,
        n: int = 1,
        seed: int | None = None,
        reference_images: Sequence[Image.Image | Path | str] = (),
        max_wait_s: float | None = None,
    ) -> tuple[list[Image.Image], Usage]:
        """``max_wait_s`` is the caller's retry budget for ONE image (each of the
        ``n`` images gets its own window, and a fallback model too), exactly like
        ``ChatRequest.max_wait_s`` clips ``GeminiModel.generate``; ``None`` = the
        full ``retry.RETRY_DEADLINE_S`` (900 s).  Kept off the ``ImageModel``
        protocol so fakes stay conformant; callers that hold a deadline pass it."""
        if not prompt.strip():
            raise ModelError("empty image prompt")
        if n < 1:
            raise ModelError("n must be >= 1")
        images: list[Image.Image] = []
        usage = Usage(backend="gemini-image", model=self.model)
        for i in range(n):
            got, u = self._one(prompt, size=size, seed=None if seed is None else seed + i,
                               refs=reference_images, max_wait_s=max_wait_s)
            images.extend(got)
            usage = usage + u
        if len(images) > n:
            images = images[:n]
        _record(usage, len(images))
        return images, usage

    # ------------------------------------------------------------------ internals
    def _models(self) -> list[str]:
        with self._lock:
            dead = self._primary_dead
        order = [self.model] if not dead else []
        if self.fallback:
            order.append(self.fallback)
        if not order:
            order = [self.model]
        return order

    def _one(
        self, prompt: str, *, size: int, seed: int | None,
        refs: Sequence[Image.Image | Path | str], max_wait_s: float | None = None,
    ) -> tuple[list[Image.Image], Usage]:
        last: ModelError | None = None
        for model in self._models():
            try:
                return self._attempts(model, prompt, size=size, seed=seed, refs=refs,
                                      max_wait_s=max_wait_s)
            except ModelError as err:
                last = err
                if model == self.model and self.fallback and (err.retryable or _is_model_missing(err)):
                    if _is_model_missing(err):
                        with self._lock:
                            self._primary_dead = True
                    log.warning("image model %s failed (%s); falling back to %s", model, err, self.fallback)
                    continue
                raise
        assert last is not None
        raise last

    def _attempts(
        self, model: str, prompt: str, *, size: int, seed: int | None,
        refs: Sequence[Image.Image | Path | str], max_wait_s: float | None = None,
    ) -> tuple[list[Image.Image], Usage]:
        parts: list[types.Part] = [_to_part(r) for r in refs]
        parts.append(types.Part.from_text(text=prompt))
        contents = [types.Content(role="user", parts=parts)]
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio="1:1", image_size=_IMAGE_SIZE_TOKEN.get(int(size), "1K")),
            seed=seed,
            http_options=types.HttpOptions(timeout=int(self.timeout_s * 1000)),
        )
        # the caller's budget clips the retry deadline, never extends it (the same
        # contract as GeminiModel.generate with ChatRequest.max_wait_s), and each
        # attempt's HTTP read timeout is the REMAINING budget, floored like
        # GeminiModel._attempt_config — a 60 s image budget no longer holds a
        # 180 s socket past its deadline.
        budget = RETRY_DEADLINE_S if max_wait_s is None else min(RETRY_DEADLINE_S, float(max_wait_s))
        deadline = time.monotonic() + budget

        def _attempt_config() -> types.GenerateContentConfig:
            remaining = max(HTTP_TIMEOUT_FLOOR_S, deadline - time.monotonic())
            return clip_timeout(config, int(min(self.timeout_s, remaining) * 1000))

        return rotate_with_retries(
            self.pool,
            lambda key: self._call(key, model, contents, _attempt_config(), size),
            classify=classify_exception,
            outcome_of=failure_outcome,
            max_attempts=self.max_attempts,
            base_delay=self.base_delay,
            max_delay=self.max_delay,
            max_total_s=budget,
            hedge=self.hedge,
            sleep=self._sleep,
            retry_after=_retry_after_s,
            tokens_of=lambda r: r[1].input_tokens,
            storm_gate=self.storm_gate,
            label=f"image {model}",
        )

    def _call(
        self, key: str, model: str, contents: list[types.Content], config: types.GenerateContentConfig, size: int
    ) -> tuple[list[Image.Image], Usage]:
        client = self._clients._client(key)
        with Stopwatch() as sw:
            resp = client.models.generate_content(model=model, contents=contents, config=config)
        pf = resp.prompt_feedback
        if pf is not None and pf.block_reason:
            raise ModelError(f"image prompt blocked: {pf.block_reason}", retryable=False)
        if not resp.candidates:
            raise ModelError("image model returned no candidates", retryable=True)
        cand = resp.candidates[0]
        finish = str(cand.finish_reason.name if cand.finish_reason is not None else "")
        images: list[Image.Image] = []
        for part in (cand.content.parts if cand.content and cand.content.parts else []):
            blob = getattr(part, "inline_data", None)
            if blob is not None and blob.data:
                img = Image.open(io.BytesIO(blob.data))
                img.load()
                images.append(img.convert("RGB"))
        if not images:
            if finish in FATAL_FINISH:
                raise ModelError(f"image generation refused (finish_reason={finish})", retryable=False)
            raise ModelError(f"image model returned no image (finish_reason={finish or 'unknown'})", retryable=True)
        usage = parse_usage(resp, model)
        usage.backend = "gemini-image"
        usage.latency_ms = sw.ms
        usage.cost_usd = image_cost(model, usage, len(images))
        if int(size) not in (0, 1024) and images[0].size != (size, size):
            images = [im.resize((int(size), int(size)), Image.LANCZOS) for im in images]
        return images, usage
