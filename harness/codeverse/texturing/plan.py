"""Material plan: ONE vision call (contact sheet + plan parts table + brief) →
``TexturePlan`` (per part: family, image prompt, projection, tile size, PBR factors,
skip) validated in code with deterministic defaults, cached by content hash.

``default_plan(plan)`` is the no-model fallback (keyword heuristics on the plan's
``material`` strings) used by tests and when ``model_id`` is empty.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from codeverse.config import get_settings
from codeverse.contracts.artifacts import RenderSet
from codeverse.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse.contracts.common import Usage
from codeverse.contracts.plan import PartPlan, StaticPlan
from codeverse.contracts.spec import Spec
from codeverse.conventions import to_snake
from codeverse.models.schema_utils import parse_json_lenient
from codeverse.prompts import load_text, prompt_hash, render

log = logging.getLogger(__name__)

MaterialFamily = Literal[
    "wood", "metal", "fabric", "stone", "plastic", "leather", "glass", "ceramic", "painted", "rubber", "other"
]
Projection = Literal["box", "cylinder", "planar_y", "planar_z", "auto"]

PLAN_TEMPLATE = "texturing/material_plan.md"
STYLE_DOC = "texturing/image_prompt_style.md"
#: parts whose largest extent is below this keep their flat material
TINY_PART_M = 0.03

STYLE_SUFFIX = (
    "seamless tileable texture, perfectly repeating at the edges, top-down orthographic view, "
    "flat even diffuse lighting, no shadows, no specular highlights, no vignette, no depth of field, "
    "no text, no watermark, no border, fills the whole frame edge to edge, photoreal, uniform scale, 1:1 square"
)


class FamilyDefault(BaseModel):
    roughness: float
    metallic: float
    tile_size_m: float
    phrase: str
    skip: bool = False


FAMILY_DEFAULTS: dict[str, FamilyDefault] = {
    "wood": FamilyDefault(roughness=0.55, metallic=0.0, tile_size_m=0.35, phrase="natural wood surface, visible grain and subtle pores"),
    "metal": FamilyDefault(roughness=0.35, metallic=1.0, tile_size_m=0.3, phrase="metal surface albedo only, matte base colour, no reflections or environment"),
    "fabric": FamilyDefault(roughness=0.9, metallic=0.0, tile_size_m=0.2, phrase="woven textile close-up, visible thread weave, soft matte"),
    "stone": FamilyDefault(roughness=0.8, metallic=0.0, tile_size_m=0.6, phrase="stone surface, natural mineral variation, matte"),
    "plastic": FamilyDefault(roughness=0.45, metallic=0.0, tile_size_m=0.3, phrase="smooth plastic surface, subtle fine noise, uniform colour"),
    "leather": FamilyDefault(roughness=0.6, metallic=0.0, tile_size_m=0.25, phrase="leather surface, fine natural grain and pores, matte"),
    "glass": FamilyDefault(roughness=0.05, metallic=0.0, tile_size_m=0.3, phrase="clear glass", skip=True),
    "ceramic": FamilyDefault(roughness=0.3, metallic=0.0, tile_size_m=0.3, phrase="glazed ceramic surface, subtle speckle, uniform colour"),
    "painted": FamilyDefault(roughness=0.5, metallic=0.0, tile_size_m=0.4, phrase="painted surface, subtle brush or roller micro-texture, uniform colour"),
    "rubber": FamilyDefault(roughness=0.85, metallic=0.0, tile_size_m=0.2, phrase="matte rubber surface, fine uniform grain"),
    "other": FamilyDefault(roughness=0.6, metallic=0.0, tile_size_m=0.3, phrase="matte natural surface material, fine detail", skip=True),
}

_FAMILY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("glass", ("glass", "acrylic", "crystal")),
    ("metal", ("steel", "iron", "aluminum", "aluminium", "brass", "bronze", "copper", "chrome", "metal", "nickel", "gold", "silver")),
    ("wood", ("wood", "oak", "walnut", "pine", "teak", "maple", "ash", "beech", "birch", "mahogany", "bamboo", "plywood", "timber")),
    ("fabric", ("fabric", "linen", "cotton", "velvet", "wool", "upholster", "textile", "canvas", "felt", "cloth", "boucle")),
    ("leather", ("leather", "suede", "hide")),
    ("stone", ("stone", "marble", "granite", "concrete", "slate", "terrazzo", "brick", "rock")),
    ("ceramic", ("ceramic", "porcelain", "terracotta", "clay", "glazed")),
    ("rubber", ("rubber", "silicone", "foam")),
    ("painted", ("paint", "lacquer", "enamel", "powder-coated", "powder coated", "varnish")),
    ("plastic", ("plastic", "abs", "polypropylene", "nylon", "resin", "vinyl", "pvc", "polycarbonate")),
]
_SKIP_WORDS = ("chrome", "mirror", "mirrored", "polished", "glossy", "emissive", "led", "transparent", "glass")


def _words(text: str) -> set[str]:
    return set(to_snake(text).split("_"))


def family_from_text(text: str) -> str:
    """Keyword → family on whole words (``oiled`` must not match ``led``)."""
    ws = _words(text)
    for fam, words in _FAMILY_KEYWORDS:
        if any(w in ws for w in words):
            return fam
    return "other"


def has_skip_word(text: str) -> bool:
    return bool(_words(text) & set(_SKIP_WORDS))


def compose_image_prompt(subject: str, family: str) -> str:
    """Harness-owned composition: subject + family phrase + the style suffix."""
    fam = FAMILY_DEFAULTS.get(family, FAMILY_DEFAULTS["other"])
    subject = " ".join(subject.strip().rstrip(".,;").split())
    return f"{subject}, {fam.phrase}, {STYLE_SUFFIX}"


# --------------------------------------------------------------------------- models
class TexturePart(BaseModel):
    part: str = Field(description="plan part name (PascalCase)")
    texture_id: str = Field(description="snake_case id; parts sharing a material share it")
    material_family: MaterialFamily = "other"
    prompt: str = Field(default="", description="full image prompt (harness-composed)")
    subject: str = Field(default="", description="pattern description from the planner")
    projection: Projection = "auto"
    tile_size_m: float = Field(default=0.3, gt=0.0, le=20.0)
    roughness: float = Field(default=0.6, ge=0.0, le=1.0)
    metallic: float = Field(default=0.0, ge=0.0, le=1.0)
    tint_rgb: tuple[float, float, float] | None = None
    skip: bool = False
    reason: str = ""


class TexturePlan(BaseModel):
    object_name: str = ""
    parts: list[TexturePart] = Field(default_factory=list)
    notes: str = ""
    model_id: str = ""
    source: Literal["vlm", "default", "cache"] = "default"
    usage: Usage = Field(default_factory=Usage)

    def textured(self) -> list[TexturePart]:
        return [p for p in self.parts if not p.skip]

    def texture_ids(self) -> list[str]:
        seen: list[str] = []
        for p in self.textured():
            if p.texture_id not in seen:
                seen.append(p.texture_id)
        return seen

    def prompts(self) -> dict[str, str]:
        """texture_id → image prompt (first part wins; ids share prompts by construction)."""
        out: dict[str, str] = {}
        for p in self.textured():
            out.setdefault(p.texture_id, p.prompt)
        return out

    def by_part(self) -> dict[str, TexturePart]:
        return {to_snake(p.part): p for p in self.parts}


class PlannerPart(BaseModel):
    """What the VLM returns per part (all optional but part/texture_id/subject)."""

    part: str
    texture_id: str
    material_family: MaterialFamily = "other"
    subject: str = ""
    projection: Projection = "auto"
    tile_size_m: float | None = None
    roughness: float | None = None
    metallic: float | None = None
    tint_rgb: list[float] | None = None
    skip: bool = False
    reason: str = ""


class PlannerOutput(BaseModel):
    parts: list[PlannerPart]
    notes: str = ""


# --------------------------------------------------------------------------- finalise
def _max_extent(p: PartPlan) -> float:
    return max(float(e) for e in p.bbox.extents)


def _clean_id(s: str, fallback: str) -> str:
    return to_snake(s) if s.strip() else fallback


def finalize_part(raw: PlannerPart, plan_part: PartPlan) -> TexturePart:
    fam = raw.material_family if raw.material_family in FAMILY_DEFAULTS else "other"
    d = FAMILY_DEFAULTS[fam]
    subject = raw.subject.strip() or (plan_part.material.strip() or f"{fam} surface")
    skip, reason = bool(raw.skip), raw.reason
    if _max_extent(plan_part) < TINY_PART_M:
        skip, reason = True, reason or f"tiny part (< {TINY_PART_M * 100:.0f} cm)"
    if fam == "glass" or d.skip and not raw.subject.strip():
        skip, reason = True, reason or f"{fam} keeps its flat material"
    if _words(plan_part.material + " " + subject) & {"chrome", "mirror", "mirrored"}:
        skip, reason = True, reason or "mirror-like finish"
    tint = None
    if raw.tint_rgb and len(raw.tint_rgb) == 3:
        tint = tuple(min(1.0, max(0.0, float(c))) for c in raw.tint_rgb)  # type: ignore[assignment]
    return TexturePart(
        part=plan_part.name,
        texture_id=_clean_id(raw.texture_id, f"{fam}_{to_snake(plan_part.name)}"),
        material_family=fam,  # type: ignore[arg-type]
        subject=subject,
        prompt=compose_image_prompt(subject, fam),
        projection=raw.projection,
        tile_size_m=float(raw.tile_size_m) if raw.tile_size_m and raw.tile_size_m > 0 else d.tile_size_m,
        roughness=float(raw.roughness) if raw.roughness is not None else d.roughness,
        metallic=float(raw.metallic) if raw.metallic is not None else d.metallic,
        tint_rgb=tint,
        skip=skip,
        reason=reason,
    )


def finalize_plan(out: PlannerOutput, plan: StaticPlan, *, model_id: str = "", usage: Usage | None = None,
                  source: Literal["vlm", "default", "cache"] = "vlm") -> TexturePlan:
    """Validate against the plan parts; unknown names dropped, missing parts defaulted;
    parts sharing a texture_id are forced onto ONE prompt/family (first wins)."""
    by_name = {to_snake(p.part): p for p in out.parts}
    parts: list[TexturePart] = []
    for pp in plan.parts:
        raw = by_name.get(to_snake(pp.name))
        if raw is None:
            raw = _default_raw(pp)
        parts.append(finalize_part(raw, pp))
    unknown = sorted(set(by_name) - {to_snake(p.name) for p in plan.parts})
    if unknown:
        log.warning("texture plan: dropping unknown parts %s", unknown)
    first: dict[str, TexturePart] = {}
    for tp in parts:
        if tp.skip:
            continue
        head = first.setdefault(tp.texture_id, tp)
        if head is not tp:
            tp.prompt, tp.subject, tp.material_family = head.prompt, head.subject, head.material_family
    return TexturePlan(object_name=plan.object_name, parts=parts, notes=out.notes, model_id=model_id,
                       source=source, usage=usage or Usage())


def _default_raw(pp: PartPlan) -> PlannerPart:
    fam = family_from_text(pp.material or pp.description)
    skip = fam in ("glass", "other") or has_skip_word(pp.material)
    words = [w for w in to_snake(pp.material).split("_") if w][:2]
    tid = "_".join([fam, *words]) if words else fam
    return PlannerPart(part=pp.name, texture_id=tid, material_family=fam, subject=pp.material.strip(),  # type: ignore[arg-type]
                       skip=skip, reason="default plan" if not skip else "default plan: unknown/glass/gloss material")


def default_plan(plan: StaticPlan) -> TexturePlan:
    """Deterministic plan without any model call (keyword heuristics)."""
    out = PlannerOutput(parts=[_default_raw(p) for p in plan.parts], notes="heuristic default plan")
    return finalize_plan(out, plan, source="default")


# --------------------------------------------------------------------------- prompt + call
def parts_table(plan: StaticPlan) -> str:
    rows = []
    for p in plan.parts:
        w, d, h = (f"{float(e):.3f}" for e in p.bbox.extents)
        rows.append(f"- {p.name} · {p.role} · {p.material or '(unspecified)'} · {w}×{d}×{h} · ×{p.instances}")
    return "\n".join(rows)


def _sheet_images(renders: RenderSet | Path | str | Sequence[Path | str] | None, max_views: int = 4) -> list[Path]:
    if renders is None:
        return []
    if isinstance(renders, RenderSet):
        if renders.contact_sheet and Path(renders.contact_sheet).is_file():
            return [Path(renders.contact_sheet)]
        return [Path(v.path) for v in renders.views[:max_views] if Path(v.path).is_file()]
    if isinstance(renders, (str, Path)):
        return [Path(renders)] if Path(renders).is_file() else []
    return [Path(p) for p in renders if Path(p).is_file()][:max_views]


def plan_cache_key(spec: Spec, plan: StaticPlan, images: Sequence[Path], model_id: str) -> str:
    h = hashlib.sha256()
    h.update(prompt_hash(load_text(PLAN_TEMPLATE)).encode())
    h.update(spec.prompt.encode())
    h.update(plan.model_dump_json().encode())
    h.update(model_id.encode())
    for p in images:
        h.update(hashlib.sha256(p.read_bytes()).hexdigest().encode())
    return h.hexdigest()[:20]


def material_plan(
    spec: Spec,
    plan: StaticPlan,
    renders: RenderSet | Path | str | Sequence[Path | str] | None,
    model_id: str,
    *,
    model: Any | None = None,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    temperature: float = 0.3,
) -> TexturePlan:
    """One structured vision call → validated ``TexturePlan``.  ``model`` may be any
    ``ChatModel`` (tests inject a fake); empty ``model_id`` and no model → default plan."""
    if not model_id and model is None:
        return default_plan(plan)
    images = _sheet_images(renders)
    cache_root = (cache_dir or get_settings().cache_dir) / "texture_plans"
    key = plan_cache_key(spec, plan, images, model_id)
    cached = cache_root / f"{key}.json"
    if use_cache and cached.is_file():
        try:
            tp = TexturePlan.model_validate_json(cached.read_text())
            tp.source, tp.usage = "cache", Usage()
            return tp
        except ValidationError:
            cached.unlink(missing_ok=True)
    if model is None:
        from codeverse.models import get_chat_model

        model = get_chat_model(model_id)
    text = render(PLAN_TEMPLATE, spec_prompt=spec.prompt, style_notes=getattr(plan, "style_notes", ""),
                  n_parts=len(plan.parts), parts_table=parts_table(plan))
    req = ChatRequest(
        messages=[ChatMessage.user(text, images=[ImagePart(path=str(p), label=p.stem) for p in images])],
        system="You plan PBR textures for 3D assets. Answer with JSON only.",
        response_schema=PlannerOutput.model_json_schema(), temperature=temperature, thinking="low",
        max_output_tokens=8000, max_wait_s=300.0, label="texture_plan",
    )
    resp = model.generate(req)
    payload = resp.parsed if resp.parsed is not None else parse_json_lenient(resp.text)
    try:
        out = PlannerOutput.model_validate(payload)
    except ValidationError as e:
        raise ValueError(f"texture planner returned invalid JSON for the schema: {e}") from e
    tp = finalize_plan(out, plan, model_id=model_id, usage=resp.usage, source="vlm")
    if use_cache:
        cache_root.mkdir(parents=True, exist_ok=True)
        cached.write_text(tp.model_dump_json(indent=2))
    return tp


def plan_table(tp: TexturePlan) -> str:
    """Human-readable summary (CLI / report)."""
    rows = [f"{'part':24} {'texture_id':20} {'family':8} {'proj':9} {'tile':6} {'rough':5} {'met':4} skip"]
    for p in tp.parts:
        rows.append(f"{p.part:24} {p.texture_id:20} {p.material_family:8} {p.projection:9} {p.tile_size_m:<6.2f} "
                    f"{p.roughness:<5.2f} {p.metallic:<4.1f} {'yes' if p.skip else 'no'}{(' — ' + p.reason) if p.skip and p.reason else ''}")
    return "\n".join(rows)

