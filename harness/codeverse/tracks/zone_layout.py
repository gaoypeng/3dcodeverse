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
import math
from typing import Any

from codeverse.contracts.chat import ChatMessage, ChatRequest
from codeverse.contracts.plan import ScenePlan, ZoneLayout, ZonePlan
from codeverse.conventions import to_snake
from codeverse.models.schema_utils import parse_json_lenient
from codeverse.orchestrator import BudgetExceeded
from codeverse.proc import fan_out
from codeverse.prompts import render
from codeverse.tracks.generation import _deadline_preflight

log = logging.getLogger(__name__)

LAYOUT_TEMPLATE = "tracks/plan_zone_layout.j2"
LAYOUT_MAX_TOKENS = 4000
LAYOUT_WAIT_S = 300.0
#: a placement may multiply the plan's instances_hint by at most this much
COUNT_SLACK = 4
#: cluster centres may sit this far outside the zone bbox (assets have radius)
BBOX_MARGIN_M = 1.0
#: lens margin beyond the asset's own reach.  Two measurements set this: fv_izakaya_night
#: (2026-08-30) — BarCounter 0.7 m from the PotDetail camera put the lens INSIDE the
#: counter, three capped rounds, unfixable downstream; and dg_izakaya_night (2026-08-31)
#: — the first tune (1.2 m base + footprint/2 + full spread) rejected EVERY placement a
#: small indoor zone could offer ("WoodenStool 1.8 m from OdenStationDetail — keep
#: >= 1.8 m") until the layout was dropped entirely, muting the layer exactly where it
#: helps.  The validator now rejects only what puts the LENS inside the asset's reach
#: (half footprint + half spread + this margin, floored at 0.8 m); polite shot spacing
#: stays in the prompt, where a director can trade it off.
CAMERA_CLEAR_M = 0.4
CAMERA_CLEAR_FLOOR_M = 0.8
#: two LARGE assets whose cluster centres nearly coincide are stacked into each other —
#: fv2_alpine_night's RetainingWall x PrayerBench interpenetration.  Deliberately narrow:
#: only same-spot (< 0.6 m) pairs where BOTH footprints are >= 0.8 m and neither stands on
#: the other — a stool against a counter (small footprint) or bottles on a bar
#: (support relation) must never be rejected.
STACK_DIST_M = 0.6
STACK_MIN_FOOT_M = 0.8


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
        # the shot must survive the layout: a cluster whose footprint reaches a camera
        # puts geometry inside the lens (fv_izakaya_night), and nothing downstream can fix it
        foot = max(known[k].approx_size_m[0], known[k].approx_size_m[2])
        need = max(CAMERA_CLEAR_FLOOR_M, foot / 2 + p.spread_m / 2 + CAMERA_CLEAR_M)
        base = known[to_snake(p.support)].approx_size_m[1] if to_snake(p.support) in known else 0.0
        top = base + known[k].approx_size_m[1]
        for cam in plan.cameras:
            if cam.position[1] > top + CAMERA_CLEAR_M:
                # the check is 2D by design (clusters stand on the floor), so an aerial /
                # establishing camera must escape it vertically: a lens this far above the
                # cluster's top cannot be inside it, and rejecting every large placement
                # under a high camera dropped the whole zone's layout (review of PR #3)
                continue
            dist = math.hypot(x - cam.position[0], z - cam.position[2])
            if dist < need:
                problems.append(f"{p.asset} cluster ({x:.1f}, {z:.1f}) reaches camera {cam.name} "
                                f"({dist:.1f} m < {need:.1f} m = half its footprint+spread plus lens margin) — "
                                f"move the cluster or shrink its spread so the lens stays outside it")
    placed = {to_snake(p.asset) for p in layout.placements}
    missing = [c for c in zone.contents if to_snake(c) not in placed]
    if missing:
        problems.append(f"no placement for planned contents: {', '.join(missing)}")
    # two large assets on the same spot = stacked into each other
    rows = [(p, known.get(to_snake(p.asset))) for p in layout.placements]
    for i, (a, pa) in enumerate(rows):
        for b, pb in rows[i + 1:]:
            if pa is None or pb is None or to_snake(a.asset) == to_snake(b.asset):
                continue
            if to_snake(a.support) == to_snake(b.asset) or to_snake(b.support) == to_snake(a.asset):
                continue   # one stands on the other by design
            fa = max(pa.approx_size_m[0], pa.approx_size_m[2])
            fb = max(pb.approx_size_m[0], pb.approx_size_m[2])
            if fa < STACK_MIN_FOOT_M or fb < STACK_MIN_FOOT_M:
                continue
            dist = math.hypot(a.cluster[0] - b.cluster[0], a.cluster[1] - b.cluster[1])
            if dist < STACK_DIST_M:
                problems.append(f"{a.asset} and {b.asset} share one spot ({dist:.1f} m apart, footprints "
                                f"{fa:.1f}/{fb:.1f} m) — they will interpenetrate; separate the clusters or "
                                f"make one the other's support")
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
    cameras = [f"{c.name} at ({c.position[0]:.1f}, {c.position[2]:.1f}), fov {c.fov:.0f} — {c.purpose}"
               for c in plan.cameras]
    system = render(LAYOUT_TEMPLATE, title=plan.title, setting=plan.setting, mood=plan.mood,
                    environment=plan.environment[:600], zone=zone, neighbours=neighbours, assets=assets,
                    cameras=cameras, schema_fields=", ".join(ZoneLayout.model_fields))
    user = f"Lay out zone {zone.name}. Description (binding): {zone.description}\nContents to place: {', '.join(zone.contents) or '(none)'}"
    complaint = ""
    for attempt in (0, 1):
        # the run clock outranks the layout (review-3 S3): past the hard ceiling the
        # documented degraded mode is "no layout for this zone", and a call that does
        # go out gets the wall clock actually left, never a flat 300 s — the re-ask
        # preflights again, so it cannot buy a second window past the ceiling
        try:
            max_wait_s = _deadline_preflight(budget, LAYOUT_WAIT_S, soft=True, floor_s=20.0)
        except BudgetExceeded as e:
            if events is not None:
                events.emit("layout.skipped_budget", zone=zone.name, error=str(e)[:200])
            return None
        ask = user if not complaint else f"{user}\n\nYour previous layout was rejected: {complaint}. Fix exactly these problems."
        resp = model.generate(ChatRequest(messages=[ChatMessage.user(ask)], system=system,
                                          response_schema=ZoneLayout.model_json_schema(), temperature=0.3,
                                          thinking="low", max_output_tokens=LAYOUT_MAX_TOKENS,
                                          max_wait_s=max_wait_s, label="zone-layout"))
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
