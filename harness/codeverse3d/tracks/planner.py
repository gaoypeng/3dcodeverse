"""Planner: spec → validated Plan (StaticPlan / ArticulatedPlan / ScenePlan / GraphicsPlan).

The ONE owner of what differs per track at planning time — the system-prompt template
(``PLAN_TEMPLATES``), the worked example (``plan_example``), the sampling temperature
(``plan_temperature``), the output-token floor, the plan budget's unit (graphics counts
passes) and the deterministic acceptance items (``ensure_acceptance``): all dispatched on
``spec.track``, so no track class parameterises the loop.

An optional cheap **brief expansion** (``expand_brief``, below) turns the one-line
request into an engineering brief, which — together with a **plan budget derived
from the request** — goes into one structured ``ChatRequest`` (system prompt from
``prompts/tracks/plan_<track>.j2``, ``response_schema`` = the plan model's JSON
schema, reference images inline).  The answer is validated IN CODE by the pydantic
plan models; a plan the schema rejects is re-asked twice with the exact validation
errors, plus one restart from the request when it is degenerate (then ``PlanningError``),
and a plan that validates but is boxes-at-different-sizes against its budget is re-asked
once with that specific complaint (then shipped anyway — a mediocre plan beats no plan).  The harness
finally appends deterministic acceptance items derived from the spec constraints
and folds the brief + sub-parts into the plan's own text (generated artifacts are
weak links — framework-owned requirements never rely on the model remembering them).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from codeverse3d.config import get_settings
from codeverse3d.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse3d.contracts.common import Language, Track, Usage
from codeverse3d.contracts.plan import (
    AcceptanceItem,
    EngineeringBrief,
    ScenePlan,
    scene_plan_schema,
)
from codeverse3d.contracts.spec import Spec
from codeverse3d.conventions import LANGUAGE_FRAME, frame_doc, to_pascal, to_snake
from codeverse3d.models import get_chat_model
from codeverse3d.models.schema_utils import ask_structured, parse_json_lenient
from codeverse3d.proc import NULL_EVENTS, sha256_file, write_text_atomic
from codeverse3d.prompts import load_text, prompt_hash, render
from codeverse3d.prompts.catalog import contract_sections, language_text
from codeverse3d.tracks.prompting import constraints_text
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)


# ===================================================================== the brief
#: tracks the object-shaped brief applies to (graphics/scene get budgets only)
BRIEF_TRACKS = (Track.STATIC_OBJECT, Track.ARTICULATED_OBJECT)
BRIEF_TEMPLATE = "tracks/brief_object.j2"


# ----------------------------------------------------------------------------- brief
def brief_enabled(spec: Spec) -> bool:
    """Whether to expand the brief for this spec (``C3D_PLAN_BRIEF``; object tracks only)."""
    return get_settings().plan_brief and spec.track in BRIEF_TRACKS


def brief_cache_dir() -> Path:
    """``Settings.cache_dir``/briefs: ``C3D_CACHE_DIR`` AND the config file's ``cache_dir:``."""
    return get_settings().cache_dir / "briefs"


def _reference_digest(spec: Spec) -> list[dict[str, str]]:
    """Identify the reference images by path AND content hash.

    ``--reference`` synthesises a NEW image per run under the same spec fields, so
    the path alone does not separate two runs; the bytes do.  An unreadable file
    still contributes its path, so it can never collapse onto "no references"."""
    out: list[dict[str, str]] = []
    for r in spec.references:
        try:
            digest = sha256_file(r.path)
        except OSError:
            digest = "(unreadable)"
        out.append({"path": r.path, "role": r.role, "note": r.note, "sha256": digest})
    return out


def brief_cache_key(spec: Spec, model_id: str) -> str:
    """Every input to the call must be in the key.

    ``expand_brief`` attaches ``spec.references`` to the request and tells the model
    to read the dimensions and features off them, so a key that omits them lets an
    ``--image`` run drink a brief generated WITHOUT the image (and vice versa) —
    silently, from the machine-wide ~/.cache/codeverse3d/briefs, with cached=True in
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
    events = NULL_EVENTS if events is None else events
    usage = Usage()
    cdir = cache_dir if cache_dir is not None else brief_cache_dir()
    key = brief_cache_key(spec, model_id)
    path = cdir / f"{key}.json"
    if path.is_file():
        try:
            brief = EngineeringBrief.model_validate_json(path.read_text())
            if not brief.is_useful:
                raise ValueError("cached brief has nothing to plan from")
            events.emit("plan.brief", cached=True, key=key, n_sub=len(brief.sub_assemblies),
                        n_signature=len(brief.signature_features), cost_usd=0.0)
            return brief, usage
        except (ValidationError, ValueError, OSError) as e:  # a stale/corrupt cache entry is not fatal
            log.warning("brief cache %s unusable (%s); regenerating", path, e)
    if model is None:
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
        brief, usage, error = ask_structured(model, EngineeringBrief, system=system, text=user, images=images,
                                             temperature=0.3, label="planner-brief")
        if brief is None:
            raise ValueError(error)
        if not brief.is_useful:
            raise ValueError(f"brief has {len(brief.sub_assemblies)} sub-assemblies and "
                             f"{len(brief.signature_features)} signature features — nothing to plan from")
    except Exception as e:  # noqa: BLE001 — the brief is an optional accelerator, never a failure mode
        log.warning("brief expansion failed (%s: %s); planning without it", type(e).__name__, e)
        events.emit("plan.brief_failed", error=f"{type(e).__name__}: {e}"[:300])
        return None, usage
    try:
        write_text_atomic(path, brief.model_dump_json(indent=1))  # a torn entry re-buys the call
    except OSError as e:
        log.warning("could not cache brief at %s: %s", path, e)
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


# ===================================================================== plan budgets
#: practical ceiling on TOP-LEVEL plan parts per language.  Blender/three.js own one
#: file per part so they scale; cadquery is one file; urdf links cost a joint each and
#: the collision sweep is O(links² × poses), so depth there goes into sub-parts.
PART_CAP: dict[str, int] = {
    Language.BLENDER.value: 20,
    Language.THREEJS.value: 20,
    Language.CADQUERY.value: 14,
    Language.URDF_BLENDER.value: 10,
}
DEFAULT_PART_CAP = 14
MIN_PARTS = 6
#: a checklist this long (or a brief) is EVIDENCE that the request really needs the target
#: part count; below it the target is an aspiration the gate must not enforce
EVIDENCE_MIN_CHECKLIST = 4

# --------------------------------------------------------------------- "is this a box?"
#: construction verbs / counted repeated features that make a part description more than
#: "a box of size X".  Stems, not bare words: `taper` alone never matches "tapered", which
#: is how every real plan writes it (measured — the un-stemmed version fired the gate on
#: 77 % of the recorded plans instead of 43 %).
_CRAFT = re.compile(
    r"\b(bevel\w*|chamfer\w*|fillet\w*|round(?:ed)?[- ](?:edge|corner|over)\w*|bullnose\w*|taper\w*|groove\w*"
    r"|flute\w*|thread(?:ed|s)?|helix|helical|spline\w*|loft\w*|revolv\w*|extrud\w*|bezier|swept|sweep\w*"
    r"|profile[ds]?|inset\w*|boolean\w*|subdivi\w*|smooth[- ]shad\w*|array\w*|lattice\w*|slot(?:s|ted)?"
    r"|notch\w*|ribs?|louvre\w*|louver\w*|knurl\w*|serrat\w*|scallop\w*|hollow\w*|shell(?:ed)?|cut-?out\w*"
    r"|recess\w*|emboss\w*|moulding\w*|molding\w*|curv\w*|arc\b|arch\w*|spiral\w*|dome[ds]?|domed|ogee"
    r"|scroll\w*|pierc\w*|perforat\w*|crown\w*|swage\w*|bulg\w*|waist\w*|concave|convex|rebate[ds]?|dovetail\w*"
    r"|mortise\w*|tenon\w*|splay\w*|conical|tapering|beading|reeded|turned|rounded|radius|radii)\b"
    r"|\br\s*=\s*\d",   # "bullnose r=10 mm" — a stated radius is construction, not a box
    re.I)
_COUNTED = re.compile(
    r"\b\d+\s*(x|×)?\s*(slats?|spokes?|teeth|dentils?|panes?|rafters?|bolts?|screws?|rivets?|holes?|segments?"
    r"|facets?|ribs?|flutes?|turns?|steps?|rings?|wires?|bars?|louvres?|louvers?|vents?|grooves?|studs?|pads?)\b",
    re.I)
#: the graphics analogue of :data:`_CRAFT`: a PASS is not "a box" when it names a real
#: technique.  Object craft words (bevel, taper, flute) never appear in a shader plan, so
#: the object vocabulary would flag every pass ever written.
_GFX_CRAFT = re.compile(
    r"\b(fbm|noise|hash|sdf|raymarch|ray[- ]?march|march|domain[- ]?warp|voronoi|worley|curl|advect|feedback"
    r"|parallax|refract|reflect|fresnel|bokeh|blur|bloom|vignette|aberration|tonemap|gamma|dither|grain"
    r"|particle|instanc|scatter|caustic|godray|god[- ]ray|volumetric|shadow|ao\b|ambient occlusion|palette"
    r"|gradient|dissolve|erosion|turbulen|flow[- ]?map|displac|tessell|subsurface|halftone|posteris|posteriz)\b",
    re.I)
#: two or more numeric literals make a pass description a recipe rather than an adjective
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def has_real_detail(text: str) -> bool:
    """True when a part description says something a box cannot say."""
    return bool(_CRAFT.search(text) or _COUNTED.search(text))


def has_pass_detail(text: str) -> bool:
    """True when a graphics pass description names a technique or carries a recipe."""
    return bool(_GFX_CRAFT.search(text)) or len(_NUMBER.findall(text)) >= 2


@dataclass(frozen=True)
class PlanBudget:
    """What this request is worth, in plan units.  ``target_parts`` is what the prompt
    asks the planner for; ``min_parts`` is what the quality gate insists on."""

    target_parts: int
    min_parts: int
    cap_parts: int
    target_leaves: int
    min_detail_parts: int
    min_assemblies: int
    reason: str = ""
    #: is there EVIDENCE that this request needs ``target_parts``?  A checklist of four or
    #: more items, or a brief.  Without it ``target_parts`` is an aspiration the prompt
    #: states and the quality gate must not enforce: "a stool" is allowed to be four parts.
    evidence: bool = False

    def as_dict(self) -> dict[str, int | str | bool]:
        return {"target_parts": self.target_parts, "min_parts": self.min_parts, "cap_parts": self.cap_parts,
                "target_leaves": self.target_leaves, "min_detail_parts": self.min_detail_parts,
                "min_assemblies": self.min_assemblies, "budget_evidence": self.evidence, "reason": self.reason}


#: the graphics track counts PASSES, not parts: more than ~6 stops being a plan and starts
#: being a wish-list.  The floor is 1 (D107): pass count correlated with the judge on
#: graphics_v2 (visual_richness +0.40, mean 3.1 passes), but a correlation is not a requirement,
#: and a mandated layer stack was the graphics twin of D106's three-depth-layer rule.  Only a
#: checklist long enough to be evidence (``EVIDENCE_MIN_CHECKLIST``) raises a floor.
GRAPHICS_CAP = 6
GRAPHICS_MIN = 1


def plan_budget(spec: Spec, brief: EngineeringBrief | None = None) -> PlanBudget:
    """Derive the plan's size budget from the REQUEST, not from a constant.

    Drivers: the user's own checklist (``must_have``), and — when a brief exists — the
    sub-assemblies × signature features a real one has.  Capped by the language's
    practical limit (:data:`PART_CAP`)."""
    if spec.track is Track.GRAPHICS:
        return _graphics_budget(spec)
    cap = PART_CAP.get(spec.language.value, DEFAULT_PART_CAP)
    n_must = len(spec.constraints.must_have)
    reasons = []
    want = 0
    if n_must:
        # one part (or sub-part) per checklist item is PARITY, not padding: the measured
        # ratio on the hard battery was 0.91 parts per checklist item, and the runs with the
        # highest ratio scored best on intent_fidelity (+0.26).
        want = max(want, n_must)
        reasons.append(f"{n_must} checklist items")
    n_visible = 0
    if brief is not None:
        n_sub, n_sig = len(brief.sub_assemblies), len(brief.signature_features)
        if n_sub + n_sig:
            want = max(want, n_sub + n_sig)
            reasons.append(f"{n_sub} sub-assemblies + {n_sig} signature features")
        # externally visible features drive the LEAF budget, not the top-level part count:
        # "six vertical flutes on the body" is a sub-part of the body, not a part of its own.
        n_visible = len(brief.visible_from_outside)
    # ``target`` sizes the answer (plan_tokens) and never drops below MIN_PARTS; the FLOOR is the
    # request's own count, so a 4-item checklist is never told it needs 6 parts (D107)
    target = max(MIN_PARTS, min(cap, int(want)))
    floor = max(2, int(round(0.85 * min(cap, int(want)))))
    min_assemblies = 0
    if brief is not None:
        min_assemblies = min(3, sum(1 for s in brief.sub_assemblies if len(s.parts) >= 3))
    elif target >= 10:
        min_assemblies = 1
    return PlanBudget(
        target_parts=target,
        min_parts=floor,
        cap_parts=cap,
        target_leaves=max(target + 4, int(round(1.8 * target)), n_visible + 2 * min_assemblies),
        min_detail_parts=min(floor, max(2, int(round(0.35 * target)))),
        min_assemblies=min_assemblies,
        reason="; ".join(reasons) or "no checklist",
        evidence=n_must >= EVIDENCE_MIN_CHECKLIST or brief is not None,
    )


def _graphics_budget(spec: Spec) -> PlanBudget:
    """Pass budget for the graphics track (see :data:`GRAPHICS_CAP`)."""
    n_must = len(spec.constraints.must_have)
    target = max(GRAPHICS_MIN, min(GRAPHICS_CAP, GRAPHICS_MIN + (n_must + 1) // 3))
    return PlanBudget(
        target_parts=target, min_parts=max(GRAPHICS_MIN, target - 1), cap_parts=GRAPHICS_CAP,
        target_leaves=4 * target, min_detail_parts=max(1, target - 1), min_assemblies=0,
        reason=f"{n_must} checklist items" if n_must else "no checklist",
        evidence=n_must >= EVIDENCE_MIN_CHECKLIST)


def budget_block(budget: PlanBudget, *, unit: str = "parts") -> str:
    """The budget as a prompt block for the planner's user message.

    A floor and a detail minimum are stated only when the request is EVIDENCE of them (a brief,
    or a checklist of :data:`EVIDENCE_MIN_CHECKLIST` items) — the only case the quality gate
    enforces them; otherwise the block states the ceiling and nothing that sets a count (D107)."""
    lines = [f"PLAN BUDGET (derived from this request: {budget.reason}):"]
    if budget.evidence:
        lines.append(f"- plan at least {budget.min_parts} top-level {unit} (ceiling {budget.cap_parts}); "
                     "a plan under that is re-asked.")
        lines.append(f"- at least {budget.min_detail_parts} of them say what a plain "
                     + ("fill cannot: the technique by name, or the pass's own quantities." if unit == "passes" else
                        "primitive cannot, where the real object has it: a profile, an edge radius, a counted "
                        "repeat, or the sub-shapes it is made of.")
                     + " A plan with fewer is re-asked.")
    else:
        lines.append(f"- as many top-level {unit} as the " + ("effect" if unit == "passes" else "object")
                     + f" really has (ceiling {budget.cap_parts}); nothing here sets a count.")
    if unit != "passes":
        lines.append("- `children` for a part a fitter would call an assembly rather than a shape.")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- quality gate
def plan_quality_complaint(plan_obj: Any, budget: PlanBudget, *, unit: str = "parts") -> str:
    """"" when the plan is worth building; otherwise the SPECIFIC complaint to re-ask with.

    Two classes of complaint.  **Hard** ones fire on their own because they mean the plan
    cannot carry the request: too few parts for the checklist, or boxes-at-different-sizes
    (too few parts saying anything a primitive cannot).  **Soft** ones — no assemblies
    nested, one material for the whole object — ride along with a hard one but never
    trigger a re-ask by themselves, because each is routinely correct on its own (a
    wrought-iron arch really is all one material).

    Never absolute: every threshold comes from ``budget``, which comes from the request.
    Measured retro-fire rate on the 86 recorded runs is in the wave report."""
    parts: list[Any] = list(getattr(plan_obj, "parts", None) or getattr(plan_obj, "passes", None) or [])
    if not parts:
        return ""
    nest = "elements" if unit == "passes" else "children"
    hard: list[str] = []
    soft: list[str] = []
    if budget.evidence and len(parts) < budget.min_parts:
        hard.append(
            f"Only {len(parts)} {unit} for a request that needs at least {budget.min_parts} "
            f"({budget.reason}). Split the ones that are really assemblies and add the features you skipped — "
            f"do NOT pad with duplicates.")
    detector = has_pass_detail if unit == "passes" else has_real_detail
    detailed = [p for p in parts if detector(f"{p.description} {getattr(p, 'detail_hint', '')}")
                or getattr(p, "children", None) or getattr(p, "elements", None)]
    if len(detailed) < budget.min_detail_parts:
        plain = [p.name for p in parts if p not in detailed][:8]
        # enforced only on EVIDENCE (a brief, or a long checklist): without it the request never
        # said the object has shaping a primitive cannot express — "a stool" may be four boxes
        (hard if budget.evidence else soft).append(
            f"This plan is boxes at different sizes: only {len(detailed)} of {len(parts)} {unit} describe anything a "
            f"plain primitive cannot express, and this request needs at least {budget.min_detail_parts}. Rewrite "
            f"{', '.join(plain)} where the real thing has it: a profile, a stated radius or edge "
            f"treatment, a counted repeat, or the sub-shapes it is actually made of.")
    if budget.min_assemblies:
        n_nested = sum(1 for p in parts if getattr(p, nest, None))
        if n_nested < budget.min_assemblies:
            soft.append(
                f"{n_nested} of the {unit} use `{nest}`, but this request has at least {budget.min_assemblies} real "
                f"sub-assemblies. Give each of them its `{nest}`" +
                ("" if unit == "passes" else " with their own bboxes (inside the parent's) and materials") +
                f"; leave the simple {unit} alone.")
    materials = {(getattr(p, "material", "") or "").strip().lower() for p in parts} - {""}
    if unit == "parts" and len(parts) >= 5 and len(materials) <= 1:
        soft.append("Every part has the same material (or none). Real objects mix materials — give each part its "
                    "own material with a colour.")
    if not hard:
        return ""
    return ("Your plan is valid but not worth building yet. Fix EXACTLY these problems and return the full corrected "
            "plan JSON again (same schema), keeping everything that was already good:\n- " + "\n- ".join(hard + soft))


# ----------------------------------------------------------------------------- enrichment
def _fold_pass(ps: Any) -> None:
    """Fold a graphics pass's ``elements`` + ``detail_hint`` into its ``description``.

    ``graphics.passes_table`` prints ``| name | kind | description |`` — one markdown
    row — so the fold must stay on a single line and must be idempotent."""
    if not (ps.elements or ps.detail_hint) or " · " in ps.description:
        return
    bits = [ps.description.rstrip().rstrip(".")]
    if ps.elements:
        bits.append("draws: " + " · ".join(e.strip() for e in ps.elements))
    if ps.detail_hint:
        bits.append(f"what makes it real: {ps.detail_hint.strip()}")
    ps.description = " · ".join(bits).replace("\n", " ").replace("|", "/")


def enrich_plan(plan_obj: Any, brief: EngineeringBrief | None) -> Any:
    """Fold what the BRIEF knows back into the plan's own fields, after validation.

    A part's own depth needs no folding: ``tracks/prompting.py`` renders
    ``PartPlan.children`` and ``PartPlan.detail_hint`` straight from the typed fields, so
    copying them into ``description`` would print every sub-part twice.  A graphics PASS is
    the exception — ``graphics.passes_table`` renders only ``description`` — so
    ``elements`` are folded there, on ONE line because that table is markdown.

    * the brief's signature features become ``should`` acceptance items — ``should``, not
      ``must``: a flash brief's private wishes must never trip ``missing_must_acceptance``,
      which caps a finished object at 0.60 (the lesson ``ensure_acceptance`` already
      records for the scene track);
    * ``style_notes`` gains the reference instance and what the object does NOT have —
      the two brief facts that change how the whole object is built rather than one part.
    """
    for ps in getattr(plan_obj, "passes", None) or []:
        _fold_pass(ps)
    if brief is None:
        return plan_obj
    items: list[AcceptanceItem] = list(getattr(plan_obj, "acceptance", None) or [])
    for feature in brief.signature_features:
        add_acceptance_item(items, "sig", f"Signature feature visible: {feature.strip()}", "visual", priority="should")
    if hasattr(plan_obj, "acceptance"):
        plan_obj.acceptance = items
    notes = getattr(plan_obj, "style_notes", "")
    extra = []
    if brief.reference and brief.reference.lower() not in notes.lower():
        extra.append(f"Reference instance: {brief.reference}.")
    if brief.not_present:
        extra.append("Does NOT have: " + "; ".join(brief.not_present) + ".")
    if extra and hasattr(plan_obj, "style_notes"):
        plan_obj.style_notes = " ".join([notes.strip(), *extra]).strip()
    return plan_obj


# ===================================================================== worked examples
def plan_example(track: Track, language: Language | None = None) -> dict[str, Any]:
    """Compact, valid worked example per track (concrete beats abstract) — and, where a language
    plans differently, per language: a scene_blender plan names ``bpy`` modules where a three.js
    plan names ``threejs`` ones (``SCENE_ASSET_KINDS``).  Its NUMBERS are the same Y-up GLB-frame
    numbers (D12: the plan never changes frame; the skeleton and the prompts convert for the
    author), so the two examples differ in the asset kinds and nothing else."""
    if track is Track.SCENE and language is Language.SCENE_BLENDER:
        example = plan_example(Track.SCENE)
        for asset in example["assets"]:
            if asset["kind"] == "threejs":
                asset["kind"] = "bpy"
        return example
    if track is Track.GRAPHICS:
        # the SCHEMA on an effect nobody asked for (no battery prompt is a tide pool) — two passes
        # because this effect has two, never a pass list, a layering or a post chain to copy
        return {
            "title": "Tide pool ripples", "summary": "Looking straight down into a shallow tide pool: pebbles and weed on "
            "the floor, rings spreading from drops that fall at irregular intervals; light shifts over the pebbles.",
            "style": "naturalistic: wet slate greys and ochre pebbles, olive weed, cold sky-blue reflections; soft, glossy",
            "resolution": [1280, 720], "duration_s": 8.0,
            "passes": [
                {"name": "PoolFloor", "kind": "fullscreen", "description": "rounded pebbles 0.03-0.08 of screen height packed "
                 "over sand, a few olive weed strands, seen through the water and displaced by the surface's slope"},
                {"name": "SurfaceRipples", "kind": "fullscreen", "description": "rings expanding from drop points at about 0.15 "
                 "screen heights per second and fading over 3 s; their slope bends the floor and carries the sky's reflection",
                 "elements": ["drop rings, 1-3 alive at a time"],
                 "detail_hint": "two rings that cross interfere instead of one overwriting the other"},
            ],
            "uniforms": ["u_time", "u_resolution"],
            "motion": "new drops land at irregular intervals and their rings spread and fade; the weed sways slowly; no hard cuts",
            "key_visuals": ["pebbles under shallow water", "expanding ripple rings", "light shifting over the floor"],
            "acceptance": [
                {"id": "a1", "text": "Ripple rings visibly expand between t=0 and t=1", "how": "visual", "priority": "must"},
                {"id": "a2", "text": "Pebbles are visible through the water and bend where a ring passes", "how": "visual", "priority": "must"},
                {"id": "a3", "text": "Frames change over time (no static image)", "how": "probe", "priority": "must"},
            ],
        }
    if track is Track.SCENE:
        # ONE place's plan, shaped like that place (an L of quay on the west, open water east and
        # running OUT of the bounds, the mole and the village street running out too, open quay-head
        # ground the zones do not cover) — never a layout to copy.  Its bounds centre, Quay bbox and
        # Overview position are pinned by tests/scene_blender/test_prompts.
        return {
            "title": "Harbour at dusk", "summary": "Small fishing harbour: an L of granite quay on the west side, two boats "
            "moored against it, the basin opening east to the sea; the mole runs out of the bounds to the north-east.",
            "setting": "Breton coast, 1960s, clear dusk", "mood": "calm, warm low sun",
            "bounds": {"center": [0, 5, 0], "extents": [100, 30, 70]},
            "environment": "Sky zenith 0x2b3f6e to horizon 0xf0a060, sun azimuth 250° elevation 8°; fog 0xe8a070 near 60 m far "
            "320 m; sea plane y=0 colour 0x2f4a55 to the east horizon; the quay deck is flat at y=1.2 along x -40..0 and "
            "the village street climbs 3 m out of the bounds to the west; the mole continues 60 m past the bounds to the "
            "north-east with the lighthouse at its end; low headland 300 m off to the south.",
            "zones": [
                {"name": "Quay", "description": "L-shaped granite quay deck at y=1.2 along the west and north edges; bollards every "
                 "6 m on the edge, crates stacked by the net shed; seen by Overview and QuayEye",
                 "bbox": {"center": [-20, 1.2, 0], "extents": [40, 3, 60]}, "contents": ["Bollard", "Crate"]},
                {"name": "Water", "description": "the basin: two boats moored bow-in against the quay edge 12 m apart, open water "
                 "east of them past the bounds to the sea (the zone runs out of the bounds on the east; the quay head "
                 "north of z=-30 is open ground no zone covers)", "bbox": {"center": [30, 0, 5], "extents": [60, 1, 60]},
                 "contents": ["FishingBoat"]},
            ],
            "assets": [
                {"name": "FishingBoat", "kind": "threejs", "description": "8 m wooden boat, wheelhouse aft, red hull", "approx_size_m": [8, 3.5, 3], "instances_hint": 2},
                {"name": "Bollard", "kind": "threejs", "description": "cast-iron mooring bollard", "approx_size_m": [0.3, 0.5, 0.3], "instances_hint": 6},
                {"name": "Crate", "kind": "blender_glb", "description": "wooden fish crate, slatted", "approx_size_m": [0.6, 0.3, 0.4], "instances_hint": 8},
            ],
            "effects": [{"name": "WaterRipple", "kind": "glsl_material", "description": "animated normal ripples on the sea plane", "target": "Water"}],
            "animation": ["boats bob ±0.05 m at 0.3 Hz", "water ripples scroll"],
            "cameras": [{"name": "Overview", "position": [38, 4, -30], "look_at": [-12, 1.5, 6], "fov": 48,
                         "purpose": "establishing: low over the basin from the mole to the north-east, looking back along the quay "
                                    "at the two moored boats; the village street climbs out of frame behind them, open water right"},
                        {"name": "QuayEye", "position": [-5, 2.8, 25], "look_at": [8, 1, -8], "fov": 60,
                         "purpose": "eye level on the quay deck, looking along the edge at the nearer boat"}],
            "acceptance": [{"id": "a1", "text": "Two boats float on the water inside the basin", "how": "visual", "priority": "must"},
                           {"id": "a2", "text": "Quay top at y≈1.2 m, boats' waterline at y≈0", "how": "probe", "priority": "must"}],
        }
    # The SCHEMA on an object nobody asks for (a profile, a counted repeat, a nested assembly,
    # an instanced row) — never a part list, a layout or numbers to copy.  Upright's one-copy
    # bbox is what c3d-repeats-and-mirrors points at.
    parts = [
        {"name": "Base", "role": "weighted foot", "description": "disc 400 mm dia, 30 mm thick, top edge rounded r=8 mm, "
         "four 20 mm square sockets for the uprights",
         "bbox": {"center": [0, 0, 0.015], "extents": [0.40, 0.40, 0.03]}, "material": "cast iron, satin black", "attach_to": None,
         "symmetry": "none", "instances": 1, "children": [], "detail_hint": "the rounded edge and the four sockets are what reads at floor level"},
        {"name": "Upright", "role": "vertical bar", "description": "square bar 20 mm, upper 100 mm tapered to 14 mm, foot seated in a base socket",
         "bbox": {"center": [0.16, 0, 0.455], "extents": [0.02, 0.02, 0.85]}, "material": "oiled ash, pale", "attach_to": "Base",
         "symmetry": "radial", "instances": 4, "children": [], "detail_hint": ""},
        {"name": "TopFrame", "role": "pegged ring the bunches hang from", "description": "flat ring on the upright tops, crossed by "
         "two bars, pegs round the rim and a finial at the crossing",
         "bbox": {"center": [0, 0, 0.905], "extents": [0.42, 0.42, 0.05]}, "material": "oiled ash, pale", "attach_to": "Upright",
         "symmetry": "radial", "instances": 1, "detail_hint": "an assembly, not a disc: the gaps between ring, bars and pegs show from every side",
         "children": [
             {"name": "Ring", "role": "flat ring", "description": "outer dia 340 mm, 20 mm wide, 12 mm thick, edges rounded r=3 mm",
              "bbox": {"center": [0, 0, 0.886], "extents": [0.34, 0.34, 0.012]}, "instances": 1},
             {"name": "CrossBar", "role": "bar across the ring", "description": "20 × 12 mm bar, ends halved into the ring",
              "bbox": {"center": [0, 0, 0.886], "extents": [0.34, 0.02, 0.012]}, "instances": 2},
             {"name": "Peg", "role": "hanging peg", "description": "12 turned pegs 10 mm dia, 40 mm proud of the rim, ends domed",
              "bbox": {"center": [0.19, 0, 0.886], "extents": [0.04, 0.01, 0.01]}, "instances": 12},
             {"name": "Finial", "role": "knob at the crossing", "description": "turned knob 40 mm dia on a 10 mm neck",
              "bbox": {"center": [0, 0, 0.911], "extents": [0.04, 0.04, 0.038]}, "instances": 1},
         ]},
    ]
    base: dict[str, Any] = {
        "object_name": "HerbDryingRack",
        "summary": "Free-standing herb drying rack: a round iron foot, four tapered uprights and a pegged top ring, 0.42 × 0.42 × 0.93 m.",
        "overall_bbox": {"center": [0, 0, 0.465], "extents": [0.42, 0.42, 0.93]},
        "style_notes": "Scandinavian kitchen craft: pale oiled ash on a black iron foot, rounded edges, no ornament.",
        "parts": parts,
        "acceptance": [{"id": "a1", "text": "Overall height 0.93 m ± 0.02 m", "how": "measure", "priority": "must"},
                       {"id": "a2", "text": "Four uprights stand in the foot, which rests flat on the ground", "how": "visual", "priority": "must"}],
    }
    if track is Track.ARTICULATED_OBJECT:
        base["object_name"] = "DeskDrawerUnit"
        base["summary"] = "Painted desk drawer unit, 0.40 × 0.50 × 0.60 m: one drawer on side runners."
        base["overall_bbox"] = {"center": [0, 0, 0.3], "extents": [0.4, 0.5, 0.6]}
        base["style_notes"] = "Plain painted carcass, a shadow gap round the drawer, one metal pull."
        base["parts"] = [
            {"name": "Cabinet", "role": "fixed carcass", "description": "18 mm carcass, 6 mm rebated back, 3 mm shadow gap round the drawer opening",
             "bbox": {"center": [0, 0, 0.3], "extents": [0.4, 0.5, 0.6]}, "material": "painted MDF", "attach_to": None, "symmetry": "none", "instances": 1,
             "children": [], "detail_hint": "the opening's shadow gap is what makes the drawer read as a drawer"},
            {"name": "Drawer", "role": "sliding drawer (ONE link, four sub-parts)", "description": "drawer box running on side runners",
             "bbox": {"center": [0, -0.02, 0.45], "extents": [0.36, 0.46, 0.2]}, "material": "painted MDF", "attach_to": "Cabinet", "symmetry": "none", "instances": 1,
             "detail_hint": "one rigid body, but never one box",
             "children": [
                 {"name": "Front", "role": "drawer face", "description": "18 mm face, 4 mm bullnose, overhangs the box 8 mm on every side",
                  "bbox": {"center": [0, -0.24, 0.45], "extents": [0.36, 0.018, 0.19]}, "material": "painted MDF", "instances": 1},
                 {"name": "Box", "role": "drawer body", "description": "12 mm sides, 8 mm base set into a 6 mm groove, open top",
                  "bbox": {"center": [0, 0.0, 0.45], "extents": [0.33, 0.42, 0.17]}, "material": "birch ply", "instances": 1},
                 {"name": "Knob", "role": "pull", "description": "turned knob 32 mm dia, 22 mm proud, on a 10 mm neck",
                  "bbox": {"center": [0, -0.26, 0.45], "extents": [0.032, 0.032, 0.032]}, "material": "brushed steel", "instances": 1},
                 {"name": "Runner", "role": "side runner", "description": "10 × 10 mm strip along each side, full depth",
                  "bbox": {"center": [0.17, 0.0, 0.40], "extents": [0.01, 0.42, 0.01]}, "material": "birch ply", "instances": 2},
             ]},
        ]
        base["root_link"] = "Cabinet"
        base["joints"] = [{"name": "DrawerSlide", "type": "prismatic", "parent": "Cabinet", "child": "Drawer", "axis": [0, -1, 0],
                           "pivot": [0, -0.02, 0.45], "lower": 0.0, "upper": 0.35, "rest": 0.0, "motion": "drawer pulls out towards -Y"}]
        # its own checks, not the chair's: the base's seat-height / four-legs items describe another object
        base["acceptance"] = [{"id": "a1", "text": "Cabinet top at 0.60 m ± 0.02 m above the ground", "how": "measure", "priority": "must"},
                              {"id": "a2", "text": "Cabinet stands on the ground at rest", "how": "visual", "priority": "must"},
                              {"id": "j1", "text": "Drawer slides out 0.35 m along -Y without penetrating the cabinet", "how": "articulation", "priority": "must"}]
    return base


P = TypeVar("P", bound=BaseModel)

#: re-ask budgets — a plan the SCHEMA rejects and a plan the QUALITY gate rejects are
#: different failures and get separate chances (see ``plan``).
MAX_VALIDATION_REASKS = 2  # was 1: compare_art_v2 lost 5 of 14 articulated prompts at this gate (2026-08-25)
MAX_QUALITY_REASKS = 1
#: a validation failure whose plan is DEGENERATE (joints naming links the plan never lists)
#: is answered by re-sampling from the original prompt instead of editing the broken answer
#: in context; at most this many times per plan.  Measured 2026-09-02: 3-4 % of plan calls
#: end this way, and both in-context re-asks reproduce the same broken plan verbatim.
MAX_PLAN_RESTARTS = 1
#: Output room for the plan call, sized from the plan budget.  A deep plan is much longer
#: JSON than a flat one AND Gemini 3.x bills its thinking against the same ceiling, so the
#: flat 24 000 that served 8 box-parts truncates a 12-part plan with sub-parts —
#: ``finish_reason=MAX_TOKENS``, no JSON, and the whole RUN dies at the plan stage.  This
#: was measured, not guessed: the first live pass of this wave failed exactly there on both
#: prompts.  Callers may raise the floor; this only ever raises it further.
PLAN_TOKENS_PER_PART = 600
PLAN_TOKENS_PER_LEAF = 400
PLAN_TOKENS_MAX = 65_536   # the model's declared output ceiling
#: one more attempt when the model truncates anyway, with half again as much room
TRUNCATION_GROWTH = 1.5
#: retry budget (``ChatRequest.max_wait_s``) for one planner call — the owner's floor
#: (2026-08-27): 15 minutes before a plan may be cut off.  For scale, the call itself is
#: 13.7 s p50 / 32 s p90 / 76 s max (audit 2026-08-26 §2); the flat 300 s that once stood
#: here is what killed grown re-asks (see the deadline-vs-answer block below).
PLAN_MAX_WAIT_S = 900.0
#: ...but a deadline must fit the ANSWER it asked for, or the tokens are billed and then
#: thrown away on a timeout.  Measured over 395 real planner calls (2026-08-27): output
#: runs at 145 tok/s p50 and 60 tok/s p10, and the largest answers seen were 32 k tokens
#: in 152-199 s.  At the p10 rate a 32 k answer needs 532 s and a 65 k one 1 089 s, so the
#: flat 300 s guaranteed a timeout the moment a re-ask grew the budget — which is exactly
#: how art_med_tool_chest and fancy_bl_windsor_chair died at the planner (2026-08-27).
PLAN_SLOW_TOKENS_PER_S = 60.0
PLAN_WAIT_OVERHEAD_S = 60.0


#: a retry after truncation may wait this much longer than the size rule says (the pro
#: planner streams a 55k-token answer slower than PLAN_SLOW_TOKENS_PER_S, 2026-08-28)
TRUNCATION_WAIT_SCALE = 1.5


def plan_wait_s(tokens: int, guard: object | None = None, *, scale: float = 1.0) -> float:
    """How long one planner call may take, given the answer size it asked for.

    Never below :data:`PLAN_MAX_WAIT_S`, and clipped to the wall clock the run has left
    (``BudgetGuard.timeout_s``) so a big plan cannot outlive its own run."""
    want = max(PLAN_MAX_WAIT_S, tokens / PLAN_SLOW_TOKENS_PER_S + PLAN_WAIT_OVERHEAD_S) * scale
    fn = getattr(guard, "timeout_s", None)
    return float(fn(want, floor_s=PLAN_MAX_WAIT_S)) if callable(fn) else want


def plan_tokens(budget: PlanBudget, floor: int) -> int:
    """Output-token ceiling for a plan of this size (never below the caller's ``floor``)."""
    want = floor + PLAN_TOKENS_PER_PART * budget.target_parts + PLAN_TOKENS_PER_LEAF * budget.target_leaves
    return min(PLAN_TOKENS_MAX, max(floor, want))


TRUNCATION_NOTE = ("\n\nCOMPACT ANSWER REQUIRED: your previous answer did not fit in the output budget "
                   "({tokens} tokens). Return the same plan with every `description` at most 25 words, at most 6 "
                   "`children` per part and nothing outside the JSON. Answer directly without long deliberation.")


def _with_note(msg: ChatMessage, note: str) -> ChatMessage:
    """The same user message with ``note`` appended to its text (images kept)."""
    images = [p for p in msg.parts if isinstance(p, ImagePart)] or None
    return ChatMessage.user(msg.text + note, images=images)


_PLACEHOLDERS = {"string", "str", "name", "text", "..."}


def _schema_echo(raw: dict[str, Any]) -> str:
    """"" or what in ``raw`` is a JSON-schema placeholder (``"name": "string"``) rather than content.

    compare_art_v4_pf0 (2026-08-28): flash answered a casement-window brief with a plan
    whose joint was ``{"name": "string", "axis": [0, 0, 0], ...}`` and failed the zero-axis
    validator three times; the re-ask echoed 'zero axis', which was not the problem."""
    bad: list[str] = []
    if str(raw.get("object_name", "")).strip().lower() in _PLACEHOLDERS:
        bad.append("object_name")
    for key in ("parts", "joints", "zones", "assets", "passes"):
        for i, item in enumerate(raw.get(key) or []):
            if isinstance(item, dict) and str(item.get("name", "")).strip().lower() in _PLACEHOLDERS:
                bad.append(f"{key}[{i}].name")
    return ", ".join(bad[:6])


def missing_link_names(raw: Any) -> list[str]:
    """Link names the joints reference that ``parts`` does not define (snake-compared).

    The one failure class measured on 200 plan calls (2026-09-02, both trees, 3-4 %): the
    planner writes ONE top-level part and hangs every joint off links it never lists, then
    reproduces that answer through both in-context re-asks."""
    if not isinstance(raw, dict):
        return []
    parts = raw.get("parts")
    joints = raw.get("joints")
    if not isinstance(parts, list) or not isinstance(joints, list):
        return []
    known = {to_snake(str(p.get("name", ""))) for p in parts if isinstance(p, dict)}
    known |= {to_snake(str(c.get("name", ""))) for p in parts if isinstance(p, dict)
              for c in (p.get("children") or []) if isinstance(c, dict)}
    missing: list[str] = []
    for j in joints:
        if not isinstance(j, dict):
            continue
        for side in ("parent", "child"):
            name = str(j.get(side, "")).strip()
            if name and to_snake(name) not in known and name not in missing:
                missing.append(name)
    return missing


def restart_note(raw: Any, missing: list[str], budget: PlanBudget) -> str:
    """The user turn that replaces the whole conversation when a plan comes back degenerate."""
    n_parts = len(raw.get("parts") or []) if isinstance(raw, dict) else 0
    return (
        "\n\nYour previous attempt listed only "
        f"{n_parts} top-level part(s) and then referenced links it never defined: "
        f"{', '.join(missing[:10])}. Start again from this request. Rules that answer is missing:\n"
        f"- `parts` must contain EVERY link a joint names, each with its own name, bbox and material "
        f"(at least {budget.min_parts} parts for this request).\n"
        "- `joints[].parent` and `joints[].child` must be spelled exactly like those part names.\n"
        "- Nothing that a joint moves may live in `children`; sub-parts are rigid detail only."
    )


def restart_enabled() -> bool:
    """Kill switch for the degenerate-plan restart (``C3D_PLAN_RESTART``, ON; docs/COST.md §30)."""
    return get_settings().plan_restart


#: an invalid plan attempt is written here, so a run that DIES at the plan stage still says
#: what the model wrote.  Pydantic truncates the offending value in its message, so the
#: event log alone cannot answer "what did it actually write?" — the question every
#: plan-stage failure class starts from (eval/docs/PAPER_WRITING.md §5.1).
INVALID_PLAN_DIR = "stages/plan/invalid"
#: bound per file: a plan is a few kB; anything larger is a runaway answer and the head of
#: it is what says so
MAX_INVALID_PLAN_BYTES = 200_000


def _record_invalid(ws: Workspace, attempt: int, raw: Any, text: str) -> None:
    """Write the rejected answer beside the run.  Never raises: this is a diagnostic."""
    try:
        body = json.dumps(raw, indent=1, default=str) if isinstance(raw, dict) else (text or "")
        out = ws.root / INVALID_PLAN_DIR
        out.mkdir(parents=True, exist_ok=True)
        (out / f"attempt{attempt:02d}.json").write_text(body[:MAX_INVALID_PLAN_BYTES])
    except (OSError, TypeError, ValueError) as e:  # noqa: BLE001 — a diagnostic never fails a run
        log.debug("could not record the invalid plan attempt: %s", e)


def degenerate_plan(raw: Any, budget: PlanBudget) -> bool:
    """A plan too small to be an attempt at this request: a third of the floor or less.

    The quality complaint owns everything above that line — this is only the collapsed
    answer (usually ONE top-level part) that the in-context re-ask reproduces instead of
    fixing, which is why it is also the restart's trigger."""
    parts = raw.get("parts") if isinstance(raw, dict) else None
    return isinstance(parts, list) and len(parts) <= max(2, budget.min_parts // 3)


def _thin_plan_note(raw: Any, budget: PlanBudget) -> str:
    """A second line for the validation re-ask when the invalid plan is also far too small.

    compare_art_v4_pf0 (2026-08-28): flash answered an architect-lamp brief with ONE part
    ("base") and a joint naming a link it never listed, three times in a row — the re-ask
    only echoed the unknown-link error, so the model kept fixing the wrong thing."""
    if not degenerate_plan(raw, budget):
        return ""
    parts = raw.get("parts")
    return (f"\nAlso: this plan lists only {len(parts)} part(s) but the request needs at least {budget.min_parts} "
            f"({budget.reason}). Put EVERY link a joint references under `parts` with its own bbox.")


def _truncated(exc: Exception) -> bool:
    """Did this model error mean 'the answer did not fit'?"""
    return "MAX_TOKENS" in str(exc)

#: the planner's system-prompt template per (track, language); ``(track, None)`` is the track's
#: default and the fallback for every language without a row of its own.  A scene_blender
#: director reads the bpy twin: the same plan shape and frame (D12), the bpy library and the
#: ``bpy`` asset kind instead of the three.js effects and ``threejs``.
PLAN_TEMPLATES: dict[tuple[Track, Language | None], str] = {
    (Track.STATIC_OBJECT, None): "tracks/plan_static.j2",
    (Track.ARTICULATED_OBJECT, None): "tracks/plan_articulated.j2",
    (Track.SCENE, None): "tracks/plan_scene.j2",
    (Track.SCENE, Language.SCENE_BLENDER): "tracks/plan_scene_bpy.j2",
    (Track.GRAPHICS, None): "tracks/plan_graphics.j2",   # no 3D frame: passes, uniforms, motion
}
#: the output-token floor ``plan_tokens`` grows from; a graphics plan starts at the model's ceiling
PLAN_OUTPUT_FLOOR = 24_000


def plan_template(track: Track, language: Language | None = None) -> str:
    """The planner template for ``(track, language)``: the language's own row, else the track's."""
    return PLAN_TEMPLATES.get((track, language)) or PLAN_TEMPLATES[(track, None)]


def plan_schema(spec: Spec, plan_model: type[BaseModel]) -> dict[str, Any]:
    """The response schema the planner call carries: a scene's names the asset kinds of ITS
    language (``scene_plan_schema``); every other plan is its model's schema as is."""
    if issubclass(plan_model, ScenePlan):
        return scene_plan_schema(spec.language)
    return plan_model.model_json_schema()


def plan_temperature(track: Track) -> float:
    """What the planner samples at: 0.5 for a shader brief, 0.4 for everything else.  THE value —
    ``record/telemetry`` reports this function, not a default it guesses."""
    return 0.5 if track is Track.GRAPHICS else 0.4


class PlanningError(RuntimeError):
    """The planner could not produce a valid plan after MAX_VALIDATION_REASKS re-asks.

    Carries the ``usage`` already spent so callers can charge the budget even
    when planning fails (a failed re-ask is still paid for)."""

    def __init__(self, message: str, usage: Usage | None = None):
        super().__init__(message)
        self.usage = usage or Usage()


def plan_event_stats(plan_obj: Any) -> dict[str, Any]:
    """The ``plan.done`` payload: a graphics plan counts passes, every other plan parts and zones."""
    passes = getattr(plan_obj, "passes", None)
    if passes is not None:
        return {"n_passes": len(passes), "n_acceptance": len(plan_obj.acceptance)}
    parts = getattr(plan_obj, "parts", []) or []
    return {"n_parts": len(parts),
            "n_zones": len(getattr(plan_obj, "zones", []) or []),
            # depth, so the flywheel can tell a 10-part plan of boxes from a 10-part plan of
            # assemblies without re-reading plan.json
            "n_nested_parts": sum(1 for p in parts if getattr(p, "children", None)),
            "n_subparts": sum(len(getattr(p, "children", None) or []) for p in parts),
            "n_leaves": getattr(plan_obj, "leaf_count", len(parts)),
            "n_acceptance": len(plan_obj.acceptance)}


def plan[P: BaseModel](spec: Spec, model_id: str, plan_model: type[P], ws: Workspace, *, model: Any | None = None,
         events: Any | None = None, budget: Any | None = None) -> P:
    """Plan and write ``ws.plan_path``: optional brief expansion → one structured planner
    call → re-asks (schema, quality, a degenerate-plan restart).  ``model`` may be injected (tests).
    Everything that differs per track is looked up from ``spec.track`` (module docstring);
    the validated plan is finalised by ``ensure_acceptance(normalise_names(...))``.

    The two re-asks have SEPARATE budgets and different complaints:
    ``MAX_VALIDATION_REASKS`` for a plan the schema rejects, ``MAX_QUALITY_REASKS`` for a
    plan that validates but is boxes-at-different-sizes against the derived budget
    (:func:`plan_quality_complaint`).  A quality re-ask never eats
    the validation re-ask, and a plan that fails quality twice is still USED — a mediocre
    plan beats no plan.

    Output room comes from :func:`plan_tokens` (the budget sizes it), and a
    ``finish_reason=MAX_TOKENS`` error buys ONE more attempt with half again as much room
    instead of killing the run — the deeper plan is longer JSON and Gemini bills thinking
    against the same ceiling.

    ``budget`` (the run's ``BudgetGuard``; ``guard`` below, because ``budget`` becomes this
    request's PlanBudget) only clips each call's wait to the clock left; every call is a
    ledger row of its own (the metered model), so success, ``PlanningError`` and a crash on
    a LATER attempt all leave the earlier dollars on the ledger.  ``PlanningError`` carries
    the total usage for a caller that wants it."""
    guard = budget
    events = NULL_EVENTS if events is None else events
    if model is None:
        model = get_chat_model(model_id)
    brief, usage = (None, Usage())
    if brief_enabled(spec):
        brief, usage = expand_brief(spec, model_id, model=model, events=events)
    budget = plan_budget(spec, brief)
    unit = "passes" if spec.track is Track.GRAPHICS else "parts"
    system = build_system_prompt(spec, plan_model, budget=budget)
    user = build_user_prompt(spec, brief=brief, budget=budget, unit=unit)
    images = [ImagePart(path=r.path, label=f"{r.role}: {r.note}".strip(": ")) for r in spec.references]
    messages = [ChatMessage.user(user, images=images or None)]
    base_messages = list(messages)
    schema = plan_schema(spec, plan_model)
    last_error = ""
    invalid = requeried = grown = restarts = 0
    tokens = plan_tokens(budget, PLAN_TOKENS_MAX if spec.track is Track.GRAPHICS else PLAN_OUTPUT_FLOOR)
    thinking = "medium"
    wait_scale = 1.0
    # 4 = the first call + truncation retries; it was 2 + the geometry re-ask's 2 (D49, code
    # removed 2026-09-21) — the total is kept so a default run has the attempts it always had
    for attempt in range(4 + MAX_VALIDATION_REASKS + MAX_QUALITY_REASKS + MAX_PLAN_RESTARTS):
        req = ChatRequest(messages=messages, system=system, response_schema=schema, temperature=plan_temperature(spec.track),
                          thinking=thinking, max_output_tokens=tokens, label=f"planner{'-retry' if attempt else ''}",
                          max_wait_s=plan_wait_s(tokens, guard, scale=wait_scale))
        try:
            resp = model.generate(req)
        except Exception as e:  # noqa: BLE001 — a truncated plan is retryable; anything else is not
            if not (_truncated(e) and tokens < PLAN_TOKENS_MAX):
                raise  # not a truncation, or already at the model ceiling: nothing to grow
            grown += 1
            tokens = min(PLAN_TOKENS_MAX, int(tokens * TRUNCATION_GROWTH))
            # A bigger budget alone does not help when thinking ate the last one: with the pro
            # planner a 55k-token retry ran ~16 min into the provider's deadline (504) and the
            # cell was lost (compare_art_v4_pp, 2026-08-28).  Retry with low thinking and a
            # compact-answer note on the last user turn.
            thinking = "off"  # "low" still streamed past a 990 s attempt budget on 2 of 6 pro cells
            wait_scale = TRUNCATION_WAIT_SCALE
            messages = messages[:-1] + [_with_note(messages[-1], TRUNCATION_NOTE.format(tokens=tokens))]
            events.emit("plan.truncated", attempt=attempt, max_output_tokens=tokens, thinking=thinking)
            continue
        usage = usage + resp.usage
        try:
            raw = resp.parsed if resp.parsed is not None else parse_json_lenient(resp.text)
        except ValueError:  # JsonParseError: nothing parsed — the re-ask below says so
            raw = None
        try:
            if not isinstance(raw, dict):
                raise ValueError(f"planner returned {type(raw).__name__}, expected a JSON object")
            echoed = _schema_echo(raw)
            if echoed:
                raise ValueError(f"the plan echoes the schema's placeholders instead of describing the object: {echoed}. "
                                 "Write the REAL plan: actual part names, real numbers, a unit axis for every joint.")
            result = plan_model.model_validate(raw)
        except (ValidationError, ValueError) as e:
            last_error = str(e)[:4000]
            _record_invalid(ws, attempt, raw, resp.text)
            events.emit("plan.invalid", attempt=attempt, error=last_error[:500])
            missing = missing_link_names(raw)
            # The measured class is BOTH conditions: a collapsed plan (one top-level part)
            # whose joints name links it never listed.  A full plan with one misspelled
            # link is the re-ask's job — restarting throws away parts that were right.
            if (missing and degenerate_plan(raw, budget)
                    and restarts < MAX_PLAN_RESTARTS and restart_enabled()):
                # Editing a degenerate answer in context reproduces it: the model reads its
                # own one-part plan and returns it again (measured, both re-asks, 2026-09-02).
                # Re-sample from the original request instead, with the rule it broke.  This
                # replaces the conversation rather than extending it, so it spends an
                # ATTEMPT, not one of the two re-ask slots the next invalid answer needs.
                restarts += 1
                events.emit("plan.restart", attempt=attempt, n_parts=len(raw.get("parts") or []),
                            missing=missing[:10])
                # rebuilt from the ORIGINAL request, so a compact-answer note an earlier
                # truncation retry added is rebuilt with it: this attempt keeps that
                # retry's smaller `tokens` and thinking="off", and dropping the note
                # while keeping the budget asks for the answer that did not fit.
                note = TRUNCATION_NOTE.format(tokens=tokens) if thinking == "off" else ""
                messages = base_messages[:-1] + [
                    ChatMessage.user(base_messages[-1].text + restart_note(raw, missing, budget) + note,
                                     images=images or None),
                ]
                continue
            invalid += 1
            if invalid > MAX_VALIDATION_REASKS:
                break
            messages = messages + [
                _echo(raw, resp.text),
                ChatMessage.user("Your plan failed validation. Fix EXACTLY these problems and return the full corrected "
                                 f"plan JSON again (same schema):\n{last_error}" + _thin_plan_note(raw, budget)),
            ]
            continue
        complaint = plan_quality_complaint(result, budget, unit=unit) if requeried < MAX_QUALITY_REASKS else ""
        if complaint:
            requeried += 1
            events.emit("plan.thin", attempt=attempt, n_parts=len(getattr(result, "parts", None) or getattr(result, "passes", None) or []),
                        target_parts=budget.target_parts, complaint=complaint[:400])
            messages = messages + [_echo(raw, resp.text), ChatMessage.user(complaint)]
            continue
        normalised = list(getattr(result, "normalisations", None) or [])
        if normalised:
            events.emit("plan.normalised", attempt=attempt, n=len(normalised), items=normalised[:8])
        result = enrich_plan(ensure_acceptance(normalise_names(result), spec), brief)
        ws.write_json(ws.plan_path, result)
        events.emit("plan.done", model=model_id, **plan_event_stats(result),
                    cost_usd=round(usage.cost_usd, 4), prompt_hash=prompt_hash(system), attempt=attempt,
                    brief=brief is not None, quality_reasks=requeried, restarts=restarts, **budget.as_dict())
        if guard is not None:
            guard.check()
        return result
    raise PlanningError(f"plan did not validate after re-ask: {last_error}", usage)


def _echo(raw: Any, text: str) -> ChatMessage:
    """The model's own answer, echoed back so a re-ask edits it instead of restarting."""
    return ChatMessage.assistant(json.dumps(raw)[:20000] if raw is not None else (text or "")[:20000])


# ----------------------------------------------------------------------------- prompts
#: the ``## `` headings of a scene contract the PLANNER acts on: it writes JSON, so it reads the
#: files the builders will own and what is measured on them — not the assembler wiring, the shader
#: loading or the worked modules (the copyability review of 2026-10-15: +38 % on the planner brief)
PLANNER_CONTRACT_SECTIONS: dict[Language, tuple[str, ...]] = {
    Language.SCENE_THREEJS: ("Files", "createScene return value", "What is measured", "Naming"),
    Language.SCENE_BLENDER: ("Files", "Frame and units", "What the renderer does", "Motion", "Naming", "Placement",
                             "What else the gates measure", "Interiors"),
}


def planner_contract(lang: Language) -> str:
    """The contract text a planner of ``lang`` reads: the scene languages' measured sections only,
    every other language's contract whole."""
    text = language_text(lang, "contract.md")
    headings = PLANNER_CONTRACT_SECTIONS.get(lang)
    return contract_sections(text, headings) if headings else text


def build_system_prompt(spec: Spec, plan_model: type[BaseModel], *, budget: PlanBudget | None = None) -> str:
    lang: Language = spec.language
    budget = budget or plan_budget(spec)
    return render(
        plan_template(spec.track, lang),
        track=spec.track.value,
        language=lang.value,
        frame_doc=frame_doc(LANGUAGE_FRAME[lang.value]),
        contract=planner_contract(lang),
        example_json=json.dumps(plan_example(spec.track, lang), indent=1),
        schema_fields=", ".join(plan_model.model_json_schema().get("properties", {}).keys()),
        target_parts=budget.target_parts,
        min_parts=budget.min_parts,
        cap_parts=budget.cap_parts,
    )


def build_user_prompt(spec: Spec, *, brief: Any | None = None, budget: PlanBudget | None = None,
                      unit: str = "parts") -> str:
    lines = [f"REQUEST: {spec.prompt}", "", "CONSTRAINTS:", constraints_text(spec)]
    block = brief_block(brief)
    if block:
        lines += ["", block]
    if spec.track is not Track.SCENE:   # a scene is planned in zones + assets, not parts
        lines += ["", budget_block(budget or plan_budget(spec), unit=unit)]
    if spec.references:
        lines += ["", f"REFERENCE IMAGES: {len(spec.references)} attached — match their shape, proportions and visible details; "
                  "give real-world dimensions in meters consistent with what they show."]
    lines += ["", "Return the plan as JSON matching the schema."]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- deterministic acceptance
def add_acceptance_item(items: list[AcceptanceItem], prefix: str, text: str, how: str, *,
                        priority: str = "must") -> None:
    """Append a framework-derived acceptance item (*must* unless told otherwise) unless an
    item with the same text exists; the id is ``<prefix><N>`` with N chosen to be unique."""
    if text.strip().lower() in {a.text.strip().lower() for a in items}:
        return
    ids = {a.id for a in items}
    n = 1
    while f"{prefix}{n}" in ids:
        n += 1
    items.append(AcceptanceItem(id=f"{prefix}{n}", text=text, how=how, priority=priority))  # type: ignore[arg-type]


def ensure_acceptance[P: BaseModel](plan_obj: P, spec: Spec) -> P:
    """Append acceptance items derived from the spec constraints when missing.

    A *must* item that the judge cannot verify caps the score (``missing_must_acceptance``)
    AND fails the run outright, so only the items the harness can hold the builder to may
    carry that priority.  On the SCENE track the planner writes its own checklist before
    the scene exists ("the parapet is 1.05 m high", "the gorge reads 200 m
    deep") and there is no measurement pass to settle them — a flash planner's private
    wishes were capping finished scenes at 0.60.  The spec's ``must_have`` list is the
    contract with the user, so on that track the plan's own items stay on the checklist as
    ``should`` (the judge still answers them, the refine loop still reads them) and only the
    spec-derived items keep ``must``.  A shader has no size, triangle count or ground: the graphics
    track gets the spec's checklist and, when the plan animates, one probe item that the frames
    change (never 'ground contact')."""
    items: list[AcceptanceItem] = list(plan_obj.acceptance)
    if spec.track is Track.SCENE:
        items = [a if a.priority == "should" else a.model_copy(update={"priority": "should"}) for a in items]
    c = spec.constraints
    solid = spec.track is not Track.GRAPHICS
    if solid and c.dimensions_m:
        dims = ", ".join(f"{k} = {v:.3f} m" for k, v in c.dimensions_m.items())
        add_acceptance_item(items, "dim", f"Overall dimensions match the request within 5%: {dims}", "measure")
    if solid and c.max_triangles:
        add_acceptance_item(items, "tri", f"Triangle count ≤ {c.max_triangles}", "measure")
    for m in c.must_have:
        add_acceptance_item(items, "must", f"Includes: {m}", "visual")
    for m in c.must_not:
        add_acceptance_item(items, "not", f"Does NOT include: {m}", "visual")
    if not solid:
        planned_motion = getattr(plan_obj, "motion", "").strip()
        if planned_motion and not any(w in a.text.lower() for a in items for w in ("static", "motion", "change over time")):
            add_acceptance_item(items, "motion", "Frames change over time as planned (not a static image)", "probe")
    elif spec.track is not Track.SCENE and not any(a.how == "measure" for a in items):
        add_acceptance_item(items, "ground", "Object stands on the ground plane (lowest point at up=0) with its footprint centred", "measure")
    items.extend(articulation_acceptance(plan_obj, items))
    plan_obj.acceptance = items
    return plan_obj


def articulation_acceptance(plan_obj: Any, items: list[AcceptanceItem]) -> list[AcceptanceItem]:
    """One ``articulation`` item per moving joint the planner's own checklist does not cover
    (2026-08-28, phase 4 item 6): the judge reads the pose sheet — rest, lower, upper — so the
    plan's rest-pose and motion statements become questions it answers per joint instead of
    prose it may skip.  ``should`` priority: an unverified item steers the refine loop's
    judge tasks but never fails the run (a *must* the judge cannot verify does)."""
    joints = [j for j in (getattr(plan_obj, "joints", None) or []) if j.type != "fixed"]
    if not joints:
        return []
    covered = " ".join(a.text.lower() for a in items if a.how == "articulation")
    words = set(re.findall(r"[a-z0-9]+", covered.replace("_", " ")))
    out: list[AcceptanceItem] = []
    for j in joints:
        # whole-word match on the joint's or the child's name ("lid" must not match "slides")
        keys = {j.name.lower(), to_snake(j.child).replace("_", " "), j.child.lower()}
        if any(k and all(w in words for w in re.findall(r"[a-z0-9]+", k)) for k in keys):
            continue
        unit = "m" if j.type == "prismatic" else "rad"
        rest = "closed / stowed" if abs(j.rest) < 1e-9 else f"at {j.rest:.2f} {unit}"
        rng = "turns freely" if j.type == "continuous" else f"moves over [{j.lower:.2f}, {j.upper:.2f}] {unit}"
        out.append(AcceptanceItem(
            id=f"art_{to_snake(j.child)}"[:40], how="articulation", priority="should",
            text=f"Joint {j.name}: at rest {j.child} sits {rest} against {j.parent}; it {rng} — {j.motion} — "
                 f"without passing through {j.parent} or any other link (pose_* views)"))
    return out


def normalise_names[P: BaseModel](plan_obj: P) -> P:
    """PascalCase part/link/zone/asset names in place — prompts ask for it; code guarantees it.
    Normalisation keeps the snake key, so validated uniqueness/tree properties are preserved."""
    for attr in ("parts", "zones", "assets"):
        for item in getattr(plan_obj, attr, None) or []:
            item.name = to_pascal(item.name)
            if getattr(item, "attach_to", None):
                item.attach_to = to_pascal(item.attach_to)
    for z in getattr(plan_obj, "zones", None) or []:
        z.contents = [to_pascal(c) for c in z.contents]
    if hasattr(plan_obj, "root_link"):
        plan_obj.root_link = to_pascal(plan_obj.root_link)
        for j in plan_obj.joints:
            j.parent, j.child = to_pascal(j.parent), to_pascal(j.child)
    return plan_obj


__all__ = ["MAX_PLAN_RESTARTS", "MAX_QUALITY_REASKS", "MAX_VALIDATION_REASKS",
           "PLAN_TEMPLATES", "PLAN_TOKENS_MAX", "PlanningError", "add_acceptance_item",
           "articulation_acceptance", "build_system_prompt", "build_user_prompt", "plan_event_stats",
           "ensure_acceptance", "missing_link_names", "normalise_names", "plan",
           "degenerate_plan", "plan_example", "plan_schema", "plan_template", "plan_temperature", "plan_tokens",
           "restart_enabled", "restart_note"]
