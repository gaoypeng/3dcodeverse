"""Generate the texture images a ``TexturePlan`` (or a scene pack) needs.

* one image per distinct prompt (parts sharing a ``texture_id`` share the image;
  identical prompts under different ids are generated once and copied),
* content cache ``<cache_dir>/textures/<sha(prompt|model|size|tile_version)>.png``
  (the raw generation, BEFORE seam fixing, so a better ``make_tileable`` can be
  re-applied without paying again),
* parallel fan-out (ThreadPool) on the shared key pool,
* every call yields a ``Usage`` (per texture and total),
* the delivered asset is ``out_dir/<texture_id>.png``: ``make_tileable`` →
  ``fit_size(size)`` → PNG, with its ``seam_score`` recorded.

``FakeImageModel`` (procedural PIL images, deterministic per prompt) lets every
test run offline with the exact same code path.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from codeverse.config import get_settings
from codeverse.contracts.common import Usage
from codeverse.texturing.tile import fit_size, make_tileable, save_texture, seam_score

log = logging.getLogger(__name__)

#: bump when the cached raw image semantics change (prompt composition, model config)
CACHE_VERSION = 1


class TextureAsset(BaseModel):
    texture_id: str
    path: str
    prompt: str
    prompt_hash: str
    seam_score: float = 0.0
    seam_score_raw: float = 0.0
    size: int = 1024
    cached: bool = False
    model: str = ""
    usage: Usage = Field(default_factory=Usage)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and Path(self.path).is_file()


class TextureSet(BaseModel):
    textures: dict[str, TextureAsset] = Field(default_factory=dict)
    usage: Usage = Field(default_factory=Usage)
    duration_s: float = 0.0

    def paths(self) -> dict[str, Path]:
        return {k: Path(v.path) for k, v in self.textures.items() if v.ok}

    def failed(self) -> dict[str, str]:
        return {k: v.error for k, v in self.textures.items() if v.error}


def prompt_key(prompt: str, model_id: str, size: int) -> str:
    return hashlib.sha256(f"v{CACHE_VERSION}|{model_id}|{size}|{prompt.strip()}".encode()).hexdigest()[:24]


# --------------------------------------------------------------------------- fake model
class FakeImageModel:
    """Deterministic procedural textures (seeded by the prompt) for offline tests.
    Records every prompt it was asked for in ``calls``."""

    provider = "fake"

    def __init__(self, model: str = "fake-image", *, latency_s: float = 0.0, fail_on: Sequence[str] = (),
                 usd_per_image: float = 0.001) -> None:
        self.model = model
        self.latency_s = latency_s
        self.fail_on = tuple(fail_on)
        self.usd_per_image = usd_per_image
        self.calls: list[str] = []
        self._lock = threading.Lock()

    @property
    def id(self) -> str:
        return f"fake-image:{self.model}"

    def generate(self, prompt: str, *, size: int = 1024, n: int = 1, seed: int | None = None,
                 reference_images: Sequence[Any] = ()) -> list[Image.Image]:
        return self.generate_with_usage(prompt, size=size, n=n, seed=seed, reference_images=reference_images)[0]

    def generate_with_usage(self, prompt: str, *, size: int = 1024, n: int = 1, seed: int | None = None,
                            reference_images: Sequence[Any] = ()) -> tuple[list[Image.Image], Usage]:
        with self._lock:
            self.calls.append(prompt)
        if any(s in prompt for s in self.fail_on):
            from codeverse.models.base import ModelError

            raise ModelError(f"fake image model refused: {prompt[:40]}", retryable=False)
        if self.latency_s:
            time.sleep(self.latency_s)
        images = [procedural_texture(prompt, size=size, seed=(seed or 0) + i) for i in range(n)]
        usage = Usage(backend="fake-image", model=self.model, input_tokens=len(prompt.split()), output_tokens=1290 * n,
                      cost_usd=self.usd_per_image * n, latency_ms=int(self.latency_s * 1000))
        return images, usage


def procedural_texture(prompt: str, *, size: int = 256, seed: int = 0) -> Image.Image:
    """Prompt-seeded noise + stripes in a prompt-derived colour.  NOT tileable on
    purpose (so tests exercise ``make_tileable``): a linear gradient is added."""
    h = int(hashlib.sha256(prompt.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(h + seed)
    base = np.array([(h >> 16) & 255, (h >> 8) & 255, h & 255], dtype=np.float32) / 255.0
    base = 0.25 + 0.6 * base
    y, x = np.mgrid[0:size, 0:size].astype(np.float32) / size
    stripes = 0.08 * np.sin(2 * np.pi * (8 * x + 3 * np.sin(2 * np.pi * y)))
    noise = 0.06 * rng.standard_normal((size, size)).astype(np.float32)
    grad = 0.25 * x  # seam-breaking gradient
    lum = (stripes + noise + grad)[:, :, None]
    img = np.clip(base[None, None, :] + lum, 0.0, 1.0)
    return Image.fromarray((img * 255).astype(np.uint8), "RGB")


# --------------------------------------------------------------------------- generation
def _cache_dir(cache_dir: Path | None) -> Path:
    d = (cache_dir or get_settings().cache_dir) / "textures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def generate_one(
    texture_id: str, prompt: str, out_dir: Path, image_model: Any, *, size: int = 1024, cache_dir: Path | None = None,
    tileable: bool = True, border_frac: float = 0.12, use_cache: bool = True, seed: int | None = 0,
) -> TextureAsset:
    """Generate (or fetch from cache) one texture and deliver ``out_dir/<texture_id>.png``."""
    model_id = getattr(image_model, "id", getattr(image_model, "model", "image"))
    key = prompt_key(prompt, str(model_id), size)
    raw_path = _cache_dir(cache_dir) / f"{key}.png"
    asset = TextureAsset(texture_id=texture_id, path=str(Path(out_dir) / f"{texture_id}.png"), prompt=prompt,
                         prompt_hash=key, size=size, model=str(model_id))
    t0 = time.time()
    if use_cache and raw_path.is_file():
        raw = Image.open(raw_path).convert("RGB")
        asset.cached = True
    else:
        images, usage = image_model.generate_with_usage(prompt, size=size, n=1, seed=seed)
        if not images:
            raise RuntimeError(f"image model returned no image for {texture_id}")
        raw = images[0].convert("RGB")
        asset.usage = usage
        if use_cache:
            tmp = raw_path.with_suffix(".tmp.png")
            raw.save(tmp, "PNG")
            tmp.replace(raw_path)
    asset.seam_score_raw = seam_score(raw)
    img = fit_size(raw, size)
    if tileable:
        img = make_tileable(img, border_frac)
    asset.seam_score = seam_score(img)
    save_texture(img, Path(asset.path))
    asset.usage.latency_ms = asset.usage.latency_ms or int((time.time() - t0) * 1000)
    return asset


def generate_textures(
    prompts: dict[str, str] | Any,
    out_dir: Path,
    image_model: Any,
    *,
    size: int = 1024,
    cache_dir: Path | None = None,
    max_workers: int = 6,
    tileable: bool = True,
    border_frac: float = 0.12,
    use_cache: bool = True,
    seed: int = 0,
) -> TextureSet:
    """``prompts`` is ``{texture_id: prompt}`` or a ``TexturePlan`` (its ``.prompts()``).
    Identical prompts are generated once and copied to every id.  Failures are
    recorded per texture (``TextureAsset.error``) — callers decide what to do."""
    if hasattr(prompts, "prompts"):
        prompts = prompts.prompts()
    prompts = {str(k): str(v) for k, v in dict(prompts).items() if str(v).strip()}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    result = TextureSet()
    if not prompts:
        return result
    # dedupe identical prompts → one generation, copies for the rest
    leaders: dict[str, str] = {}
    copies: dict[str, list[str]] = {}
    for tid, pr in prompts.items():
        k = pr.strip()
        if k in leaders:
            copies.setdefault(leaders[k], []).append(tid)
        else:
            leaders[k] = tid

    def _job(tid: str) -> TextureAsset:
        try:
            return generate_one(tid, prompts[tid], out_dir, image_model, size=size, cache_dir=cache_dir,
                                tileable=tileable, border_frac=border_frac, use_cache=use_cache, seed=seed)
        except Exception as e:  # noqa: BLE001 — per-texture failure is data, not a crash
            log.warning("texture %s failed: %s: %s", tid, type(e).__name__, e)
            return TextureAsset(texture_id=tid, path=str(out_dir / f"{tid}.png"), prompt=prompts[tid],
                                prompt_hash=prompt_key(prompts[tid], str(getattr(image_model, "id", "")), size),
                                size=size, error=f"{type(e).__name__}: {e}")

    ids = list(leaders.values())
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(ids)))) as pool:
        assets = list(pool.map(_job, ids))
    for a in assets:
        result.textures[a.texture_id] = a
        result.usage = result.usage + a.usage
        for other in copies.get(a.texture_id, []):
            dup = a.model_copy(update={"texture_id": other, "path": str(out_dir / f"{other}.png"), "usage": Usage(),
                                       "cached": True})
            if a.ok:
                shutil.copy2(a.path, dup.path)
            result.textures[other] = dup
    result.duration_s = round(time.time() - t0, 2)
    return result
