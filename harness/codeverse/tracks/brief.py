"""Brief expansion: the "input prompt" lever, upstream of the planner.

One cheap structured call turns "a hand-crank coffee grinder" into an
:class:`~codeverse.contracts.plan.EngineeringBrief`: the real-world reference instance
and its dimensions, the sub-assemblies a real one has, the mechanism, what is visible
from outside, what it does NOT have, and 3-6 *signature features* a viewer uses to
recognise it.  Optional (``CV3D_PLAN_BRIEF``), cached on disk by prompt hash, and never
fatal — any failure returns ``None`` and the planner runs exactly as it did before.

What the planner then DOES with the brief — the derived plan budget, the re-plan quality
gate and the fold back into the plan — lives in ``tracks/plan_budget.py``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from codeverse.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse.contracts.common import Track, Usage
from codeverse.contracts.plan import EngineeringBrief
from codeverse.contracts.spec import Spec
from codeverse.models.schema_utils import parse_json_lenient
from codeverse.prompts import load_text, prompt_hash, render

log = logging.getLogger(__name__)

#: env switch: ``off``/``0``/``false`` disables brief expansion for the run
BRIEF_ENV = "CV3D_PLAN_BRIEF"
#: tracks the object-shaped brief applies to (graphics/scene get budgets only)
BRIEF_TRACKS = (Track.STATIC_OBJECT, Track.ARTICULATED_OBJECT)
BRIEF_TEMPLATE = "tracks/brief_object.j2"
BRIEF_MAX_TOKENS = 9000


# ----------------------------------------------------------------------------- brief
def brief_enabled(spec: Spec, *, default: bool = True) -> bool:
    """Whether to expand the brief for this spec (env switch wins; object tracks only)."""
    raw = (os.environ.get(BRIEF_ENV) or "").strip().lower()
    on = default if not raw else raw not in ("0", "off", "false", "no")
    return on and spec.track in BRIEF_TRACKS


def brief_cache_dir() -> Path:
    root = os.environ.get("CV3D_CACHE_DIR") or (Path.home() / ".cache" / "codeverse")
    return Path(root) / "briefs"


def _reference_digest(spec: Spec) -> list[dict[str, str]]:
    """Identify the reference images by path AND content hash.

    ``--reference`` synthesises a NEW image per run under the same spec fields, so
    the path alone does not separate two runs; the bytes do.  An unreadable file
    still contributes its path, so it can never collapse onto "no references"."""
    out: list[dict[str, str]] = []
    for r in spec.references:
        try:
            digest = _sha256_file(Path(r.path))
        except OSError:
            digest = "(unreadable)"
        out.append({"path": r.path, "role": r.role, "note": r.note, "sha256": digest})
    return out


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def brief_cache_key(spec: Spec, model_id: str) -> str:
    """Every input to the call must be in the key.

    ``expand_brief`` attaches ``spec.references`` to the request and tells the model
    to read the dimensions and features off them, so a key that omits them lets an
    ``--image`` run drink a brief generated WITHOUT the image (and vice versa) —
    silently, from the machine-wide ~/.cache/codeverse/briefs, with cached=True in
    events.jsonl."""
    c = spec.constraints
    payload = json.dumps({
        "prompt": spec.prompt.strip(), "track": spec.track.value, "language": spec.language.value,
        "must_have": list(c.must_have), "must_not": list(c.must_not), "style": c.style,
        "dimensions_m": c.dimensions_m or {}, "model": model_id,
        "references": _reference_digest(spec),
        "template": prompt_hash(load_text(BRIEF_TEMPLATE)),
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def expand_brief(spec: Spec, model_id: str, *, model: Any | None = None, events: Any | None = None,
                 cache_dir: Path | None = None) -> tuple[EngineeringBrief | None, Usage]:
    """One cheap structured call → :class:`EngineeringBrief`, cached by prompt hash.

    Never raises and never blocks planning: any failure returns ``(None, usage)`` and the
    planner runs exactly as it did before."""
    usage = Usage()
    cdir = cache_dir if cache_dir is not None else brief_cache_dir()
    key = brief_cache_key(spec, model_id)
    path = cdir / f"{key}.json"
    if path.is_file():
        try:
            brief = EngineeringBrief.model_validate_json(path.read_text())
            if not brief.is_useful:
                raise ValueError("cached brief has nothing to plan from")
            if events is not None:
                events.emit("plan.brief", cached=True, key=key, n_sub=len(brief.sub_assemblies),
                            n_signature=len(brief.signature_features), cost_usd=0.0)
            return brief, usage
        except (ValidationError, ValueError, OSError) as e:  # a stale/corrupt cache entry is not fatal
            log.warning("brief cache %s unusable (%s); regenerating", path, e)
    if model is None:
        from codeverse.models import get_chat_model

        model = get_chat_model(model_id)
    system = render(BRIEF_TEMPLATE, track=spec.track.value, language=spec.language.value)
    images = [ImagePart(path=r.path, label=f"{r.role}: {r.note}".strip(": ")) for r in spec.references]
    user = f"REQUEST: {spec.prompt.strip()}"
    if spec.constraints.must_have:
        user += "\n\nThe user also requires:\n" + "\n".join(f"- {m}" for m in spec.constraints.must_have)
    if spec.constraints.dimensions_m:
        user += "\n\nStated dimensions (m): " + ", ".join(f"{k}={v:.3f}" for k, v in spec.constraints.dimensions_m.items())
    if images:
        user += f"\n\n{len(images)} reference image(s) are attached — read the dimensions and features off them."
    try:
        resp = model.generate(ChatRequest(
            messages=[ChatMessage.user(user, images=images or None)], system=system,
            response_schema=EngineeringBrief.model_json_schema(), temperature=0.3, thinking="low",
            max_output_tokens=BRIEF_MAX_TOKENS, max_wait_s=300.0, label="planner-brief"))
        usage = usage + resp.usage
        # parse_json_lenient, not json.loads: it tolerates fences/prose AND strips the
        # C0 controls a model can emit, which json.loads would carry into the brief.
        raw = resp.parsed if resp.parsed is not None else parse_json_lenient(resp.text or "{}")
        brief = EngineeringBrief.model_validate(raw)
        if not brief.is_useful:
            raise ValueError(f"brief has {len(brief.sub_assemblies)} sub-assemblies and "
                             f"{len(brief.signature_features)} signature features — nothing to plan from")
    except Exception as e:  # noqa: BLE001 — the brief is an optional accelerator, never a failure mode
        log.warning("brief expansion failed (%s: %s); planning without it", type(e).__name__, e)
        if events is not None:
            events.emit("plan.brief_failed", error=f"{type(e).__name__}: {e}"[:300])
        return None, usage
    try:
        cdir.mkdir(parents=True, exist_ok=True)
        path.write_text(brief.model_dump_json(indent=1))
    except OSError as e:
        log.warning("could not cache brief at %s: %s", path, e)
    if events is not None:
        events.emit("plan.brief", cached=False, key=key, n_sub=len(brief.sub_assemblies),
                    n_signature=len(brief.signature_features), cost_usd=round(usage.cost_usd, 5))
    return brief, usage


def brief_block(brief: EngineeringBrief | None) -> str:
    """The brief as a prompt block for the planner's user message ("" when absent)."""
    if brief is None:
        return ""
    out = ["ENGINEERING BRIEF (researched for this request — treat it as ground truth about the real object):",
           f"- it is: {brief.one_line or brief.object_name}"]
    if brief.reference:
        out.append(f"- reference instance the numbers come from: {brief.reference}")
    if brief.dimensions_m:
        out.append("- real-world dimensions (m): " + ", ".join(f"{d.name} {d.meters:.3f}" for d in brief.dimensions_m))
    if brief.mechanism:
        out.append(f"- how it works: {brief.mechanism}")
    if brief.sub_assemblies:
        out.append(f"- sub-assemblies a real one has ({len(brief.sub_assemblies)}):")
        for s in brief.sub_assemblies:
            bits = f"  · {s.name}"
            if s.purpose:
                bits += f" — {s.purpose}"
            if s.parts:
                bits += f"; made of: {', '.join(s.parts)}"
            if s.material:
                bits += f" [{s.material}]"
            out.append(bits)
    if brief.visible_from_outside:
        out.append("- visible from outside (model these): " + "; ".join(brief.visible_from_outside))
    if brief.hidden_inside:
        out.append("- inside, NOT visible (do not model): " + "; ".join(brief.hidden_inside))
    if brief.not_present:
        out.append("- it does NOT have: " + "; ".join(brief.not_present))
    if brief.materials:
        out.append("- materials: " + "; ".join(brief.materials))
    if brief.signature_features:
        out.append(f"- SIGNATURE FEATURES ({len(brief.signature_features)}) — a viewer recognises the object by these; "
                   "every one of them must be a part, a sub-part or a named detail in your plan:")
        out += [f"  {i}. {f}" for i, f in enumerate(brief.signature_features, 1)]
    return "\n".join(out)



__all__ = ["BRIEF_ENV", "BRIEF_TEMPLATE", "BRIEF_TRACKS", "brief_block", "brief_cache_dir",
           "brief_cache_key", "brief_enabled", "expand_brief"]
