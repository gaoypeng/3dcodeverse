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

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import RenderSet
from codeverse3d.contracts.chat import ImagePart
from codeverse3d.contracts.common import Usage
from codeverse3d.contracts.plan import PartPlan, ScenePlan, StaticPlan
from codeverse3d.contracts.spec import Spec
from codeverse3d.conventions import to_snake
from codeverse3d.models import get_chat_model
from codeverse3d.models.schema_utils import ask_structured
from codeverse3d.proc import write_json_atomic, write_text_atomic
from codeverse3d.prompts import load_text, prompt_hash, render
from codeverse3d.texturing.generate import TextureSet, generate_textures
from codeverse3d.texturing.materials import COARSE_TO_FINE, MATERIALS, Pbr

log = logging.getLogger(__name__)

MaterialFamily = Literal[
    "wood", "metal", "fabric", "stone", "plastic", "leather", "glass", "ceramic", "painted", "rubber", "other"
]
Projection = Literal["box", "cylinder", "planar_y", "planar_z", "auto"]

PLAN_TEMPLATE = "texturing/material_plan.md"
#: parts whose largest extent is below this keep their flat material
TINY_PART_M = 0.03

STYLE_SUFFIX = (
    "seamless tileable texture, perfectly repeating at the edges, top-down orthographic view, "
    "flat even diffuse lighting, no shadows, no specular highlights, no vignette, no depth of field, "
    "no text, no watermark, no border, fills the whole frame edge to edge, photoreal, uniform scale, 1:1 square"
)


class FamilyDefault(BaseModel):
    """A coarse family's IMAGE side (tile, prompt phrase, skip).  Its PBR factors are
    ``materials.pbr_for(family)`` — the one table the normaliser uses too (review 2 C5)."""

    tile_size_m: float
    phrase: str
    skip: bool = False


FAMILY_DEFAULTS: dict[str, FamilyDefault] = {
    "wood": FamilyDefault(tile_size_m=0.35, phrase="natural wood surface, visible grain and subtle pores"),
    "metal": FamilyDefault(tile_size_m=0.3, phrase="metal surface albedo only, matte base colour, no reflections or environment"),
    "fabric": FamilyDefault(tile_size_m=0.2, phrase="woven textile close-up, visible thread weave, soft matte"),
    "stone": FamilyDefault(tile_size_m=0.6, phrase="stone surface, natural mineral variation, matte"),
    "plastic": FamilyDefault(tile_size_m=0.3, phrase="smooth plastic surface, subtle fine noise, uniform colour"),
    "leather": FamilyDefault(tile_size_m=0.25, phrase="leather surface, fine natural grain and pores, matte"),
    "glass": FamilyDefault(tile_size_m=0.3, phrase="clear glass", skip=True),
    "ceramic": FamilyDefault(tile_size_m=0.3, phrase="glazed ceramic surface, subtle speckle, uniform colour"),
    "painted": FamilyDefault(tile_size_m=0.4, phrase="painted surface, subtle brush or roller micro-texture, uniform colour"),
    "rubber": FamilyDefault(tile_size_m=0.2, phrase="matte rubber surface, fine uniform grain"),
    "other": FamilyDefault(tile_size_m=0.3, phrase="matte natural surface material, fine detail", skip=True),
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


def _pbr(family: str) -> Pbr:
    """A coarse family's factors from THE material table (``COARSE_TO_FINE`` maps every one)."""
    return MATERIALS[COARSE_TO_FINE[family]]


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
    d, pbr = FAMILY_DEFAULTS[fam], _pbr(fam)
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
        roughness=float(raw.roughness) if raw.roughness is not None else pbr.roughness,
        metallic=float(raw.metallic) if raw.metallic is not None else pbr.metallic,
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
        model = get_chat_model(model_id)
    text = render(PLAN_TEMPLATE, spec_prompt=spec.prompt, style_notes=getattr(plan, "style_notes", ""),
                  n_parts=len(plan.parts), parts_table=parts_table(plan))
    out, usage, err = ask_structured(
        model, PlannerOutput, system="You plan PBR textures for 3D assets. Answer with JSON only.", text=text,
        images=[ImagePart(path=str(p), label=p.stem) for p in images], temperature=temperature, label="texture_plan",
    )
    if out is None:
        raise ValueError(f"texture planner: {err}")
    tp = finalize_plan(out, plan, model_id=model_id, usage=usage, source="vlm")
    if use_cache:
        # pid+thread tmp + rename (a torn sidecar re-bought the VLM call; a pid-only
        # name raced between threads of one process — review of PR #3)
        write_text_atomic(cached, tp.model_dump_json(indent=2))
    return tp


def plan_table(tp: TexturePlan) -> str:
    """Human-readable summary (CLI / report)."""
    rows = [f"{'part':24} {'texture_id':20} {'family':8} {'proj':9} {'tile':6} {'rough':5} {'met':4} skip"]
    for p in tp.parts:
        rows.append(f"{p.part:24} {p.texture_id:20} {p.material_family:8} {p.projection:9} {p.tile_size_m:<6.2f} "
                    f"{p.roughness:<5.2f} {p.metallic:<4.1f} {'yes' if p.skip else 'no'}{(' — ' + p.reason) if p.skip and p.reason else ''}")
    return "\n".join(rows)


# ===================================================================== scene_pack

PACK_TEMPLATE = "texturing/scene_pack.md"
DEFAULT_URL_PREFIX = "/public/textures"
MANIFEST_NAME = "manifest.json"


class PackEntry(BaseModel):
    name: str
    material_family: MaterialFamily = "other"
    subject: str = ""
    prompt: str = ""
    tile_size_m: float = Field(default=1.0, gt=0.0, le=50.0)
    role: str = "ground"
    #: a scene SURFACE default (ground, walls, planks read rough), deliberately not the object table's
    roughness: float = Field(default=0.8, ge=0.0, le=1.0)
    #: left unset by the planner → the family's (``materials``), set by ``_finalize_entry``
    metallic: float = Field(default=0.0, ge=0.0, le=1.0)
    file: str = ""
    seam_score: float = 0.0
    error: str = ""


class PackOutput(BaseModel):
    textures: list[PackEntry]
    notes: str = ""


class ScenePack(BaseModel):
    out_dir: str
    entries: dict[str, PackEntry] = Field(default_factory=dict)
    notes: str = ""
    model_id: str = ""
    source: str = "default"
    usage: Usage = Field(default_factory=Usage)
    manifest_path: str = ""

    def manifest(self) -> dict[str, dict[str, Any]]:
        return {n: {"file": e.file, "tile_size_m": e.tile_size_m, "family": e.material_family, "role": e.role,
                    "prompt": e.prompt, "seam_score": e.seam_score, "roughness": e.roughness, "metallic": e.metallic}
                for n, e in self.entries.items() if e.file and not e.error}


# --------------------------------------------------------------------------- planning
_GROUND_RULES: list[tuple[tuple[str, ...], PackEntry]] = [
    (("grass", "lawn", "meadow", "garden", "park"), PackEntry(name="grass_ground", subject="short green lawn grass seen from above, fine blades, slight colour variation", tile_size_m=1.5, role="ground")),
    (("moss", "mossy", "forest", "woodland"), PackEntry(name="moss_ground", subject="dense green moss carpet, soft velvety clumps", tile_size_m=1.0, role="ground")),
    (("gravel", "karesansui", "zen", "raked"), PackEntry(name="fine_gravel", material_family="stone", subject="fine pale grey granite gravel, small even pebbles", tile_size_m=0.8, role="ground")),
    (("sand", "beach", "desert", "dune"), PackEntry(name="sand_ground", subject="fine pale beige sand with soft ripples", tile_size_m=1.0, role="ground")),
    (("soil", "dirt", "earth", "mud", "path", "trail"), PackEntry(name="packed_soil", subject="dark brown packed earth with small pebbles", tile_size_m=1.0, role="path")),
    (("cobble", "pavement", "plaza", "street", "square", "town"), PackEntry(name="cobblestone", material_family="stone", subject="grey granite cobblestones with dark mortar joints", tile_size_m=1.2, role="path")),
    (("stone", "rock", "boulder", "granite", "cliff"), PackEntry(name="rough_granite", material_family="stone", subject="rough grey granite rock with lichen speckles", tile_size_m=1.0, role="prop")),
    (("wood", "plank", "bridge", "deck", "dock", "pier", "timber", "cabin", "fence"), PackEntry(name="weathered_planks", material_family="wood", subject="weathered grey-brown wooden planks, straight grain, narrow gaps", tile_size_m=0.8, role="prop")),
    (("roof", "tile", "shingle", "house", "temple", "hut"), PackEntry(name="clay_roof_tiles", material_family="ceramic", subject="terracotta clay roof tiles in overlapping rows", tile_size_m=1.0, role="roof")),
    (("brick", "wall"), PackEntry(name="red_brick", material_family="stone", subject="red clay bricks with light grey mortar, running bond", tile_size_m=1.0, role="wall")),
    (("bark", "tree", "trunk", "maple", "oak", "pine"), PackEntry(name="tree_bark", material_family="wood", subject="dark grey-brown tree bark with vertical furrows", tile_size_m=0.5, role="prop")),
    (("water", "pond", "lake", "river", "sea", "harbour", "harbor"), PackEntry(name="pond_bed", material_family="stone", subject="dark silty pond bed with scattered small pebbles and algae", tile_size_m=1.0, role="water_bed")),
    (("fabric", "tent", "cloth", "banner", "awning", "canvas"), PackEntry(name="canvas_fabric", material_family="fabric", subject="natural off-white woven canvas", tile_size_m=0.3, role="fabric")),
    (("metal", "steel", "iron", "rust"), PackEntry(name="rusty_steel", material_family="metal", subject="rust-streaked painted steel plate, matte", tile_size_m=0.5, role="prop")),
    (("concrete", "asphalt", "road", "industrial"), PackEntry(name="concrete", material_family="stone", subject="smooth grey concrete with fine pores", tile_size_m=1.0, role="ground")),
    (("bamboo",), PackEntry(name="bamboo_poles", material_family="wood", subject="vertical cured yellow bamboo poles with dark nodes", tile_size_m=0.5, role="prop")),
]


def _plan_text(plan: ScenePlan) -> str:
    bits = [plan.title, plan.summary, plan.setting, plan.mood, plan.environment]
    bits += [f"{z.name} {z.description}" for z in plan.zones]
    bits += [f"{a.name} {a.description}" for a in plan.assets]
    return " ".join(bits)


def _finalize_entry(e: PackEntry) -> PackEntry:
    fam = e.material_family if e.material_family in FAMILY_DEFAULTS else "other"
    name = to_snake(e.name) or f"{fam}_texture"
    subject = e.subject.strip() or name.replace("_", " ")
    return e.model_copy(update={
        "name": name, "material_family": fam, "subject": subject, "prompt": compose_image_prompt(subject, fam),
        # a "metal" pack the planner left unset shipped metallic 0.0 until 2026-09-23 (review 2 C5)
        "metallic": e.metallic if "metallic" in e.model_fields_set else _pbr(fam).metallic,
    })


def default_scene_pack_plan(plan: ScenePlan, *, n_min: int = 6, n_max: int = 12) -> list[PackEntry]:
    """Keyword fallback: textures whose trigger words appear in the plan; always a
    ground; padded with generic ground/stone/wood up to ``n_min``."""
    words = set(to_snake(_plan_text(plan)).split("_"))
    out: list[PackEntry] = []
    for keys, entry in _GROUND_RULES:
        if any(k in words for k in keys) and len(out) < n_max:
            out.append(entry)
    names = {e.name for e in out}
    for _, entry in _GROUND_RULES:
        if len(out) >= n_min:
            break
        if entry.name not in names:
            out.append(entry)
            names.add(entry.name)
    return [_finalize_entry(e) for e in out[:n_max]]


def scene_pack_plan(
    plan: ScenePlan, model_id: str, *, model: Any | None = None, n_min: int = 6, n_max: int = 12, temperature: float = 0.4,
) -> tuple[list[PackEntry], str, Usage]:
    """Structured text call → entries.  Empty ``model_id`` and no model → default plan."""
    if not model_id and model is None:
        return default_scene_pack_plan(plan, n_min=n_min, n_max=n_max), "default", Usage()
    if model is None:
        model = get_chat_model(model_id)
    zones = "\n".join(f"- {z.name}: {z.description}" for z in plan.zones)
    assets = "\n".join(f"- {a.name} ({a.kind}): {a.description}" for a in plan.assets) or "- (none)"
    text = render(PACK_TEMPLATE, n_min=n_min, n_max=n_max, title=plan.title, setting=plan.setting, mood=plan.mood,
                  environment=plan.environment, zones=zones, assets=assets)
    out, usage, err = ask_structured(model, PackOutput, system="You plan texture packs for 3D scenes. JSON only.",
                                     text=text, temperature=temperature, label="scene_texture_pack")
    if out is None:
        raise ValueError(f"scene pack planner: {err}")
    entries: list[PackEntry] = []
    seen: set[str] = set()
    for e in out.textures:
        fe = _finalize_entry(e)
        if fe.name in seen:
            continue
        seen.add(fe.name)
        entries.append(fe)
    if not entries:
        raise ValueError("scene pack planner returned no textures")
    return entries[:n_max], out.notes, usage


# --------------------------------------------------------------------------- generation
def scene_texture_pack(
    scene_plan: ScenePlan,
    out_dir: Path,
    image_model: Any,
    model_id: str = "",
    *,
    model: Any | None = None,
    size: int = 1024,
    cache_dir: Path | None = None,
    n_min: int = 6,
    n_max: int = 12,
    max_workers: int = 6,
    use_cache: bool = True,
) -> ScenePack:
    """Plan + generate the pack into ``out_dir`` and write ``manifest.json``."""
    out_dir = Path(out_dir)
    entries, notes, usage = scene_pack_plan(scene_plan, model_id, model=model, n_min=n_min, n_max=n_max)
    pack = ScenePack(out_dir=str(out_dir), notes=notes, model_id=model_id, source="vlm" if (model_id or model) else "default",
                     usage=usage)
    ts: TextureSet = generate_textures({e.name: e.prompt for e in entries}, out_dir, image_model, size=size,
                                       cache_dir=cache_dir, max_workers=max_workers, use_cache=use_cache)
    pack.usage = pack.usage + ts.usage
    for e in entries:
        a = ts.textures.get(e.name)
        if a is None:
            continue
        e = e.model_copy(update={"file": Path(a.path).name if a.ok else "", "seam_score": round(a.seam_score, 4), "error": a.error})
        pack.entries[e.name] = e
    pack.manifest_path = str(write_manifest(pack, out_dir))
    return pack


def write_manifest(pack: ScenePack, out_dir: Path) -> Path:
    p = Path(out_dir) / MANIFEST_NAME
    write_json_atomic(p, pack.manifest())
    return p


def texture_pack_prompt(manifest: dict[str, dict[str, Any]], *, url_prefix: str = DEFAULT_URL_PREFIX) -> str:
    """Prompt snippet for zone/env generation: what exists and the exact loading idiom.

    Rendered AFTER the cookbook recipes (``tracks/scene_env.j2`` / ``scene_zone.j2``), and
    it says outright that the ground blend is what the pack is for.  Measured 2026-09-05:
    the first two cells of the texture arm generated 8 and 9 textures, carried this block
    verbatim in their env prompt, and used **zero** of them across `env.js` and six zone
    modules.  Three things were telling the model to write a procedural colour blend — the
    plan's own ground sentence ("blends velvet moss, raked gravel, packed earth and slate
    by height, slope and normal noise"), the cookbook chapter "Ground that reads real
    (blend, paths, edges — never one flat colour)", and that chapter's position AFTER this
    block — against one passive list offering an alternative.  A passive offer loses.
    """
    if not manifest:
        return ""
    ground = [n for n, e in manifest.items() if str(e.get("role", "")).lower() in ("ground", "path")]
    lines = ["## Available textures (seamless tileable albedo PNGs, generated FOR THIS SCENE — use them)"]
    for name, e in manifest.items():
        lines.append(f"- `{url_prefix}/{e['file']}` — {name} ({e.get('family', 'other')}, {e.get('role', '')}; "
                     f"one tile = {float(e['tile_size_m']):.2f} m; roughness {float(e.get('roughness', 0.8)):.2f})")
    lines.append(
        "Load with the provided loader ONLY (no TextureLoader of your own, no DataTexture):\n"
        "```js\n"
        f"const tex = loaders.texture.load('{url_prefix}/{next(iter(manifest.values()))['file']}');\n"
        "tex.wrapS = tex.wrapT = THREE.RepeatWrapping;\n"
        "tex.colorSpace = THREE.SRGBColorSpace;\n"
        "tex.anisotropy = 8;\n"
        "tex.repeat.set(sizeX / TILE_M, sizeZ / TILE_M);   // TILE_M = the tile size listed above, sizeX/Z = the surface's world size in m\n"
        "const mat = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.8, metalness: 0.0 });\n"
        "```\n"
        "Use `texture.clone()` (+ `needsUpdate = true`) when the same image needs a different repeat. "
        "Never invent texture URLs, and keep procedural materials where no texture fits."
    )
    if ground:
        lines.append(
            "\n**The ground is what this pack is for.** The single most repeated defect on this "
            "track is a ground that reads as one flat untextured colour. The recipe above "
            "blends ground COLOURS by height, slope and noise — keep that logic exactly, and "
            "blend these MAPS instead of (or multiplied by) the flat colours: "
            + ", ".join(f"`{n}`" for n in ground)
            + ". Sample each with its own `repeat`, mix them with the same masks the recipe "
            "builds, and keep the vertex-colour term as a tint rather than the whole albedo."
        )
    return "\n".join(lines)
