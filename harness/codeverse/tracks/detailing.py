"""The DETAIL round: surface detail without moving anything.

Measured on the recorded corpus (wave "generation-depth"): across 88 consecutive
refine-round pairs the built part count never changed and mean Δgeometry_detail
was **+0.003** — the refine loop is a repair loop.  The rounds that *did* add
geometry added it while assembly was still broken and paid for it: > 2000 tris
added → Δassembly_fit −0.075 / Δoverall −0.025, against +0.078 / +0.092 for
rounds that changed ≤ 200 triangles.

So detail gets its own round kind, offered only once the structure gates are
clean (``orchestrator.rounds.detail_blocked``), with one promise — the silhouette,
the placement and the part list do not move — and one deterministic gate that
checks the promise (:func:`drift_gate`).
"""

from __future__ import annotations

from typing import Any

from codeverse.conventions import LANGUAGE_FRAME, Frame, to_pascal, to_snake

# --------------------------------------------------------------------------- detail-round drift gate
DRIFT_GATE = "detail_drift"


def drift_findings(before: Any, after: Any, *, tol_m: float, language: str = "") -> list[Any]:
    """Did a detail round move anything?  Findings for the ``detail_drift`` gate.

    The detail round's whole contract is "surface only": the silhouette, the part
    list and every part box stay put.  This is the deterministic check of that
    promise — the judge is never asked whether the shape moved, code answers it.
    """
    from codeverse.contracts.artifacts import GateFinding, Severity

    out: list[Any] = []
    if before is None or after is None:
        return out
    for axis, a, b in zip(_axes(language), before.extents, after.extents, strict=True):
        d = float(b) - float(a)
        if abs(d) > tol_m:
            out.append(GateFinding(
                gate=DRIFT_GATE, severity=Severity.ERROR, target="overall",
                message=f"the detail round changed the overall {axis} extent by {d * 1000:+.1f} mm "
                        f"({a:.3f} → {b:.3f} m); a detail round may not change the silhouette",
                fix_hint="revert whatever grew the object (a bevel that widened a part, a fastener sticking out) "
                         "and keep the added geometry inside the existing surfaces",
                data={"kind": "detail_drift", "axis": axis, "delta_m": round(d, 5)}))
    was = {to_snake(str(p.name)): p for p in (before.parts or ())}
    now = {to_snake(str(p.name)): p for p in (after.parts or ())}
    for key in sorted(set(was) - set(now)):
        out.append(GateFinding(
            gate=DRIFT_GATE, severity=Severity.ERROR, target=to_pascal(key),
            message=f"part '{to_pascal(key)}' disappeared during the detail round",
            fix_hint=f"restore '{to_pascal(key)}' exactly as it was before this round",
            data={"kind": "detail_drift", "removed": key}))
    for key in sorted(set(now) - set(was)):
        out.append(GateFinding(
            gate=DRIFT_GATE, severity=Severity.WARN, target=to_pascal(key),
            message=f"the detail round introduced a new top-level part '{to_pascal(key)}'",
            fix_hint="detail belongs inside an existing part; merge it into the part it decorates "
                     "(or accept it only if the plan names it)",
            data={"kind": "detail_drift", "added": key}))
    moved = 0
    for key in sorted(set(was) & set(now)):
        a, b = was[key], now[key]
        dc = max(abs((bl + bh) / 2 - (al + ah) / 2)
                 for al, ah, bl, bh in zip(a.bbox_min, a.bbox_max, b.bbox_min, b.bbox_max, strict=True))
        de = max(abs((bh - bl) - (ah - al))
                 for al, ah, bl, bh in zip(a.bbox_min, a.bbox_max, b.bbox_min, b.bbox_max, strict=True))
        if max(dc, de) <= tol_m:
            continue
        moved += 1
        if moved > 8:
            continue
        out.append(GateFinding(
            gate=DRIFT_GATE, severity=Severity.ERROR, target=to_pascal(key),
            message=f"part '{to_pascal(key)}' moved {dc * 1000:.1f} mm / resized {de * 1000:.1f} mm during the "
                    f"detail round (tolerance {tol_m * 1000:.0f} mm)",
            fix_hint="put the part back on its previous centre and extents; add the detail inside that box",
            data={"kind": "detail_drift", "part": key, "centre_mm": round(dc * 1000, 2),
                  "extent_mm": round(de * 1000, 2)}))
    if moved > 8:
        out.append(GateFinding(gate=DRIFT_GATE, severity=Severity.ERROR, target="overall",
                               message=f"{moved} parts moved during the detail round ({moved - 8} more not listed)",
                               fix_hint="revert the placement changes; this round may only add surface geometry",
                               data={"kind": "detail_drift", "moved": moved}))
    if not out:
        d_tri = int(getattr(after, "tri_count", 0) or 0) - int(getattr(before, "tri_count", 0) or 0)
        out.append(GateFinding(gate=DRIFT_GATE, severity=Severity.INFO, target="overall",
                               message=f"detail round held the contract: no part moved more than "
                                       f"{tol_m * 1000:.0f} mm; {d_tri:+,} triangles added",
                               data={"kind": "detail_drift", "delta_tris": d_tri}))
    return out


def _axes(language: str) -> tuple[str, str, str]:
    """Axis letters as the AUTHOR sees them.  ``Measurement.extents`` is in the GLB frame
    (Y-up); a blender/cadquery/urdf author thinks Z-up, where glb (x, y, z) reads (x, z, y)
    — so naming the GLB axis in a fix hint would send them to the wrong dimension."""
    if LANGUAGE_FRAME.get(language) is Frame.Z_UP_NEG_Y_FRONT:
        return ("x", "z", "y")
    return ("x", "y", "z")


def drift_gate(before: Any, after: Any, *, tol_m: float, language: str = "") -> Any:
    """``detail_drift`` GateReport (passing when nothing moved)."""
    from codeverse.contracts.artifacts import GateReport, Severity

    findings = drift_findings(before, after, tol_m=tol_m, language=language)
    return GateReport(gate=DRIFT_GATE, findings=findings,
                      passed=not any(f.severity is Severity.ERROR for f in findings))


# --------------------------------------------------------------------------- detail-round tasks
#: judge improvement-plan kinds a DETAIL round is allowed to act on.  Assembly/placement
#: work is the repair loop's job and would break the no-drift contract.
DETAIL_KINDS = frozenset({"geometry", "detail", "material", "materials", "craftsmanship", "texture", "finish"})

#: what a detail round always does, whatever the judge said.  Ordered by measured value per
#: triangle: edge treatment first (it changes how every surface reads under light), then the
#: features a viewer counts, then wear.
DEFAULT_DETAIL_LINES: tuple[str, ...] = (
    "Bevel or chamfer every hard edge that a real version of this object would have "
    "(2-6 mm on furniture and cast parts, 0.5-2 mm on sheet metal and small mechanisms).",
    "Add the panel lines, seams and shut-lines where the real object's shells meet "
    "(lids, drawers, housings, trays): 1-2 mm wide, ~1 mm deep insets.",
    "Add the fasteners the real object is held together with — bolt heads, rivets, screws, "
    "hinges — arrayed in a loop at the joints, sized 3-10 mm.",
    "Give each material family a distinct roughness/metalness and a slightly different value; "
    "no two different materials may share the same flat grey.",
)


def detail_instructions(last: Any, plan: Any, *, max_lines: int = 8) -> list[str]:
    """Instruction lines for a detail round: the measured density gap first, then the
    judge's detail-shaped asks, then the standing detail vocabulary; deduped and capped."""
    lines: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        key = text.strip().lower()[:80]
        if key and key not in seen:
            seen.add(key)
            lines.append(text.strip())

    for g in getattr(last, "gates", None) or ():
        for f in getattr(g, "findings", None) or ():
            if (f.data or {}).get("kind") == "under_tri_budget":
                _add(f"{f.message}. {f.fix_hint}")
    j = getattr(last, "judgment", None)
    for item in sorted(getattr(j, "improvement_plan", None) or (),
                       key=lambda i: (i.priority, -getattr(i, "expected_gain", 0.0))):
        if str(getattr(item, "kind", "")).lower() not in DETAIL_KINDS:
            continue
        target = getattr(item, "target", "") or "overall"
        _add(f"{target}: {item.instruction}")
    for issue in getattr(j, "issues", None) or ():
        if str(getattr(issue, "kind", "")).lower() in ("material", "materials", "detail"):
            _add(f"{issue.target or 'overall'}: {issue.detail}")
    for text in DEFAULT_DETAIL_LINES:
        _add(text)
    return lines[:max_lines]
