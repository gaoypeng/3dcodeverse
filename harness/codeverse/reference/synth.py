"""prompt → reference image(s): give the pipeline something to LOOK at.

``synth_reference(spec, model=…, image_model=…)`` does three things:

1. ONE text call turns the brief into an image-prompt ``subject`` (+ per-view
   notes).  The camera/backdrop clause is **harness-owned**
   (``prompts.STUDIO_SUFFIX``) so every reference is a neutral studio product
   shot with the whole object in frame — the only kind of picture a silhouette
   IoU can be computed against.
2. The image model renders ``n_views`` of them: the 3/4 shot (part inventory) first,
   then a straight-on FRONT elevation — that second one becomes the silhouette
   ``target``, because the harness measures IoU against a straight-on render and
   the two cameras have to agree (:mod:`.attach`).
3. Every candidate goes through the plausibility gate (:mod:`.gate`).  A
   candidate that fails is discarded; if none survives, the caller gets an empty
   set and falls back to running with no reference at all.  A survivor whose own
   proportions contradict the brief's dimensions is kept but flagged
   (:mod:`.proportions`) so no numeric proportion signal is taken from it.

Everything is cached by brief+models+prompt hash under
``~/.cache/codeverse/references``.  Cost: 1 cheap text call + ``n_views`` images
(≈ $0.067 each) + ``n_views`` cheap vision calls ≈ $0.15 for the default 2 views.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from codeverse.contracts.chat import ChatMessage, ChatRequest
from codeverse.contracts.common import Usage
from codeverse.contracts.spec import Spec
from codeverse.models.base import ModelError
from codeverse.models.schema_utils import parse_json_lenient
from codeverse.reference import cache as C
from codeverse.reference.gate import check_plausible
from codeverse.reference.prompts import (
    IMAGE_PROMPT_SYSTEM,
    IMAGE_PROMPT_USER,
    STUDIO_SUFFIX,
    VIEW_CLAUSE,
    VIEW_ORDER,
)
from codeverse.reference.proportions import dimension_conflict
from codeverse.reference.spec_text import brief_text
from codeverse.reference.types import ReferenceSet, ReferenceView

log = logging.getLogger(__name__)

MAX_VIEWS = len(VIEW_ORDER)
DEFAULT_SIZE = 1024


class ViewPrompt(BaseModel):
    view: str
    view_note: str = ""


class ImagePromptPlan(BaseModel):
    object_name: str = ""
    subject: str = ""
    views: list[ViewPrompt] = Field(default_factory=list)


def views_for(n_views: int) -> list[str]:
    return list(VIEW_ORDER[: max(1, min(int(n_views), MAX_VIEWS))])


def compose_image_prompt(subject: str, view: str, note: str = "") -> str:
    """Harness-owned composition: subject + view clause + note + studio suffix."""
    subject = " ".join(subject.strip().rstrip(".,;").split())
    clause = VIEW_CLAUSE.get(view, VIEW_CLAUSE["three_quarter"])
    note = " ".join(note.strip().rstrip(".,;").split())
    bits = [subject, clause, *( [f"the picture must make readable: {note}"] if note else [] ), STUDIO_SUFFIX]
    return ", ".join(bits)


def _fallback_plan(spec: Spec, view_names: list[str]) -> ImagePromptPlan:
    """No text model (or it failed) → use the brief's own words as the subject."""
    return ImagePromptPlan(object_name=spec.prompt.strip()[:60],
                           subject=brief_text(spec).replace("\n", ", "),
                           views=[ViewPrompt(view=v) for v in view_names])


def image_prompt_plan(spec: Spec, view_names: list[str], *, model: Any, temperature: float = 0.3) -> tuple[ImagePromptPlan, Usage]:
    """ONE structured text call: brief → subject sentence + per-view notes."""
    if model is None:
        return _fallback_plan(spec, view_names), Usage()
    text = IMAGE_PROMPT_USER.format(brief=brief_text(spec), n_views=len(view_names), views=", ".join(view_names))
    req = ChatRequest(messages=[ChatMessage.user(text)], system=IMAGE_PROMPT_SYSTEM,
                      response_schema=ImagePromptPlan.model_json_schema(), temperature=temperature,
                      thinking="low", max_output_tokens=65_536, max_wait_s=900.0, label="reference_prompt")
    try:
        resp = model.generate(req)
    except ModelError as e:
        log.warning("reference prompt writer failed (%s); using the brief verbatim", e)
        return _fallback_plan(spec, view_names), Usage()
    payload = resp.parsed if resp.parsed is not None else parse_json_lenient(resp.text)
    try:
        plan = ImagePromptPlan.model_validate(payload)
    except (ValidationError, TypeError) as e:
        log.warning("reference prompt writer returned invalid JSON (%s); using the brief verbatim", e)
        return _fallback_plan(spec, view_names), resp.usage
    if not plan.subject.strip():
        plan.subject = brief_text(spec).replace("\n", ", ")
    by_view = {v.view.strip().lower(): v for v in plan.views}
    plan.views = [by_view.get(v, ViewPrompt(view=v)) for v in view_names]
    for v, name in zip(plan.views, view_names, strict=True):
        v.view = name
    return plan, resp.usage


def synth_reference(
    spec: Spec,
    *,
    model: Any = None,
    image_model: Any = None,
    n_views: int = 2,
    model_id: str = "",
    cache_dir: Path | None = None,
    use_cache: bool = True,
    out_dir: Path | None = None,
    size: int = DEFAULT_SIZE,
) -> ReferenceSet:
    """Synthesize + validate reference images for ``spec``.  Never raises.

    ``out_dir`` (a run's ``artifacts/reference``) receives a copy of every
    accepted image so the run is self-contained; the cache keeps the originals.
    """
    view_names = views_for(n_views)
    text_id = model_id or getattr(model, "id", "") or ""
    image_id = getattr(image_model, "id", "") or getattr(image_model, "model", "") or ""
    key = C.cache_key(spec, n_views=len(view_names), text_model=text_id, image_model=image_id)
    if use_cache:
        hit = C.load(key, cache_dir=cache_dir)
        if hit is not None:
            _refresh_conflicts(spec, hit)  # the proportion check may have changed since it was cached
            log.info("reference cache hit %s (%s)", key, hit.summary())
            return _publish(hit, out_dir)
    rs = ReferenceSet(prompt=spec.prompt, key=key, text_model=text_id, image_model=image_id, source="fresh")
    if image_model is None:
        rs.error = "no image model configured"
        return rs
    plan, usage = image_prompt_plan(spec, view_names, model=model)
    rs.usage, rs.object_name, rs.subject = usage, plan.object_name.strip()[:80], plan.subject.strip()
    out = C.image_dir(key, cache_dir=cache_dir)
    for i, vp in enumerate(plan.views):
        prompt = compose_image_prompt(plan.subject, vp.view, vp.view_note)
        view = ReferenceView(path="", view=vp.view, image_prompt=prompt)  # type: ignore[arg-type]
        try:
            images, u = image_model.generate_with_usage(prompt, size=size, n=1, seed=spec.seed + i)
        except Exception as e:  # noqa: BLE001 — a reference is optional; it must never fail the run
            log.warning("reference image %s failed: %s", vp.view, e)
            rs.error = rs.error or f"{type(e).__name__}: {e}"
            continue
        rs.usage = rs.usage + u
        if not images:
            continue
        path = out / f"ref_{i}_{vp.view}.png"
        images[0].save(path)
        view.path = str(path)
        verdict, gu = check_plausible(path, spec, model=model, model_id=text_id) if model is not None else (None, Usage())
        rs.usage = rs.usage + gu
        if verdict is not None:
            view.verdict, view.accepted = verdict, verdict.ok
            if verdict.ok:
                info = dimension_conflict(spec, path)
                view.aspect, view.dimension_conflict = info.get("image_aspect"), bool(info.get("conflict"))
        else:  # no text model to verify with → an unverified image is NOT used
            view.accepted = False
        rs.views.append(view)
    if use_cache and rs.views:
        C.store(rs, cache_dir=cache_dir)
    log.info("reference synthesis %s: %s", key, rs.summary())
    return _publish(rs, out_dir)


def _refresh_conflicts(spec: Spec, rs: ReferenceSet) -> None:
    """Recompute the proportion flags of a cached set against today's rules."""
    for v in rs.views:
        if not v.accepted or not v.path or not Path(v.path).is_file():
            continue
        info = dimension_conflict(spec, v.path)
        v.aspect, v.dimension_conflict = info.get("image_aspect"), bool(info.get("conflict"))


def _publish(rs: ReferenceSet, out_dir: Path | None) -> ReferenceSet:
    """Copy accepted images into the run and repoint their paths (idempotent)."""
    if out_dir is None or not rs.accepted:
        return rs
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for v in rs.views:
        if not v.accepted or not v.path:
            continue
        src = Path(v.path)
        dst = out_dir / src.name
        if src.resolve() == dst.resolve():
            continue
        try:
            shutil.copy2(src, dst)
        except OSError as e:  # keep the cache path — still readable
            log.warning("could not copy reference into the run (%s); using the cache path", e)
            continue
        v.path = str(dst)
    (out_dir / "reference_set.json").write_text(rs.model_dump_json(indent=2))
    return rs


__all__ = ["ImagePromptPlan", "ViewPrompt", "compose_image_prompt", "image_prompt_plan", "synth_reference", "views_for"]
