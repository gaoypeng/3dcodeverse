"""Planner: spec → validated Plan (StaticPlan / ArticulatedPlan / ScenePlan).

An optional cheap **brief expansion** (``tracks/brief.py``) turns the one-line
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

import json
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from codeverse.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse.contracts.common import Language, Track, Usage
from codeverse.contracts.plan import AcceptanceItem
from codeverse.contracts.spec import Spec
from codeverse.conventions import LANGUAGE_FRAME, frame_doc, to_pascal
from codeverse.prompts import prompt_hash, render
from codeverse.tracks.brief import brief_block, brief_enabled, expand_brief
from codeverse.tracks.common import language_contract, load_prompt_or
from codeverse.tracks.plan_budget import (
    PlanBudget,
    budget_block,
    enrich_plan,
    plan_budget,
    plan_quality_complaint,
)
from codeverse.tracks.plan_examples import plan_example
from codeverse.tracks.prompting import constraints_text
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

P = TypeVar("P", bound=BaseModel)

#: re-ask budgets — a plan the SCHEMA rejects and a plan the QUALITY gate rejects are
#: different failures and get separate chances (see ``plan_with_usage``).
MAX_VALIDATION_REASKS = 2  # was 1: compare_art_v2 lost 5 of 14 articulated prompts at this gate (2026-08-25)
MAX_QUALITY_REASKS = 1
#: Output room for the plan call, sized from the plan budget.  A deep plan is much longer
#: JSON than a flat one AND Gemini 3.x bills its thinking against the same ceiling, so the
#: flat 24 000 that served 8 box-parts truncates a 12-part plan with sub-parts —
#: ``finish_reason=MAX_TOKENS``, no JSON, and the whole RUN dies at the plan stage.  This
#: was measured, not guessed: the first live pass of this wave failed exactly there on both
#: prompts.  Callers may raise the floor; this only ever raises it further.
PLAN_TOKENS_PER_PART = 600
PLAN_TOKENS_PER_LEAF = 400
PLAN_TOKENS_MAX = 60000
#: one more attempt when the model truncates anyway, with half again as much room
TRUNCATION_GROWTH = 1.5
#: retry budget (``ChatRequest.max_wait_s``) for one planner call.  Audit 2026-08-26 §2: the
#: call itself is 13.7 s p50 / 32 s p90 / 76 s max (23.4 / 43 / 68 under the storm), yet the
#: storm-day plan stage waited 492 s median per run for 39 s of model time; 300 s is ~4x the
#: worst observed call and replaces the model's 900 s default.  The re-asks are on top.
PLAN_MAX_WAIT_S = 300.0


def plan_tokens(budget: PlanBudget, floor: int) -> int:
    """Output-token ceiling for a plan of this size (never below the caller's ``floor``)."""
    want = floor + PLAN_TOKENS_PER_PART * budget.target_parts + PLAN_TOKENS_PER_LEAF * budget.target_leaves
    return min(PLAN_TOKENS_MAX, max(floor, want))


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
    """Plan and write ``ws.plan_path``.  ``model`` may be injected (tests).

    Every attempt's money is booked inside ``plan_with_usage`` the moment it is
    paid (brief, first call, each re-ask) — success, ``PlanningError`` and a crash
    on a LATER attempt all leave the earlier dollars in the guard.  The ceilings
    are enforced once here, after the plan is written."""
    result, _ = plan_with_usage(spec, model_id, plan_model, ws, model=model, events=events, guard=budget,
                                runtime=runtime, template=template, example=example, temperature=temperature,
                                max_output_tokens=max_output_tokens, finalise=finalise, event_stats=event_stats)
    if budget is not None:
        budget.check()
    return result


def plan_with_usage[P: BaseModel](spec: Spec, model_id: str, plan_model: type[P], ws: Workspace, *, model: Any | None = None,
                    events: Any | None = None, guard: Any | None = None, runtime: Any | None = None, template: str | None = None,
                    example: dict[str, Any] | None = None, temperature: float = 0.4, max_output_tokens: int = 24000,
                    finalise: FinalisePlan | None = None, event_stats: EventStats | None = None) -> tuple[P, Usage]:
    """Optional brief expansion → one structured planner call → up to two re-asks.

    Parameterised by the track hooks: ``template``/``example`` (system prompt),
    ``finalise`` (post-validation fixup, default = ``ensure_acceptance(normalise_names(...))``)
    and ``event_stats`` (extra ``plan.done`` payload).

    The two re-asks have SEPARATE budgets and different complaints:
    ``MAX_VALIDATION_REASKS`` for a plan the schema rejects, ``MAX_QUALITY_REASKS`` for a
    plan that validates but is boxes-at-different-sizes against the derived budget
    (:func:`codeverse.tracks.plan_budget.plan_quality_complaint`).  A quality re-ask never eats
    the validation re-ask, and a plan that fails quality twice is still USED — a mediocre
    plan beats no plan.

    Output room comes from :func:`plan_tokens` (the budget sizes it), and a
    ``finish_reason=MAX_TOKENS`` error buys ONE more attempt with half again as much room
    instead of killing the run — the deeper plan is longer JSON and Gemini bills thinking
    against the same ceiling.

    ``guard`` (a ``BudgetGuard``; named so because ``budget`` is this function's local
    PlanBudget) is booked with a non-enforcing ``add`` the moment each call is PAID —
    brief, first call, every re-ask — so a later attempt that raises can never erase an
    earlier attempt's dollars.  ``PlanningError`` still carries the total usage for
    guard-less callers; when ``guard`` is given those dollars are already booked and the
    caller must NOT charge them again (:func:`plan` just runs one final ``check()``)."""
    if model is None:
        from codeverse.models import get_chat_model

        model = get_chat_model(model_id)
    brief, usage = (None, Usage())
    if brief_enabled(spec):
        brief, usage = expand_brief(spec, model_id, model=model, events=events)
        if guard is not None and (usage.cost_usd or usage.input_tokens or usage.output_tokens):
            guard.add(usage, stage="plan", role="planner", label="planner-brief")
    budget = plan_budget(spec, brief)
    unit = "passes" if spec.track is Track.GRAPHICS else "parts"
    system = build_system_prompt(spec, plan_model, runtime=runtime, template=template, example=example, budget=budget)
    user = build_user_prompt(spec, brief=brief, budget=budget, unit=unit)
    images = [ImagePart(path=r.path, label=f"{r.role}: {r.note}".strip(": ")) for r in spec.references]
    messages = [ChatMessage.user(user, images=images or None)]
    schema = plan_model.model_json_schema()
    last_error = ""
    invalid = requeried = grown = 0
    tokens = plan_tokens(budget, max_output_tokens)
    for attempt in range(2 + MAX_VALIDATION_REASKS + MAX_QUALITY_REASKS):
        req = ChatRequest(messages=messages, system=system, response_schema=schema, temperature=temperature,
                          thinking="medium", max_output_tokens=tokens, label=f"planner{'-retry' if attempt else ''}",
                          max_wait_s=PLAN_MAX_WAIT_S)
        try:
            resp = model.generate(req)
        except Exception as e:  # noqa: BLE001 — a truncated plan is retryable; anything else is not
            if not (_truncated(e) and not grown and tokens < PLAN_TOKENS_MAX):
                raise
            grown += 1
            tokens = min(PLAN_TOKENS_MAX, int(tokens * TRUNCATION_GROWTH))
            if events is not None:
                events.emit("plan.truncated", attempt=attempt, max_output_tokens=tokens)
            continue
        usage = usage + resp.usage
        if guard is not None:
            # booked where it is paid: a later attempt that raises cannot erase this dollar
            guard.add(resp.usage, stage="plan", role="planner",
                      label=f"planner{'-retry' if attempt else ''}")
        raw = resp.parsed if resp.parsed is not None else _parse_json(resp.text)
        try:
            if not isinstance(raw, dict):
                raise ValueError(f"planner returned {type(raw).__name__}, expected a JSON object")
            result = plan_model.model_validate(raw)
        except (ValidationError, ValueError) as e:
            last_error = str(e)[:4000]
            invalid += 1
            if events is not None:
                events.emit("plan.invalid", attempt=attempt, error=last_error[:500])
            if invalid > MAX_VALIDATION_REASKS:
                break
            messages = messages + [
                _echo(raw, resp.text),
                ChatMessage.user("Your plan failed validation. Fix EXACTLY these problems and return the full corrected "
                                 f"plan JSON again (same schema):\n{last_error}"),
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
        result = enrich_plan(result, brief, budget)
        ws.write_json(ws.plan_path, result)
        if events is not None:
            stats = (event_stats or default_event_stats)(result)
            events.emit("plan.done", model=model_id, **stats,
                        cost_usd=round(usage.cost_usd, 4), prompt_hash=prompt_hash(system), attempt=attempt,
                        brief=brief is not None, quality_reasks=requeried, **budget.as_dict())
        return result, usage
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
        contract=language_contract(lang, runtime)[:6000],
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
    plan_obj.acceptance = items
    return plan_obj


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


__all__ = ["MAX_QUALITY_REASKS", "MAX_VALIDATION_REASKS", "PLAN_TOKENS_MAX", "PlanningError", "plan",
           "plan_tokens", "plan_with_usage", "plan_example", "ensure_acceptance", "add_acceptance_item",
           "default_event_stats", "normalise_names", "build_system_prompt", "build_user_prompt", "load_prompt_or"]
