"""Planner: spec → validated Plan (StaticPlan / ArticulatedPlan / ScenePlan).

One structured ``ChatRequest`` (system prompt from ``prompts/tracks/plan_<track>.j2``,
``response_schema`` = the plan model's JSON schema, reference images inline),
validated IN CODE by the pydantic plan models; on failure the model is re-asked
once with the exact validation errors, then ``PlanningError``.  The harness
then appends deterministic acceptance items derived from the spec constraints
(generated artifacts are weak links — framework-owned requirements never rely
on the model remembering them).
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from codeverse.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse.contracts.common import Language, Track, Usage
from codeverse.contracts.plan import AcceptanceItem, ArticulatedPlan, ScenePlan, StaticPlan
from codeverse.contracts.spec import Spec
from codeverse.conventions import LANGUAGE_FRAME, frame_doc, to_pascal
from codeverse.prompts import prompt_hash, render
from codeverse.tracks.common import language_contract, load_prompt_or
from codeverse.tracks.prompting import constraints_text
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

P = TypeVar("P", bound=BaseModel)

PLAN_TEMPLATES: dict[Track, str] = {
    Track.STATIC_OBJECT: "tracks/plan_static.j2",
    Track.ARTICULATED_OBJECT: "tracks/plan_articulated.j2",
    Track.SCENE: "tracks/plan_scene.j2",
}


class PlanningError(RuntimeError):
    """The planner could not produce a valid plan after one re-ask."""


def plan(spec: Spec, model_id: str, plan_model: type[P], ws: Workspace, *, model: Any | None = None,
         events: Any | None = None, budget: Any | None = None, runtime: Any | None = None) -> P:
    """Plan and write ``ws.plan_path``.  ``model`` may be injected (tests)."""
    result, usage = plan_with_usage(spec, model_id, plan_model, ws, model=model, events=events, runtime=runtime)
    if budget is not None:
        budget.charge(usage)
    return result


def plan_with_usage(spec: Spec, model_id: str, plan_model: type[P], ws: Workspace, *, model: Any | None = None,
                    events: Any | None = None, runtime: Any | None = None) -> tuple[P, Usage]:
    if model is None:
        from codeverse.models import get_chat_model

        model = get_chat_model(model_id)
    system = build_system_prompt(spec, plan_model, runtime=runtime)
    user = build_user_prompt(spec)
    images = [ImagePart(path=r.path, label=f"{r.role}: {r.note}".strip(": ")) for r in spec.references]
    messages = [ChatMessage.user(user, images=images or None)]
    schema = plan_model.model_json_schema()
    usage = Usage()
    last_error = ""
    for attempt in range(2):
        req = ChatRequest(messages=messages, system=system, response_schema=schema, temperature=0.4,
                          thinking="medium", max_output_tokens=24000, label=f"planner{'-retry' if attempt else ''}")
        resp = model.generate(req)
        usage = usage + resp.usage
        raw = resp.parsed if resp.parsed is not None else _parse_json(resp.text)
        try:
            if not isinstance(raw, dict):
                raise ValueError(f"planner returned {type(raw).__name__}, expected a JSON object")
            result = plan_model.model_validate(raw)
        except (ValidationError, ValueError) as e:
            last_error = str(e)[:4000]
            if events is not None:
                events.emit("plan.invalid", attempt=attempt, error=last_error[:500])
            messages = messages + [
                ChatMessage.assistant(json.dumps(raw)[:20000] if raw is not None else (resp.text or "")[:20000]),
                ChatMessage.user("Your plan failed validation. Fix EXACTLY these problems and return the full corrected "
                                 f"plan JSON again (same schema):\n{last_error}"),
            ]
            continue
        result = ensure_acceptance(normalise_names(result), spec)
        ws.write_json(ws.plan_path, result)
        if events is not None:
            events.emit("plan.done", model=model_id, attempt=attempt, n_parts=len(getattr(result, "parts", []) or []),
                        n_zones=len(getattr(result, "zones", []) or []), n_acceptance=len(result.acceptance),
                        cost_usd=round(usage.cost_usd, 4), prompt_hash=prompt_hash(system))
        return result, usage
    raise PlanningError(f"plan did not validate after re-ask: {last_error}")


# ----------------------------------------------------------------------------- prompts
def build_system_prompt(spec: Spec, plan_model: type[BaseModel], *, runtime: Any | None = None) -> str:
    template = PLAN_TEMPLATES[spec.track]
    lang: Language = spec.language
    return render(
        template,
        track=spec.track.value,
        language=lang.value,
        frame_doc=frame_doc(LANGUAGE_FRAME[lang.value]),
        contract=language_contract(lang, runtime)[:6000],
        example_json=json.dumps(plan_example(spec.track), indent=1),
        schema_fields=", ".join(plan_model.model_json_schema().get("properties", {}).keys()),
    )


def build_user_prompt(spec: Spec) -> str:
    lines = [f"REQUEST: {spec.prompt}", "", "CONSTRAINTS:", constraints_text(spec)]
    if spec.references:
        lines += ["", f"REFERENCE IMAGES: {len(spec.references)} attached — match their shape, proportions and visible details; "
                  "give real-world dimensions in meters consistent with what they show."]
    lines += ["", "Return the plan as JSON matching the schema."]
    return "\n".join(lines)


def _parse_json(text: str) -> Any:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(t[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None


# ----------------------------------------------------------------------------- deterministic acceptance
def ensure_acceptance(plan_obj: P, spec: Spec) -> P:
    """Append acceptance items derived from the spec constraints when missing."""
    items: list[AcceptanceItem] = list(plan_obj.acceptance)
    have = {a.text.strip().lower() for a in items}
    ids = {a.id for a in items}

    def _add(prefix: str, text: str, how: str) -> None:
        if text.strip().lower() in have:
            return
        n = 1
        while f"{prefix}{n}" in ids:
            n += 1
        ids.add(f"{prefix}{n}")
        items.append(AcceptanceItem(id=f"{prefix}{n}", text=text, how=how, priority="must"))  # type: ignore[arg-type]

    c = spec.constraints
    if c.dimensions_m:
        dims = ", ".join(f"{k} = {v:.3f} m" for k, v in c.dimensions_m.items())
        _add("dim", f"Overall dimensions match the request within 5%: {dims}", "measure")
    if c.max_triangles:
        _add("tri", f"Triangle count ≤ {c.max_triangles}", "measure")
    for m in c.must_have:
        _add("must", f"Includes: {m}", "visual")
    for m in c.must_not:
        _add("not", f"Does NOT include: {m}", "visual")
    if spec.track is not Track.SCENE and not any(a.how == "measure" for a in items):
        _add("ground", "Object stands on the ground plane (lowest point at up=0) with its footprint centred", "measure")
    plan_obj.acceptance = items
    return plan_obj


# ----------------------------------------------------------------------------- worked examples
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
         "bbox": {"center": [0, 0, 0.43], "extents": [0.42, 0.40, 0.04]}, "material": "oiled oak", "attach_to": None, "symmetry": "mirror_x", "instances": 1},
        {"name": "FrontLeg", "role": "front leg", "description": "tapered round leg 35→25 mm, splayed 6° outward",
         "bbox": {"center": [0.17, -0.16, 0.205], "extents": [0.035, 0.035, 0.41]}, "material": "oiled oak", "attach_to": "Seat", "symmetry": "mirror_x", "instances": 2},
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
            {"name": "Cabinet", "role": "fixed carcass", "description": "box with open front", "bbox": {"center": [0, 0, 0.3], "extents": [0.4, 0.5, 0.6]},
             "material": "painted MDF", "attach_to": None, "symmetry": "none", "instances": 1},
            {"name": "Drawer", "role": "sliding drawer", "description": "open-top box with front panel and handle", "bbox": {"center": [0, -0.02, 0.45], "extents": [0.36, 0.46, 0.2]},
             "material": "painted MDF", "attach_to": "Cabinet", "symmetry": "none", "instances": 1},
        ]
        base["root_link"] = "Cabinet"
        base["joints"] = [{"name": "DrawerSlide", "type": "prismatic", "parent": "Cabinet", "child": "Drawer", "axis": [0, -1, 0],
                           "pivot": [0, -0.02, 0.45], "lower": 0.0, "upper": 0.35, "rest": 0.0, "motion": "drawer pulls out towards -Y"}]
        base["acceptance"].append({"id": "j1", "text": "Drawer slides out 0.35 m along -Y without penetrating the cabinet", "how": "articulation", "priority": "must"})
    return base


def plan_model_for(track: Track) -> type[StaticPlan] | type[ArticulatedPlan] | type[ScenePlan]:
    return {Track.STATIC_OBJECT: StaticPlan, Track.ARTICULATED_OBJECT: ArticulatedPlan, Track.SCENE: ScenePlan}[track]


def normalise_names(plan_obj: P) -> P:
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


__all__ = ["PlanningError", "plan", "plan_with_usage", "plan_model_for", "plan_example", "ensure_acceptance",
           "normalise_names", "build_system_prompt", "build_user_prompt", "load_prompt_or"]
