"""Tileability: seam scoring, mirror-blend seam fixing, resizing and saving.

* ``seam_score(img)``: mean absolute difference (0..1) between the left/right and
  top/bottom wrap edges, normalised by the image's own local contrast so a noisy
  texture is not penalised for being noisy.  ≈0 = perfect wrap.
* ``make_tileable(img, border_frac)``: cross-fades each edge band with the mirrored
  opposite band (a smoothstep ramp over ``border_frac`` of the width) so the wrap
  seam disappears; content in the middle is untouched.
* ``fit_size`` / ``save_texture``: power-of-two downscale + PNG or JPEG q90.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import Judgment, Measurement, RenderSet
from codeverse3d.contracts.common import Usage
from codeverse3d.contracts.plan import AcceptanceItem, StaticPlan
from codeverse3d.contracts.spec import Spec
from codeverse3d.conventions import OBJECT_VIEWS_QUICK, ViewPreset
from codeverse3d.judges.base import JudgeInput, plan_summary
from codeverse3d.judges.rubrics import load_rubric
from codeverse3d.proc import fan_out, unique_tmp

#: seam score above which a texture is considered NOT tileable (after make_tileable)
SEAM_MAX = 0.08


def _arr(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0


def seam_score(img: Image.Image | np.ndarray) -> float:
    """Mean |Δ| across the wrap seams relative to the mean |Δ| between neighbouring
    pixel columns/rows inside the image (so 1.0 ≈ 'the seam is as strong as the
    texture's own detail'); clamped to [0, 1]."""
    a = _arr(img) if isinstance(img, Image.Image) else np.asarray(img, dtype=np.float32)
    if a.ndim == 2:
        a = a[:, :, None]
    h, w = a.shape[:2]
    if h < 4 or w < 4:
        return 0.0
    seam_lr = np.abs(a[:, 0, :] - a[:, -1, :]).mean()
    seam_tb = np.abs(a[0, :, :] - a[-1, :, :]).mean()
    # typical one-step difference inside the image (excluding the seam)
    inner_lr = np.abs(a[:, 1:, :] - a[:, :-1, :]).mean()
    inner_tb = np.abs(a[1:, :, :] - a[:-1, :, :]).mean()
    # a seam is bad when it exceeds the local detail noticeably; blend absolute + relative
    rel = 0.5 * (seam_lr / max(inner_lr, 1e-3) + seam_tb / max(inner_tb, 1e-3))
    absolute = 0.5 * (seam_lr + seam_tb)
    score = 0.5 * absolute + 0.5 * min(1.0, max(0.0, (rel - 1.0) / 8.0))
    return float(min(1.0, max(0.0, score)))


def _ramp(n: int) -> np.ndarray:
    t = (np.arange(n, dtype=np.float32) + 0.5) / n
    return t * t * (3.0 - 2.0 * t)  # smoothstep 0 → 1


def make_tileable(img: Image.Image, border_frac: float = 0.12) -> Image.Image:
    """Mirror-blend the wrap seams.  Along X: the left band is cross-faded with the
    mirrored right band and vice versa (weights symmetric so the wrap is exact);
    then the same along Y.  ``border_frac`` ∈ [0.05, 0.3] of the size."""
    border_frac = float(min(0.3, max(0.05, border_frac)))
    a = _arr(img)
    h, w = a.shape[:2]
    bw, bh = max(2, int(round(w * border_frac))), max(2, int(round(h * border_frac)))
    out = a.copy()
    # horizontal: blend left band with mirrored right band
    r = _ramp(bw)[None, :, None]  # 0 at the outer edge → 1 inside
    left, right = a[:, :bw, :], a[:, w - bw:, :]
    mir_right = right[:, ::-1, :]  # right band mirrored so its outer edge meets the left edge
    mir_left = left[:, ::-1, :]
    out[:, :bw, :] = left * (0.5 + 0.5 * r) + mir_right * (0.5 - 0.5 * r)
    out[:, w - bw:, :] = right * (0.5 + 0.5 * r[:, ::-1, :]) + mir_left * (0.5 - 0.5 * r[:, ::-1, :])
    a = out.copy()
    r = _ramp(bh)[:, None, None]
    top, bottom = a[:bh, :, :], a[h - bh:, :, :]
    mir_bottom, mir_top = bottom[::-1, :, :], top[::-1, :, :]
    out[:bh, :, :] = top * (0.5 + 0.5 * r) + mir_bottom * (0.5 - 0.5 * r)
    out[h - bh:, :, :] = bottom * (0.5 + 0.5 * r[::-1, :, :]) + mir_top * (0.5 - 0.5 * r[::-1, :, :])
    return Image.fromarray((np.clip(out, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGB")


def fit_size(img: Image.Image, size: int) -> Image.Image:
    """Square, power-of-two-ish downscale to ``size`` (never upscales)."""
    w, h = img.size
    if w != h:
        s = min(w, h)
        img = img.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    if img.size[0] > size:
        img = img.resize((size, size), Image.LANCZOS)
    return img.convert("RGB")


def save_texture(img: Image.Image, path: Path, *, fmt: str | None = None, quality: int = 90) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = (fmt or ("JPEG" if path.suffix.lower() in (".jpg", ".jpeg") else "PNG")).upper()
    if fmt == "JPEG":
        img.convert("RGB").save(path, "JPEG", quality=quality, optimize=True)
    else:
        img.convert("RGB").save(path, "PNG", optimize=True)
    return path


# ===================================================================== generate
log = logging.getLogger(__name__)

#: bump when the cached raw image semantics change (prompt composition, model config)
CACHE_VERSION = 2  # v2: seed joined the key


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


def prompt_key(prompt: str, model_id: str, size: int, seed: int | None = 0) -> str:
    # seed is in the key: seed=2 must not be served seed=1's cached pixels
    return hashlib.sha256(f"v{CACHE_VERSION}|{model_id}|{size}|{seed}|{prompt.strip()}".encode()).hexdigest()[:24]


# --------------------------------------------------------------------------- generation
def _cache_dir(cache_dir: Path | None) -> Path:
    d = (cache_dir or get_settings().cache_dir) / "textures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def generate_one(
    texture_id: str, prompt: str, out_dir: Path, image_model: Any, *, size: int = 1024, cache_dir: Path | None = None,
    use_cache: bool = True, seed: int | None = 0,
) -> TextureAsset:
    """Generate (or fetch from cache) one texture and deliver ``out_dir/<texture_id>.png``."""
    model_id = getattr(image_model, "id", getattr(image_model, "model", "image"))
    key = prompt_key(prompt, str(model_id), size, seed)
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
            # per-writer tmp (pid AND thread — candidates fan out over threads in ONE
            # process, so a pid-only name is still a race: review of PR #3); tolerant
            # replace like every other cache publish
            tmp = unique_tmp(raw_path)
            try:
                raw.save(tmp, "PNG")
                tmp.replace(raw_path)
            finally:
                tmp.unlink(missing_ok=True)
    asset.seam_score_raw = seam_score(raw)
    img = make_tileable(fit_size(raw, size))
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
                                use_cache=use_cache, seed=seed)
        except Exception as e:  # noqa: BLE001 — per-texture failure is data, not a crash
            log.warning("texture %s failed: %s: %s", tid, type(e).__name__, e)
            return TextureAsset(texture_id=tid, path=str(out_dir / f"{tid}.png"), prompt=prompts[tid],
                                prompt_hash=prompt_key(prompts[tid], str(getattr(image_model, "id", "")), size, seed),
                                size=size, error=f"{type(e).__name__}: {e}")

    ids = list(leaders.values())
    # fan_out, not a bare pool: its workers inherit the caller's context, so the image
    # model's spend is billed to the run's ledger instead of the per-process log
    # (codeverse3d.cost.context).  ``_job`` swallows its own failures, so nothing here
    # comes back as an Exception.
    assets = [a for a in fan_out(ids, _job, max_workers=max_workers, label="texture", item_name=str)
              if isinstance(a, TextureAsset)]
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


# ===================================================================== gate

MIN_OVERALL_DELTA = -0.01
MIN_MATERIALS_DELTA = 0.0


class SeamGateResult(BaseModel):
    passed: dict[str, float] = Field(default_factory=dict, description="texture_id → seam score (kept)")
    failed: dict[str, float] = Field(default_factory=dict, description="texture_id → seam score (dropped)")
    threshold: float = SEAM_MAX


def seam_gate(textures: dict[str, TextureAsset], *, max_seam: float = SEAM_MAX) -> SeamGateResult:
    res = SeamGateResult(threshold=max_seam)
    for tid, a in textures.items():
        if not a.ok:
            continue
        (res.passed if a.seam_score <= max_seam else res.failed)[tid] = round(float(a.seam_score), 4)
    return res


class GateResult(BaseModel):
    shipped: bool
    reason: str = ""
    overall_before: float | None = None
    overall_after: float | None = None
    delta: float | None = None
    materials_criterion: str = ""
    materials_before: float | None = None
    materials_after: float | None = None
    materials_delta: float | None = None
    renders_before: RenderSet | None = None
    renders_after: RenderSet | None = None
    judgment_before: Judgment | None = None
    judgment_after: Judgment | None = None
    usage: Usage = Field(default_factory=Usage)
    duration_s: float = 0.0


def judge_gate(
    spec: Spec,
    plan: StaticPlan | None,
    glb_before: Path,
    glb_after: Path,
    out_dir: Path,
    *,
    judge: Any,
    measurement: Measurement | None = None,
    views: Sequence[ViewPreset] = OBJECT_VIEWS_QUICK,
    render: Callable[..., RenderSet] | None = None,
    size: int = 512,
    min_overall_delta: float = MIN_OVERALL_DELTA,
    min_materials_delta: float = MIN_MATERIALS_DELTA,
) -> GateResult:
    """Render + judge both GLBs; decide.  ``judge`` is any object with
    ``.judge(JudgeInput) -> Judgment`` (``VlmJudge`` or a fake)."""
    t0 = time.time()
    if render is None:
        from codeverse3d.spatial.render import render_glb

        render = render_glb
    out_dir = Path(out_dir)
    rs_before = render(glb_before, out_dir / "before", views=list(views), width=size, height=size)
    rs_after = render(glb_after, out_dir / "after", views=list(views), width=size, height=size)
    acceptance: list[AcceptanceItem] = list(getattr(plan, "acceptance", []) or [])
    summary = plan_summary(plan, spec.language)
    res = GateResult(shipped=False, renders_before=rs_before, renders_after=rs_after)
    jb = judge.judge(JudgeInput(spec=spec, renders=rs_before, measurement=measurement, acceptance=acceptance,
                                plan_summary=summary, round_index=0,
                                extra_context="Texture gate: BEFORE texturing (flat materials)."))
    ja = judge.judge(JudgeInput(spec=spec, renders=rs_after, measurement=measurement, acceptance=acceptance,
                                plan_summary=summary, round_index=0,
                                extra_context="Texture gate: AFTER texturing (image textures applied)."))
    res.judgment_before, res.judgment_after = jb, ja
    res.usage = jb.usage + ja.usage
    res.duration_s = round(time.time() - t0, 2)
    if jb.degraded or ja.degraded:
        res.reason = "judge degraded on one side — not shipped"
        return res
    res.overall_before, res.overall_after = float(jb.overall), float(ja.overall)
    res.delta = round(res.overall_after - res.overall_before, 4)
    crit = load_rubric(ja.rubric).materials_criterion  # "" (articulated_v1): Δoverall alone decides
    res.materials_criterion = crit
    if crit:
        res.materials_before = float(jb.scores.get(crit, 0.0))
        res.materials_after = float(ja.scores.get(crit, 0.0))
        res.materials_delta = round(res.materials_after - res.materials_before, 4)
    ok_overall = res.delta >= min_overall_delta
    ok_mat = (res.materials_delta is None) or (res.materials_delta > min_materials_delta)
    res.shipped = bool(ok_overall and ok_mat)
    if res.shipped:
        res.reason = f"Δoverall {res.delta:+.3f} ≥ {min_overall_delta:+.2f} and {crit or 'overall'} {res.materials_delta:+.3f}" if crit \
            else f"Δoverall {res.delta:+.3f} ≥ {min_overall_delta:+.2f}"
    else:
        why = []
        if not ok_overall:
            why.append(f"Δoverall {res.delta:+.3f} < {min_overall_delta:+.2f}")
        if not ok_mat:
            why.append(f"{crit} {res.materials_delta:+.3f} did not improve")
        res.reason = "; ".join(why)
    return res
