"""Planner: spec → validated Plan (StaticPlan / ArticulatedPlan / ScenePlan).

An optional cheap **brief expansion** (``expand_brief``, below) turns the one-line
request into an engineering brief, which — together with a **plan budget derived
from the request** — goes into one structured ``ChatRequest`` (system prompt from
``prompts/tracks/plan_<track>.j2``, ``response_schema`` = the plan model's JSON
schema, reference images inline).  The answer is validated IN CODE by the pydantic
plan models; a plan the schema rejects is re-asked once with the exact validation
errors (then ``PlanningError``), and a plan that validates but is
boxes-at-different-sizes against its budget is re-asked once with that specific
complaint (then shipped anyway — a mediocre plan beats no plan).  The harness
finally appends deterministic acceptance items derived from the spec constraints
and folds the brief + sub-parts into the plan's own text (generated artifacts are
weak links — framework-owned requirements never rely on the model remembering them).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from codeverse3d.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse3d.contracts.common import Language, Track, Usage
from codeverse3d.contracts.plan import AcceptanceItem, EngineeringBrief
from codeverse3d.contracts.spec import Spec
from codeverse3d.conventions import LANGUAGE_FRAME, frame_doc, to_pascal, to_snake
from codeverse3d.models.schema_utils import parse_json_lenient
from codeverse3d.proc import sha256_file
from codeverse3d.prompts import load_text, prompt_hash, render
from codeverse3d.tracks.common import language_contract
from codeverse3d.tracks.prompting import constraints_text
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)


# ===================================================================== the brief
#: env switch: ``off``/``0``/``false`` disables brief expansion for the run
BRIEF_ENV = "C3D_PLAN_BRIEF"
#: tracks the object-shaped brief applies to (graphics/scene get budgets only)
BRIEF_TRACKS = (Track.STATIC_OBJECT, Track.ARTICULATED_OBJECT)
BRIEF_TEMPLATE = "tracks/brief_object.j2"
BRIEF_MAX_TOKENS = 65_536


# ----------------------------------------------------------------------------- brief
def brief_enabled(spec: Spec, *, default: bool = True) -> bool:
    """Whether to expand the brief for this spec (env switch wins; object tracks only)."""
    from codeverse3d.config import env_flag  # ONE flag vocabulary (review-3 S4)

    return env_flag(BRIEF_ENV, default) and spec.track in BRIEF_TRACKS


def brief_cache_dir() -> Path:
    """``Settings.cache_dir``/briefs: ``C3D_CACHE_DIR`` AND the config file's ``cache_dir:``."""
    from codeverse3d.config import get_settings

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
        from codeverse3d.models import get_chat_model

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
            max_output_tokens=BRIEF_MAX_TOKENS, max_wait_s=900.0, label="planner-brief"))
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


#: the graphics track counts PASSES, not parts: a fragment shader is a stack of layers and
#: more than ~6 stops being a plan and starts being a wish-list.  Recorded mean was 3.1 on
#: graphics_v2 (0.530 / 12% pass) and pass count is the ONE complexity metric that
#: correlates positively with the judge there (visual_richness +0.40).
GRAPHICS_CAP = 6
GRAPHICS_MIN = 3


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
    want = MIN_PARTS
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
    target = max(MIN_PARTS, min(cap, int(want)))
    min_assemblies = 0
    if brief is not None:
        min_assemblies = min(3, sum(1 for s in brief.sub_assemblies if len(s.parts) >= 3))
    elif target >= 10:
        min_assemblies = 1
    return PlanBudget(
        target_parts=target,
        min_parts=max(MIN_PARTS, int(round(0.85 * target))),
        cap_parts=cap,
        target_leaves=max(target + 4, int(round(1.8 * target)), n_visible + 2 * min_assemblies),
        min_detail_parts=max(2, int(round(0.35 * target))),
        min_assemblies=min_assemblies,
        reason="; ".join(reasons) or "no checklist — default floor",
        evidence=n_must >= EVIDENCE_MIN_CHECKLIST or brief is not None,
    )


def _graphics_budget(spec: Spec) -> PlanBudget:
    """Pass budget for the graphics track (see :data:`GRAPHICS_CAP`)."""
    n_must = len(spec.constraints.must_have)
    target = max(GRAPHICS_MIN, min(GRAPHICS_CAP, GRAPHICS_MIN + (n_must + 1) // 3))
    return PlanBudget(
        target_parts=target, min_parts=max(GRAPHICS_MIN, target - 1), cap_parts=GRAPHICS_CAP,
        target_leaves=4 * target, min_detail_parts=max(2, target - 1), min_assemblies=max(2, target - 2),
        reason=f"{n_must} checklist items" if n_must else "no checklist — default floor",
        evidence=n_must >= EVIDENCE_MIN_CHECKLIST)


def budget_block(budget: PlanBudget, *, unit: str = "parts") -> str:
    """The budget as a prompt block for the planner's user message."""
    return "\n".join([
        f"PLAN BUDGET (derived from this request: {budget.reason}):",
        f"- aim for {budget.target_parts} top-level {unit} (hard floor {budget.min_parts}, hard ceiling "
        f"{budget.cap_parts}); a plan under the floor will be rejected and re-asked.",
        f"- at least {budget.min_detail_parts} of them must carry detail a plain primitive cannot express "
        + ("(named elements with their own counts, sizes and speeds)." if unit == "passes" else
           "(a taper, a bevel radius, a groove, a counted repeat: '7 slats', '24 spokes')."),
        (f"- at least {budget.min_assemblies} of them must use "
         + ("`elements` (3-6 named things the pass draws)." if unit == "passes"
            else "`children` (3-6 sub-parts) because they are ASSEMBLIES, not shapes.")
         if budget.min_assemblies else
         "- use `children` for any part a fitter would call an assembly rather than a shape."),
        f"- total named shapes across {unit} and their sub-parts should reach about {budget.target_leaves}.",
    ])


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
            f"Only {len(parts)} {unit} for a request that needs about {budget.target_parts} "
            f"({budget.reason}). Split the ones that are really assemblies and add the features you skipped — "
            f"do NOT pad with duplicates.")
    detector = has_pass_detail if unit == "passes" else has_real_detail
    detailed = [p for p in parts if detector(f"{p.description} {getattr(p, 'detail_hint', '')}")
                or getattr(p, "children", None) or getattr(p, "elements", None)]
    if len(detailed) < budget.min_detail_parts:
        plain = [p.name for p in parts if p not in detailed][:8]
        hard.append(
            f"This plan is boxes at different sizes: only {len(detailed)} of {len(parts)} {unit} describe anything a "
            f"plain primitive cannot express, and at least {budget.min_detail_parts} must. Rewrite "
            f"{', '.join(plain)} with real construction: profile (tapered / fluted / bevelled r=…), counted repeats "
            f"('7 slats at 12 mm pitch'), edge treatment, and the sub-shapes each one is actually made of.")
    if budget.min_assemblies:
        n_nested = sum(1 for p in parts if len(getattr(p, nest, None) or []) >= 2)
        if n_nested < budget.min_assemblies:
            soft.append(
                f"{n_nested} of the {unit} use `{nest}`, but this request has at least {budget.min_assemblies} real "
                f"sub-assemblies. Give each of them 3-6 `{nest}`" +
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
    known = {a.text.strip().lower() for a in items}
    ids = {a.id for a in items}
    n = 1
    for feature in brief.signature_features:
        text = f"Signature feature visible: {feature.strip()}"
        if text.lower() in known:
            continue
        while f"sig{n}" in ids:
            n += 1
        items.append(AcceptanceItem(id=f"sig{n}", text=text, how="visual", priority="should"))
        ids.add(f"sig{n}")
        known.add(text.lower())
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
def plan_example(track: Track) -> dict[str, Any]:
    """Compact, valid worked example per track (concrete beats abstract)."""
    if track is Track.SCENE:
        return {
            "title": "Harbour at dusk", "summary": "Small fishing harbour: quay, two boats, lighthouse, calm water.",
            "setting": "Breton coast, 1960s, clear dusk", "mood": "calm, warm low sun",
            "bounds": {"center": [0, 5, 0], "extents": [120, 30, 120]},
            "environment": "Sky gradient orange→indigo, sun at azimuth 250° elevation 8°, light fog density 0.004, flat sea plane y=0, stone quay at y=1.2.",
            "zones": [
                {"name": "Quay", "description": "L-shaped granite quay with bollards and crates", "bbox": {"center": [-20, 1.2, 0], "extents": [40, 3, 60]}, "contents": ["Bollard", "Crate"]},
                {"name": "Water", "description": "calm harbour basin with two moored boats", "bbox": {"center": [20, 0, 0], "extents": [60, 1, 80]}, "contents": ["FishingBoat"]},
            ],
            "assets": [
                {"name": "FishingBoat", "kind": "threejs", "description": "8 m wooden boat, wheelhouse aft, red hull", "approx_size_m": [8, 3.5, 3], "instances_hint": 2},
                {"name": "Bollard", "kind": "threejs", "description": "cast-iron mooring bollard", "approx_size_m": [0.3, 0.5, 0.3], "instances_hint": 6},
                {"name": "Crate", "kind": "blender_glb", "description": "wooden fish crate, slatted", "approx_size_m": [0.6, 0.3, 0.4], "instances_hint": 8},
            ],
            "effects": [{"name": "WaterRipple", "kind": "glsl_material", "description": "animated normal ripples on the sea plane", "target": "Water"}],
            "animation": ["boats bob ±0.05 m at 0.3 Hz", "water ripples scroll"],
            "cameras": [{"name": "Overview", "position": [45, 18, 55], "look_at": [0, 1, 0], "fov": 50, "purpose": "establishing shot"},
                        {"name": "QuayEye", "position": [-5, 2.8, 25], "look_at": [15, 1, -10], "fov": 60, "purpose": "eye level along the quay"}],
            "acceptance": [{"id": "a1", "text": "Two boats float on the water inside the basin", "how": "visual", "priority": "must"},
                           {"id": "a2", "text": "Quay top at y≈1.2 m, boats' waterline at y≈0", "how": "probe", "priority": "must"}],
        }
    parts = [
        {"name": "Seat", "role": "horizontal seat board", "description": "solid oak board, 40 mm thick, rounded front edge (r=10 mm)",
         "bbox": {"center": [0, 0, 0.43], "extents": [0.42, 0.40, 0.04]}, "material": "oiled oak", "attach_to": None, "symmetry": "mirror_x", "instances": 1,
         "children": [], "detail_hint": "the eye lands here first — keep the 10 mm bullnose and the 3 mm chamfer under the front edge"},
        {"name": "FrontLeg", "role": "front leg", "description": "tapered round leg 35→25 mm, splayed 6° outward",
         "bbox": {"center": [0.17, -0.16, 0.205], "extents": [0.035, 0.035, 0.41]}, "material": "oiled oak", "attach_to": "Seat", "symmetry": "mirror_x", "instances": 2,
         "children": [], "detail_hint": ""},
        {"name": "Backrest", "role": "slatted back assembly", "description": "three vertical slats in a curved top rail and a lower rail",
         "bbox": {"center": [0, 0.19, 0.66], "extents": [0.40, 0.03, 0.42]}, "material": "oiled oak", "attach_to": "Seat", "symmetry": "none", "instances": 1,
         "detail_hint": "an assembly, not a panel: the gaps between the slats must be visible from the front and the side",
         "children": [
             {"name": "TopRail", "role": "curved crest rail", "description": "60 mm deep rail bowed 18 mm back on a 0.9 m radius, ends rounded r=8 mm",
              "bbox": {"center": [0, 0.19, 0.85], "extents": [0.40, 0.03, 0.06]}, "material": "oiled oak", "instances": 1},
             {"name": "Slat", "role": "vertical back slat", "description": "12 mm thick slat, 55 mm wide, 4 mm chamfer both faces, 30 mm gap to its neighbour",
              "bbox": {"center": [0, 0.19, 0.66], "extents": [0.055, 0.012, 0.34]}, "material": "oiled oak", "instances": 3},
             {"name": "LowerRail", "role": "rail the slats seat into", "description": "40 mm rail with three 12 × 55 mm mortises on 85 mm centres",
              "bbox": {"center": [0, 0.19, 0.48], "extents": [0.40, 0.03, 0.04]}, "material": "oiled oak", "instances": 1},
         ]},
    ]
    base: dict[str, Any] = {
        "object_name": "DiningChair", "summary": "Mid-century oak dining chair, 0.45 × 0.50 × 0.82 m.",
        "overall_bbox": {"center": [0, 0, 0.41], "extents": [0.45, 0.50, 0.82]},
        "style_notes": "Danish mid-century: tapered legs, thin spindles, warm oak, no ornament.",
        "parts": parts,
        "acceptance": [{"id": "a1", "text": "Seat top at 0.45 m ± 0.02 m above the ground", "how": "measure", "priority": "must"},
                       {"id": "a2", "text": "Four legs, all touching the ground, splayed outward", "how": "visual", "priority": "must"}],
    }
    if track is Track.ARTICULATED_OBJECT:
        base["object_name"] = "DeskDrawerUnit"
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
        base["acceptance"].append({"id": "j1", "text": "Drawer slides out 0.35 m along -Y without penetrating the cabinet", "how": "articulation", "priority": "must"})
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
#: kill-switch for that restart (default ON, mirrors C3D_AXIS_REPAIR)
PLAN_RESTART_ENV = "C3D_PLAN_RESTART"
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
        f"(about {budget.target_parts} parts for this request).\n"
        "- `joints[].parent` and `joints[].child` must be spelled exactly like those part names.\n"
        "- Nothing that a joint moves may live in `children`; sub-parts are rigid detail only."
    )


def restart_enabled() -> bool:
    """Kill switch for the degenerate-plan restart, ON by default (docs/COST.md §30)."""
    from codeverse3d.config import env_flag  # ONE flag vocabulary (review-3 S4)

    return env_flag(PLAN_RESTART_ENV, True)


#: an invalid plan attempt is written here, so a run that DIES at the plan stage still says
#: what the model wrote.  Pydantic truncates the offending value in its message, so the
#: event log alone cannot answer "what did it actually write?" — the question every
#: plan-stage failure class starts from (docs/PAPER_WRITING.md §5.1).
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
    return (f"\nAlso: this plan lists only {len(parts)} part(s) but the request needs about {budget.target_parts} "
            f"({budget.reason}). Put EVERY link a joint references under `parts` with its own bbox.")


def _truncated(exc: Exception) -> bool:
    """Did this model error mean 'the answer did not fit'?"""
    return "MAX_TOKENS" in str(exc)

PLAN_TEMPLATES: dict[Track, str] = {
    Track.STATIC_OBJECT: "tracks/plan_static.j2",
    Track.ARTICULATED_OBJECT: "tracks/plan_articulated.j2",
    Track.SCENE: "tracks/plan_scene.j2",
}


class PlanningError(RuntimeError):
    """The planner could not produce a valid plan after MAX_VALIDATION_REASKS re-asks.

    Carries the ``usage`` already spent so callers can charge the budget even
    when planning fails (a failed re-ask is still paid for)."""

    def __init__(self, message: str, usage: Usage | None = None):
        super().__init__(message)
        self.usage = usage or Usage()


#: hook types (BaseTrack subclasses parameterise the ONE planner loop with these)
FinalisePlan = Callable[[Any], Any]
EventStats = Callable[[Any], dict[str, Any]]


def default_event_stats(plan_obj: Any) -> dict[str, Any]:
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
         events: Any | None = None, budget: Any | None = None, runtime: Any | None = None,
         template: str | None = None, example: dict[str, Any] | None = None, temperature: float = 0.4,
         max_output_tokens: int = 24000, finalise: FinalisePlan | None = None,
         event_stats: EventStats | None = None) -> P:
    """Plan and write ``ws.plan_path``: optional brief expansion → one structured planner
    call → re-asks (schema, quality, a degenerate-plan restart).  ``model`` may be injected (tests).

    Parameterised by the track hooks: ``template``/``example`` (system prompt),
    ``finalise`` (post-validation fixup, default = ``ensure_acceptance(normalise_names(...))``)
    and ``event_stats`` (extra ``plan.done`` payload).

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
    request's PlanBudget) is booked with a non-enforcing ``add`` the moment each call is
    PAID — brief, first call, every re-ask — so success, ``PlanningError`` and a crash on a
    LATER attempt all leave the earlier dollars in the guard.  ``PlanningError`` still
    carries the total usage for guard-less callers; when a guard is given those dollars are
    already booked and the caller must NOT charge them again.  The ceilings are enforced
    once, after the plan is written."""
    guard = budget
    if model is None:
        from codeverse3d.models import get_chat_model

        model = get_chat_model(model_id)
    brief, usage = (None, Usage())
    if brief_enabled(spec):
        brief, usage = expand_brief(spec, model_id, model=model, events=events)
        if guard is not None and (usage.cost_usd or usage.input_tokens or usage.output_tokens):
            guard.add(usage, stage="plan")
    budget = plan_budget(spec, brief)
    unit = "passes" if spec.track is Track.GRAPHICS else "parts"
    system = build_system_prompt(spec, plan_model, runtime=runtime, template=template, example=example, budget=budget)
    user = build_user_prompt(spec, brief=brief, budget=budget, unit=unit)
    images = [ImagePart(path=r.path, label=f"{r.role}: {r.note}".strip(": ")) for r in spec.references]
    messages = [ChatMessage.user(user, images=images or None)]
    base_messages = list(messages)
    schema = plan_model.model_json_schema()
    last_error = ""
    invalid = requeried = grown = restarts = 0
    tokens = plan_tokens(budget, max_output_tokens)
    thinking = "medium"
    wait_scale = 1.0
    # 4 = the first call + truncation retries; it was 2 + the geometry re-ask's 2 (D49, code
    # removed 2026-09-21) — the total is kept so a default run has the attempts it always had
    for attempt in range(4 + MAX_VALIDATION_REASKS + MAX_QUALITY_REASKS + MAX_PLAN_RESTARTS):
        req = ChatRequest(messages=messages, system=system, response_schema=schema, temperature=temperature,
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
            if events is not None:
                events.emit("plan.truncated", attempt=attempt, max_output_tokens=tokens, thinking=thinking)
            continue
        usage = usage + resp.usage
        if guard is not None:
            # booked where it is paid: a later attempt that raises cannot erase this dollar
            guard.add(resp.usage, stage="plan")
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
            if events is not None:
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
                if events is not None:
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
            if events is not None:
                events.emit("plan.thin", attempt=attempt, n_parts=len(getattr(result, "parts", None) or getattr(result, "passes", None) or []),
                            target_parts=budget.target_parts, complaint=complaint[:400])
            messages = messages + [_echo(raw, resp.text), ChatMessage.user(complaint)]
            continue
        normalised = list(getattr(result, "normalisations", None) or [])
        if normalised and events is not None:
            events.emit("plan.normalised", attempt=attempt, n=len(normalised), items=normalised[:8])
        result = finalise(result) if finalise is not None else ensure_acceptance(normalise_names(result), spec)
        result = enrich_plan(result, brief)
        ws.write_json(ws.plan_path, result)
        if events is not None:
            stats = (event_stats or default_event_stats)(result)
            events.emit("plan.done", model=model_id, **stats,
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
def build_system_prompt(spec: Spec, plan_model: type[BaseModel], *, runtime: Any | None = None,
                        template: str | None = None, example: dict[str, Any] | None = None,
                        budget: PlanBudget | None = None) -> str:
    template = template or PLAN_TEMPLATES[spec.track]
    lang: Language = spec.language
    budget = budget or plan_budget(spec)
    return render(
        template,
        track=spec.track.value,
        language=lang.value,
        frame_doc=frame_doc(LANGUAGE_FRAME[lang.value]),
        contract=language_contract(lang, runtime),
        example_json=json.dumps(example if example is not None else plan_example(spec.track), indent=1),
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
def add_acceptance_item(items: list[AcceptanceItem], prefix: str, text: str, how: str) -> None:
    """Append a framework-derived *must* acceptance item unless an item with the
    same text exists; the id is ``<prefix><N>`` with N chosen to be unique."""
    if text.strip().lower() in {a.text.strip().lower() for a in items}:
        return
    ids = {a.id for a in items}
    n = 1
    while f"{prefix}{n}" in ids:
        n += 1
    items.append(AcceptanceItem(id=f"{prefix}{n}", text=text, how=how, priority="must"))  # type: ignore[arg-type]


def ensure_acceptance[P: BaseModel](plan_obj: P, spec: Spec) -> P:
    """Append acceptance items derived from the spec constraints when missing.

    A *must* item that the judge cannot verify caps the score (``missing_must_acceptance``)
    AND fails the run outright, so only the items the harness can hold the builder to may
    carry that priority.  On the SCENE track the planner writes its own checklist before
    the scene exists ("the parapet is 1.05 m high", "the establishing camera frames 70 % of
    the gorge") and there is no measurement pass to settle them — a flash planner's private
    wishes were capping finished scenes at 0.60.  The spec's ``must_have`` list is the
    contract with the user, so on that track the plan's own items stay on the checklist as
    ``should`` (the judge still answers them, the refine loop still reads them) and only the
    spec-derived items keep ``must``."""
    items: list[AcceptanceItem] = list(plan_obj.acceptance)
    if spec.track is Track.SCENE:
        items = [a if a.priority == "should" else a.model_copy(update={"priority": "should"}) for a in items]
    c = spec.constraints
    if c.dimensions_m:
        dims = ", ".join(f"{k} = {v:.3f} m" for k, v in c.dimensions_m.items())
        add_acceptance_item(items, "dim", f"Overall dimensions match the request within 5%: {dims}", "measure")
    if c.max_triangles:
        add_acceptance_item(items, "tri", f"Triangle count ≤ {c.max_triangles}", "measure")
    for m in c.must_have:
        add_acceptance_item(items, "must", f"Includes: {m}", "visual")
    for m in c.must_not:
        add_acceptance_item(items, "not", f"Does NOT include: {m}", "visual")
    if spec.track is not Track.SCENE and not any(a.how == "measure" for a in items):
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
           "PLAN_RESTART_ENV", "PLAN_TOKENS_MAX", "PlanningError", "add_acceptance_item",
           "articulation_acceptance", "build_system_prompt", "build_user_prompt", "default_event_stats",
           "ensure_acceptance", "missing_link_names", "normalise_names", "plan",
           "degenerate_plan", "plan_example", "plan_tokens", "restart_enabled",
           "restart_note"]
