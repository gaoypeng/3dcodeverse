"""``3dcode validate-plan``: ``plan.json`` against the track's plan model plus the deterministic plan checks
(placeholder echo, the size / detail budget derived from the request, fixed-part clash), then the normalisation
every accepted plan gets (PascalCase names, the acceptance items the spec implies)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import SCENE_ASSET_KINDS, AcceptanceItem
from codeverse3d.contracts.spec import Spec
from codeverse3d.conventions import to_pascal, to_snake

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
#: a checklist this long is EVIDENCE that the request really needs the target part count; below it the
#: target is an aspiration the check must not enforce
EVIDENCE_MIN_CHECKLIST = 4
GRAPHICS_CAP = 6
GRAPHICS_MIN = 1

# --------------------------------------------------------------------- "is this a box?"
#: construction verbs / counted repeated features that make a part description more than
#: "a box of size X".  Stems, not bare words: `taper` alone never matches "tapered", which
#: is how every real plan writes it.
_CRAFT = re.compile(
    r"\b(bevel\w*|chamfer\w*|fillet\w*|round(?:ed)?[- ](?:edge|corner|over)\w*|bullnose\w*|taper\w*|groove\w*"
    r"|flute\w*|thread(?:ed|s)?|helix|helical|spline\w*|loft\w*|revolv\w*|extrud\w*|bezier|swept|sweep\w*"
    r"|profile[ds]?|inset\w*|boolean\w*|subdivi\w*|smooth[- ]shad\w*|array\w*|lattice\w*|slot(?:s|ted)?"
    r"|notch\w*|ribs?|louvre\w*|louver\w*|knurl\w*|serrat\w*|scallop\w*|hollow\w*|shell(?:ed)?|cut-?out\w*"
    r"|recess\w*|emboss\w*|moulding\w*|molding\w*|curv\w*|arc\b|arch\w*|spiral\w*|dome[ds]?|domed|ogee"
    r"|scroll\w*|pierc\w*|perforat\w*|crown\w*|swage\w*|bulg\w*|waist\w*|concave|convex|rebate[ds]?|dovetail\w*"
    r"|mortise\w*|tenon\w*|splay\w*|conical|tapering|beading|reeded|turned|rounded|radius|radii)\b"
    r"|\br\s*=\s*\d",
    re.I)
_COUNTED = re.compile(
    r"\b\d+\s*(x|×)?\s*(slats?|spokes?|teeth|dentils?|panes?|rafters?|bolts?|screws?|rivets?|holes?|segments?"
    r"|facets?|ribs?|flutes?|turns?|steps?|rings?|wires?|bars?|louvres?|louvers?|vents?|grooves?|studs?|pads?)\b",
    re.I)
_GFX_CRAFT = re.compile(
    r"\b(fbm|noise|hash|sdf|raymarch|ray[- ]?march|march|domain[- ]?warp|voronoi|worley|curl|advect|feedback"
    r"|parallax|refract|reflect|fresnel|bokeh|blur|bloom|vignette|aberration|tonemap|gamma|dither|grain"
    r"|particle|instanc|scatter|caustic|godray|god[- ]ray|volumetric|shadow|ao\b|ambient occlusion|palette"
    r"|gradient|dissolve|erosion|turbulen|flow[- ]?map|displac|tessell|subsurface|halftone|posteris|posteriz)\b",
    re.I)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def has_real_detail(text: str) -> bool:
    """True when a part description says something a box cannot say."""
    return bool(_CRAFT.search(text) or _COUNTED.search(text))


def has_pass_detail(text: str) -> bool:
    """True when a graphics pass description names a technique or carries a recipe."""
    return bool(_GFX_CRAFT.search(text)) or len(_NUMBER.findall(text)) >= 2


@dataclass(frozen=True)
class PlanBudget:
    """What this request is worth, in plan units (parts, or passes for graphics)."""

    target_parts: int
    min_parts: int
    cap_parts: int
    min_detail_parts: int
    min_assemblies: int
    reason: str = ""
    evidence: bool = False

    def as_dict(self) -> dict[str, int | str | bool]:
        return {"target_parts": self.target_parts, "min_parts": self.min_parts, "cap_parts": self.cap_parts,
                "min_detail_parts": self.min_detail_parts, "min_assemblies": self.min_assemblies,
                "budget_evidence": self.evidence, "reason": self.reason}


def plan_budget(spec: Spec) -> PlanBudget:
    """Derive the plan's size budget from the REQUEST, not from a constant."""
    n_must = len(spec.constraints.must_have)
    if spec.track is Track.GRAPHICS:
        target = max(GRAPHICS_MIN, min(GRAPHICS_CAP, GRAPHICS_MIN + (n_must + 1) // 3))
        return PlanBudget(target_parts=target, min_parts=max(GRAPHICS_MIN, target - 1), cap_parts=GRAPHICS_CAP,
                          min_detail_parts=max(1, target - 1), min_assemblies=0,
                          reason=f"{n_must} checklist items" if n_must else "no checklist",
                          evidence=n_must >= EVIDENCE_MIN_CHECKLIST)
    cap = PART_CAP.get(spec.language.value, DEFAULT_PART_CAP)
    want = n_must
    target = max(MIN_PARTS, min(cap, want))
    floor = max(2, int(round(0.85 * min(cap, want))))
    return PlanBudget(
        target_parts=target, min_parts=floor, cap_parts=cap,
        min_detail_parts=min(floor, max(2, int(round(0.35 * target)))),
        min_assemblies=1 if target >= 10 else 0,
        reason=f"{n_must} checklist items" if n_must else "no checklist",
        evidence=n_must >= EVIDENCE_MIN_CHECKLIST,
    )


def plan_quality_complaints(plan_obj: Any, budget: PlanBudget, *, unit: str = "parts") -> tuple[list[str], list[str]]:
    """``(hard, soft)`` complaints about a VALID plan: hard ones fail the check, soft ones are warnings."""
    parts: list[Any] = list(getattr(plan_obj, "parts", None) or getattr(plan_obj, "passes", None) or [])
    if not parts:
        return [], []
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
        # enforced only on EVIDENCE (a long checklist): without it the request never said the object has
        # shaping a primitive cannot express — "a stool" may be four boxes
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
    return hard, soft


# ===================================================================== deterministic checks on the raw plan
_PLACEHOLDERS = {"string", "str", "name", "text", "..."}


def schema_echo(raw: dict[str, Any]) -> str:
    """"" or what in ``raw`` is a JSON-schema placeholder (``"name": "string"``) rather than content."""
    bad: list[str] = []
    if str(raw.get("object_name", "")).strip().lower() in _PLACEHOLDERS:
        bad.append("object_name")
    for key in ("parts", "joints", "zones", "assets", "passes"):
        for i, item in enumerate(raw.get(key) or []):
            if isinstance(item, dict) and str(item.get("name", "")).strip().lower() in _PLACEHOLDERS:
                bad.append(f"{key}[{i}].name")
    return ", ".join(bad[:6])


def missing_link_names(raw: Any) -> list[str]:
    """Link names the joints reference that ``parts`` does not define (snake-compared)."""
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


def fixed_part_clash(plan_obj: BaseModel, spec: Spec) -> str:
    """'' unless the plan lists one of the spec's fixed parts (``Spec.fixed_parts``) as a part of its own: the
    project builds those, so a planned one invites the agent to author it."""
    fixed = {to_snake(f.name): f for f in spec.fixed_parts if f.name}
    if not fixed:
        return ""
    names = [p.name for p in getattr(plan_obj, "parts", None) or []]
    hits = [n for n in names if to_snake(n) in fixed]
    if not hits:
        return ""
    return (f"the plan lists fixed parts {hits} as its own parts: they are given by the project (FIXED PARTS) — "
            "remove them from the plan and plan only the parts around them")


def asset_kind_errors(raw: Any, language: Language) -> list[str]:
    """Scene assets whose ``kind`` is not one this scene language builds (``SCENE_ASSET_KINDS``)."""
    kinds = SCENE_ASSET_KINDS.get(language.value)
    if not kinds or not isinstance(raw, dict):
        return []
    out = []
    for i, a in enumerate(raw.get("assets") or []):
        if isinstance(a, dict) and a.get("kind") not in kinds:
            out.append(f"assets[{i}].kind: {a.get('kind')!r} is not one of {list(kinds)} for {language.value}")
    return out


def validation_errors(e: ValidationError) -> list[str]:
    """One ``path: message`` line per pydantic error."""
    out = []
    for err in e.errors():
        path = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        out.append(f"{path}: {err.get('msg', '')}")
    return out


# ===================================================================== normalisation
def _fold_pass(ps: Any) -> None:
    """Fold a graphics pass's ``elements`` + ``detail_hint`` into its ``description``."""
    if not (ps.elements or ps.detail_hint) or " · " in ps.description:
        return
    bits = [ps.description.rstrip().rstrip(".")]
    if ps.elements:
        bits.append("draws: " + " · ".join(e.strip() for e in ps.elements))
    if ps.detail_hint:
        bits.append(f"what makes it real: {ps.detail_hint.strip()}")
    ps.description = " · ".join(bits).replace("\n", " ").replace("|", "/")


def add_acceptance_item(items: list[AcceptanceItem], prefix: str, text: str, how: str, *,
                        priority: str = "must") -> bool:
    """Append a derived acceptance item (*must* unless told otherwise) unless an equal text exists."""
    if text.strip().lower() in {a.text.strip().lower() for a in items}:
        return False
    ids = {a.id for a in items}
    n = 1
    while f"{prefix}{n}" in ids:
        n += 1
    items.append(AcceptanceItem(id=f"{prefix}{n}", text=text, how=how, priority=priority))  # type: ignore[arg-type]
    return True


def ensure_acceptance(plan_obj: Any, spec: Spec) -> list[str]:
    """Append the acceptance items the spec constraints imply; returns the ids added."""
    items: list[AcceptanceItem] = list(plan_obj.acceptance)
    before = {a.id for a in items}
    if spec.track is Track.SCENE:
        items = [a if a.priority == "should" else a.model_copy(update={"priority": "should"}) for a in items]
    c = spec.constraints
    solid = spec.track is not Track.GRAPHICS
    if solid and c.dimensions_m:
        dims = ", ".join(f"{k} = {v:.3f} m" for k, v in c.dimensions_m.items())
        add_acceptance_item(items, "dim", f"Overall dimensions match the request within 5%: {dims}", "measure")
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
    return [a.id for a in items if a.id not in before]


def articulation_acceptance(plan_obj: Any, items: list[AcceptanceItem]) -> list[AcceptanceItem]:
    """One ``articulation`` item per moving joint the plan's own checklist does not cover."""
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


def normalise_names(plan_obj: Any) -> None:
    """PascalCase part/link/zone/asset names in place."""
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


def normalise_plan(plan_obj: Any, spec: Spec) -> list[str]:
    """Every normalisation an accepted plan gets, as human lines (``[]`` when nothing changed)."""
    notes: list[str] = list(getattr(plan_obj, "normalisations", None) or [])
    before = [p.name for p in getattr(plan_obj, "parts", None) or []]
    normalise_names(plan_obj)
    after = [p.name for p in getattr(plan_obj, "parts", None) or []]
    renamed = [f"{a} → {b}" for a, b in zip(before, after, strict=True) if a != b]
    if renamed:
        notes.append("part names made PascalCase: " + ", ".join(renamed[:8]))
    for ps in getattr(plan_obj, "passes", None) or []:
        _fold_pass(ps)
    added = ensure_acceptance(plan_obj, spec)
    if added:
        notes.append("acceptance items added from the spec: " + ", ".join(added))
    return notes
