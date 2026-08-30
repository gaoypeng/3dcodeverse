"""L2 zone layouts: one cheap structured call per zone, in parallel, validated.

Why this layer exists (measured 2026-08-30 across the 48 scored scene runs):
``ZonePlan.contents`` is a list of NAMES — the zone agent freehand-places
everything, and the final rounds still carried 55 interpenetration, 38 sunken
and 22 floating gate ERRORS, with undressed_scene standing in 31/48 verdicts.
The layout is planning work, so it is done by the PLANNER model with a schema
(never a coding agent), validated deterministically against the zone bbox and
the plan, re-asked once with the exact complaint, and handed to the zone
builder as numbers to realise.

Layouts are an optional accelerator like the engineering brief: any failure
returns no layout for that zone and generation proceeds exactly as before.
"""

from __future__ import annotations

import logging
from typing import Any

from codeverse.contracts.chat import ChatMessage, ChatRequest
from codeverse.contracts.plan import ScenePlan, ZoneLayout, ZonePlan
from codeverse.conventions import to_snake
from codeverse.models.schema_utils import parse_json_lenient
from codeverse.proc import fan_out
from codeverse.prompts import render

log = logging.getLogger(__name__)

LAYOUT_TEMPLATE = "tracks/plan_zone_layout.j2"
LAYOUT_MAX_TOKENS = 4000
LAYOUT_WAIT_S = 300.0
#: a placement may multiply the plan's instances_hint by at most this much
COUNT_SLACK = 4
#: cluster centres may sit this far outside the zone bbox (assets have radius)
BBOX_MARGIN_M = 1.0


def validate_layout(layout: ZoneLayout, zone: ZonePlan, plan: ScenePlan) -> str:
    """"" when the layout is buildable; otherwise the exact complaint to re-ask with."""
    problems: list[str] = []
    known = {to_snake(a.name): a for a in plan.assets}
    hints = {to_snake(a.name): max(1, a.instances_hint) for a in plan.assets}
    lo, hi = zone.bbox.min, zone.bbox.max
    for p in layout.placements:
        k = to_snake(p.asset)
        if k not in known:
            problems.append(f"placement names unknown asset '{p.asset}' (plan assets: {', '.join(sorted(known))})")
            continue
        x, z = p.cluster
        if not (lo[0] - BBOX_MARGIN_M <= x <= hi[0] + BBOX_MARGIN_M and lo[2] - BBOX_MARGIN_M <= z <= hi[2] + BBOX_MARGIN_M):
            problems.append(f"{p.asset} cluster ({x:.1f}, {z:.1f}) is outside zone {zone.name} bbox "
                            f"x {lo[0]:.1f}..{hi[0]:.1f}, z {lo[2]:.1f}..{hi[2]:.1f}")
        if p.count > COUNT_SLACK * hints[k]:
            problems.append(f"{p.asset} count {p.count} is over {COUNT_SLACK}x the plan's instances_hint {hints[k]}")
    placed = {to_snake(p.asset) for p in layout.placements}
    missing = [c for c in zone.contents if to_snake(c) not in placed]
    if missing:
        problems.append(f"no placement for planned contents: {', '.join(missing)}")
    return "; ".join(problems)


def layout_block(layout: ZoneLayout | dict[str, Any] | None) -> str:
    """The prompt table a zone builder realises (`""` when there is no layout)."""
    if layout is None:
        return ""
    lay = layout if isinstance(layout, ZoneLayout) else ZoneLayout.model_validate(layout)
    lines = []
    for p in lay.placements:
        faces = f", facing {p.faces}" if p.faces else ""
        support = f", on {p.support}" if p.support and p.support != "ground" else ""
        lines.append(f"- {p.count}x {p.asset} around ({p.cluster[0]:.1f}, {p.cluster[1]:.1f}) "
                     f"spread {p.spread_m:.1f} m{faces}{support}")
    if lay.path_points:
        pts = " -> ".join(f"({x:.1f}, {z:.1f})" for x, z in lay.path_points)
        lines.append(f"- path polyline: {pts}")
    dress = [f"{n} {label}" for n, label in ((lay.mid_props, "mid props"), (lay.small_props, "small props"),
                                             (lay.ground_cover, "ground-cover instances")) if n]
    if dress:
        lines.append("- dressing beyond the placements: " + ", ".join(dress))
    if lay.notes:
        lines.append(f"- intent: {lay.notes}")
    return "\n".join(lines)


def _one_layout(zone: ZonePlan, plan: ScenePlan, model: Any, budget: Any, events: Any) -> ZoneLayout | None:
    neighbours = [f"{z.name}: x {z.bbox.min[0]:.0f}..{z.bbox.max[0]:.0f}, z {z.bbox.min[2]:.0f}..{z.bbox.max[2]:.0f}"
                  for z in plan.zones if z.name != zone.name]
    assets = [f"{a.name}: {a.approx_size_m[0]:g}x{a.approx_size_m[1]:g}x{a.approx_size_m[2]:g} m, ~{a.instances_hint} planned"
              for a in plan.assets if to_snake(a.name) in {to_snake(c) for c in zone.contents}] or ["(no planned assets — dressing only)"]
    system = render(LAYOUT_TEMPLATE, title=plan.title, setting=plan.setting, mood=plan.mood,
                    environment=plan.environment[:600], zone=zone, neighbours=neighbours, assets=assets,
                    schema_fields=", ".join(ZoneLayout.model_fields))
    user = f"Lay out zone {zone.name}. Description (binding): {zone.description}\nContents to place: {', '.join(zone.contents) or '(none)'}"
    complaint = ""
    for attempt in (0, 1):
        ask = user if not complaint else f"{user}\n\nYour previous layout was rejected: {complaint}. Fix exactly these problems."
        resp = model.generate(ChatRequest(messages=[ChatMessage.user(ask)], system=system,
                                          response_schema=ZoneLayout.model_json_schema(), temperature=0.3,
                                          thinking="low", max_output_tokens=LAYOUT_MAX_TOKENS,
                                          max_wait_s=LAYOUT_WAIT_S, label="zone-layout"))
        if budget is not None:
            budget.add(resp.usage, stage="plan")
        raw = resp.parsed if resp.parsed is not None else parse_json_lenient(resp.text or "{}")
        layout = ZoneLayout.model_validate(raw)
        layout.zone = zone.name   # the name is an input, never model-invented
        complaint = validate_layout(layout, zone, plan)
        if not complaint:
            if events is not None:
                events.emit("layout.done", zone=zone.name, placements=len(layout.placements), reasked=bool(attempt))
            return layout
    if events is not None:
        events.emit("layout.rejected", zone=zone.name, complaint=complaint[:300])
    log.warning("zone layout for %s rejected twice (%s); building without one", zone.name, complaint)
    return None


def layout_zones(plan: ScenePlan, model: Any, *, budget: Any = None, events: Any = None,
                 max_workers: int = 6) -> dict[str, ZoneLayout]:
    """One validated layout per zone, computed in parallel.  Failures drop out silently
    (an exception or double rejection yields no layout for that zone, never a dead run)."""

    def _safe(zone: ZonePlan) -> ZoneLayout | None:
        return _one_layout(zone, plan, model, budget, events)

    results = fan_out(list(plan.zones), _safe, max_workers=max_workers, label="layouts",
                      item_name=lambda z: z.name)
    out: dict[str, ZoneLayout] = {}
    for zone, r in zip(plan.zones, results, strict=True):
        if isinstance(r, Exception):
            if events is not None:
                events.emit("layout.failed", zone=zone.name, error=f"{type(r).__name__}: {r}"[:300])
            log.warning("zone layout for %s failed (%s); building without one", zone.name, r)
        elif r is not None:
            out[zone.name] = r
    return out
