"""Plan budgets, the re-plan quality gate, and brief → plan enrichment.

Two levers on the COMPLEXITY CEILING that live between the brief and the planner:

* **Plan budgets** (:func:`plan_budget`) — a target part count derived from the REQUEST
  (checklist size; sub-assemblies × signature features when a brief exists) and capped by
  the language's practical limit, instead of the flat "6 to 14 parts" the template used to
  ask for regardless of difficulty.  Measured motivation: on the hard static battery the
  plan came back with FEWER parts than the prompt had checklist items (0.91 parts per item,
  vs 1.71 on the easy battery) — the constant bound exactly where the request was hardest.
* **The quality gate** (:func:`plan_quality_complaint`) — one re-ask when the plan is
  boxes-at-different-sizes *relative to that budget*.  Absolute boxiness is not a defect
  (a cabinet door really is three boxes: boxy 1.00, scored 0.964), so every threshold here
  is relative to what the request asked for.

:func:`enrich_plan` then folds what only the brief knows back into the plan's own fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from codeverse.contracts.common import Language, Track
from codeverse.contracts.plan import AcceptanceItem, EngineeringBrief
from codeverse.contracts.spec import Spec

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

    ``graphics_steps.passes_table`` prints ``| name | kind | description |`` — one markdown
    row — so the fold must stay on a single line and must be idempotent."""
    if not (ps.elements or ps.detail_hint) or " · " in ps.description:
        return
    bits = [ps.description.rstrip().rstrip(".")]
    if ps.elements:
        bits.append("draws: " + " · ".join(e.strip() for e in ps.elements))
    if ps.detail_hint:
        bits.append(f"what makes it real: {ps.detail_hint.strip()}")
    ps.description = " · ".join(bits).replace("\n", " ").replace("|", "/")


def enrich_plan(plan_obj: Any, brief: EngineeringBrief | None, budget: PlanBudget | None = None) -> Any:
    """Fold what the BRIEF knows back into the plan's own fields, after validation.

    A part's own depth needs no folding: ``tracks/prompting.py`` renders
    ``PartPlan.children`` and ``PartPlan.detail_hint`` straight from the typed fields, so
    copying them into ``description`` would print every sub-part twice.  A graphics PASS is
    the exception — ``graphics_steps.passes_table`` renders only ``description`` — so
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


__all__ = ["DEFAULT_PART_CAP", "EVIDENCE_MIN_CHECKLIST", "GRAPHICS_CAP", "GRAPHICS_MIN", "MIN_PARTS",
           "PART_CAP", "PlanBudget", "budget_block", "enrich_plan", "has_pass_detail", "has_real_detail",
           "plan_budget", "plan_quality_complaint"]
