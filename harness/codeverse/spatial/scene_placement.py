"""Scene placement gate ``scene_placement``: floating / sunken / unsupported /
interpenetrating assets, measured — not judged.

Added 2026-08-26.  Audit that day: the scene track had NO deterministic placement
check (``probes.py`` reported 2-D zone-footprint overlap as INFO; the census
``ground_y`` fed camera checks only); the scene_v1 ``floating_part`` cap (kinds
floating / sunken / unsupported, gate ``*``) could never fire because no scene gate
emitted those words; ``contracts/plan.py`` declared acceptance ``how: probe`` that
nothing probed; in 52 recorded scene sessions the object tools ``check_connectivity``
/ ``measure`` were called 0 times (they fail on a scene workspace).  Floating /
sunken was left to the VLM (defect −0.06).

Numbers come from ``runtime_js/lib/host_placement.mjs`` (per placed asset: foot-column
gap to the surface beneath, burial depth, water, contacts; 3-D interpenetration pairs),
carried in the probe census under ``placement``; this module turns them into
``GateFinding``\ s whose messages contain the cap words and whose ``fix_hint`` names
the move ("lower X by 0.23 m onto Terrain").  Sunk is judged relative to the asset's
height and its name words because the harness's own starter scene buries rocks
20–50 % of their height, digs a pond basin 1.4 m under the terrain and drives jetty
posts 0.97 m into the pond bed — a naive "> 0.10 m" rule flagged all of them
(measured 2026-08-26).  Word matching is a set lookup on ``to_snake`` words.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.conventions import to_snake
from codeverse.proc import read_json_or_none
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

GATE = "scene_placement"
CONTACT_M = 0.02                 # a foot this close to what is under it rests on it
FLOATING_OUTDOOR_M, FLOATING_INDOOR_M = 0.05, 0.02
FLOATING_ERROR_M, FLOATING_ERROR_COUNT = 0.15, 3   # this high, or more than this many, → ERROR
SUNK_M, SUNK_ERROR_M = 0.10, 0.30
SUNK_ERROR_FRAC = 0.25           # an ERROR burial must also swallow this fraction of the height
PARTIAL_OK_FRAC, PARTIAL_OK_ERROR_FRAC = 0.50, 0.75   # rocks / posts / bushes: WARN past half, ERROR past 3/4
OVERLAP_WARN, OVERLAP_ERROR = 0.20, 0.60
MAX_FINDINGS_PER_KIND = 8

#: setting / environment words that make a scene "indoors" (tighter floating tolerance)
INDOOR_WORDS = frozenset({
    "attic", "basement", "bathroom", "bedroom", "cabin", "cellar", "chamber", "classroom", "cockpit", "corridor", "garage", "hall", "hut", "indoor",
    "indoors", "inside", "interior", "kitchen", "lab", "laboratory", "library", "lobby", "office", "room", "studio", "tent", "warehouse", "workshop",
})
#: things that are BELOW ground by definition: never "sunken"
BURIED_OK_WORDS = frozenset({
    "basin", "bed", "canal", "cave", "cellar", "crater", "ditch", "drain", "foundation", "foundations", "grave", "gutter", "hole", "lakebed", "moat",
    "pit", "pool", "riverbed", "trench", "tunnel", "well",
})
#: things normally driven or grown into the ground: sunken only past half their height
PARTIAL_OK_WORDS = frozenset({
    "boulder", "boulders", "bridge", "bush", "bushes", "cliff", "cliffs", "dock", "dune", "dunes", "fence", "fences", "flower", "flowers", "grass",
    "hill", "hills", "jetty", "log", "logs", "mound", "mounds", "outcrop", "pebble", "pebbles", "pier", "pile", "piles", "plant", "plants", "pole",
    "poles", "post", "posts", "reed", "reeds", "rock", "rocks", "root", "roots", "shrub", "shrubs", "stake", "stakes", "stone", "stones", "stump",
    "stumps", "tree", "trees", "trunk", "trunks", "tuft", "tufts",
})


class AssetRow(BaseModel):
    """One row of the census placement table (``host_placement.mjs``)."""

    model_config = ConfigDict(extra="ignore")

    name: str
    zone: str = ""
    exempt: str = ""
    bbox: dict[str, Any] | None = None
    ground_gap_m: float | None = None
    support: str = ""
    sunk_m: float = 0.0
    sunk_into: str = ""
    on_water: bool = False
    attached: list[str] = Field(default_factory=list)

    @property
    def qualified(self) -> str:
        """``Zone/Name`` — routes to the zone's file in refine tasks, stays unique per asset."""
        return f"{self.zone}/{self.name}" if self.zone else self.name

    @property
    def height(self) -> float:
        return float(self.bbox["size"][1]) if self.bbox and self.bbox.get("size") else 0.0


class Interpenetration(BaseModel):
    model_config = ConfigDict(extra="ignore")

    a: str
    b: str
    zone_a: str = ""
    zone_b: str = ""
    aabb_overlap: float = 0.0   # box overlap / smaller box (the reported number)
    inside_frac: float = 0.0    # vertices of the smaller inside the larger (the confirmation)


class PlacementTable(BaseModel):
    model_config = ConfigDict(extra="ignore")

    assets: list[AssetRow] = Field(default_factory=list)
    interpenetrations: list[Interpenetration] = Field(default_factory=list)
    total: int = 0
    checked: int = 0
    truncated: bool = False
    exempt: dict[str, int] = Field(default_factory=dict)
    ground_y: float | None = None
    notes: list[str] = Field(default_factory=list)
    error: str = ""


def _words(name: str) -> set[str]:
    return {w for w in to_snake(name).split("_") if w and not w.isdigit()}


def infer_indoor(text: str) -> bool:
    """Does the plan's setting / environment text say the scene is indoors?"""
    return bool(_words(text) & INDOOR_WORDS)


def _f(sev: Severity, msg: str, *, target: str, hint: str = "", kind: str, **data: Any) -> GateFinding:
    return GateFinding(gate=GATE, severity=sev, target=target, message=msg, fix_hint=hint, data={"kind": kind, **data})


def _support_label(row: AssetRow, ground_y: float | None) -> str:
    if row.support and row.support != "ground_y":
        return row.support
    return f"the ground (census ground_y = {ground_y:.2f} m)" if ground_y is not None else "the ground"


def _sunk_severity(row: AssetRow) -> Severity | None:
    """WARN / ERROR / None for a buried foot, honouring height fraction and name words."""
    if row.sunk_m <= 0 or row.on_water:
        return None
    words = _words(row.name)
    if words & BURIED_OK_WORDS:
        return None
    frac = row.sunk_m / row.height if row.height > 1e-6 else 1.0
    if words & PARTIAL_OK_WORDS:
        if frac > PARTIAL_OK_ERROR_FRAC and row.sunk_m > SUNK_ERROR_M:
            return Severity.ERROR
        return Severity.WARN if frac > PARTIAL_OK_FRAC else None
    if row.sunk_m > SUNK_ERROR_M and frac > SUNK_ERROR_FRAC:
        return Severity.ERROR
    return Severity.WARN if row.sunk_m > SUNK_M else None


def _asset_findings(rows: list[AssetRow], *, floating_m: float, ground_y: float | None) -> list[GateFinding]:
    out: list[GateFinding] = []
    floating: list[tuple[AssetRow, float]] = []
    for row in rows:
        q = row.qualified
        sev = _sunk_severity(row)
        if sev is not None:
            out.append(_f(sev, f"{q} is sunken {row.sunk_m:.2f} m into {row.sunk_into or 'the ground'}", target=q,
                          hint=f"raise {q} by {row.sunk_m:.2f} m so its lowest point sits on {row.sunk_into or 'the ground'}",
                          kind="sunken", sunk_m=row.sunk_m, into=row.sunk_into, zone=row.zone))
            continue
        gap = row.ground_gap_m
        if gap is None or row.on_water or row.sunk_m > 0 or gap <= CONTACT_M:
            continue
        support = _support_label(row, ground_y)
        if gap > floating_m and row.attached:
            out.append(_f(Severity.WARN, f"{q} is floating {gap:.2f} m above {support}, but its bbox touches {', '.join(row.attached[:3])}",
                          target=q, hint=f"fine if {q} is mounted on {row.attached[0]}; otherwise lower it by {gap:.2f} m onto {support}",
                          kind="floating", gap_m=gap, attached=row.attached[:3], zone=row.zone))
        elif gap > floating_m:
            floating.append((row, gap))
        elif not row.attached:
            out.append(_f(Severity.WARN, f"{q} is unsupported: hovers {gap:.2f} m above {support} and touches nothing", target=q,
                          hint=f"lower {q} by {gap:.2f} m onto {support}, or attach it to a neighbour; "
                               f"if it is meant to hang free set {row.name}.userData.placement = 'free'",
                          kind="unsupported", gap_m=gap, zone=row.zone))
    many = len(floating) > FLOATING_ERROR_COUNT
    for row, gap in floating:
        q = row.qualified
        support = _support_label(row, ground_y)
        sev = Severity.ERROR if (many or gap > FLOATING_ERROR_M) else Severity.WARN
        out.append(_f(sev, f"{q} is floating {gap:.2f} m above {support} (touches nothing)", target=q,
                      hint=f"lower {q} by {gap:.2f} m onto {support} — seat it with heightAt(x, z) or on the surface it stands on; "
                           f"a deliberately airborne thing gets {row.name}.userData.placement = 'free'",
                      kind="floating", gap_m=gap, zone=row.zone))
    return out


def _pair_findings(pairs: list[Interpenetration]) -> list[GateFinding]:
    out: list[GateFinding] = []
    for p in pairs:
        qa = f"{p.zone_a}/{p.a}" if p.zone_a else p.a
        qb = f"{p.zone_b}/{p.b}" if p.zone_b else p.b
        sev = Severity.ERROR if p.aabb_overlap > OVERLAP_ERROR else Severity.WARN
        out.append(_f(sev, f"interpenetration: {qa} and {qb} overlap ({p.aabb_overlap:.0%} of the smaller box; "
                           f"{p.inside_frac:.0%} of its vertices inside the other)", target=qa,
                      hint=f"move {qa} out of {qb} or shrink one of them so their boxes share ≤ {OVERLAP_WARN:.0%}",
                      kind="interpenetration", overlap=p.aabb_overlap, inside_frac=p.inside_frac, other=qb, zone=p.zone_a))
    return out


def _cap_per_kind(findings: list[GateFinding]) -> list[GateFinding]:
    """Keep the worst ``MAX_FINDINGS_PER_KIND`` per kind (ERROR first, then magnitude); the
    rest becomes one INFO line, so 30 floating pebbles cannot flood the refine prompt."""
    rank = {Severity.ERROR: 0, Severity.WARN: 1, Severity.INFO: 2}
    by_kind: dict[str, list[GateFinding]] = {}
    for f in findings:
        by_kind.setdefault(str(f.data.get("kind")), []).append(f)
    out: list[GateFinding] = []
    for kind, fs in by_kind.items():
        fs.sort(key=lambda f: (rank[f.severity], -float(f.data.get("gap_m") or f.data.get("sunk_m") or f.data.get("overlap") or 0)))
        out.extend(fs[:MAX_FINDINGS_PER_KIND])
        if len(fs) > MAX_FINDINGS_PER_KIND:
            out.append(_f(Severity.INFO, f"… and {len(fs) - MAX_FINDINGS_PER_KIND} more {kind} finding(s) not listed", target="scene",
                          kind="truncated", of=kind))
    return out


def placement_findings(table: dict[str, Any] | PlacementTable, *, indoor: bool = False, duration_ms: int = 0) -> GateReport:
    """The ``scene_placement`` GateReport for one census placement table (pure)."""
    t = table if isinstance(table, PlacementTable) else PlacementTable.model_validate(table or {})
    if t.error:
        return GateReport(gate=GATE, passed=True, duration_ms=duration_ms, findings=[
            _f(Severity.WARN, f"placement probe failed: {t.error[:300]}", target="scene", kind="probe_failed",
               hint="harness instrumentation, not your code; the placement check was skipped this round")])
    floating_m = FLOATING_INDOOR_M if indoor else FLOATING_OUTDOOR_M
    rows = [r for r in t.assets if not r.exempt]
    findings = _cap_per_kind(_asset_findings(rows, floating_m=floating_m, ground_y=t.ground_y) + _pair_findings(t.interpenetrations))
    counts = {k: sum(1 for f in findings if f.data.get("kind") == k and f.severity != Severity.INFO)
              for k in ("floating", "sunken", "unsupported", "interpenetration")}
    n_exempt = sum(t.exempt.values())
    summary = (f"{t.checked} assets checked ({t.total} placed, {n_exempt} exempt): {counts['floating']} floating, "
               f"{counts['sunken']} sunken, {counts['unsupported']} unsupported, {counts['interpenetration']} interpenetrating"
               + (f"; {'indoor' if indoor else 'outdoor'} tolerance {floating_m * 100:.0f} cm")
               + ("; table truncated (first 400 assets / time budget)" if t.truncated else ""))
    findings.insert(0, _f(Severity.INFO, summary, target="scene", kind="summary", counts=counts, checked=t.checked, total=t.total,
                          exempt=t.exempt, truncated=t.truncated))
    passed = not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=GATE, passed=passed, findings=findings, duration_ms=duration_ms)


def placement_census(ws: Workspace, *, force_probe: bool = False, timeout_s: float = 60.0) -> dict[str, Any]:
    """The census dict carrying ``placement``: the last build's ``artifacts/census.json``,
    or a fresh ``probe_scene`` when it is missing / predates the table / ``force_probe``."""
    census = None if force_probe else read_json_or_none(ws.artifacts / "census.json")
    if isinstance(census, dict) and isinstance(census.get("placement"), dict):
        return census
    from codeverse.spatial.probes import probe_scene

    res = probe_scene(ws, timeout_s=timeout_s)
    if res.errors:
        return {"placement": {"error": res.errors[0]}}
    census = res.census or {}
    if not isinstance(census.get("placement"), dict):
        census["placement"] = {"error": "the scene did not boot, so nothing was placed" if not census else "probe driver returned no placement table"}
    return census


def _setting_text(ws: Workspace) -> str:
    plan = read_json_or_none(ws.plan_path)
    if not isinstance(plan, dict):
        return ""
    return " ".join(str(plan.get(k) or "") for k in ("setting", "environment", "title"))


def check_placement(ws: Workspace, *, indoor: bool | None = None, force_probe: bool = False, timeout_s: float = 60.0) -> GateReport:
    """Gate ``scene_placement`` for a workspace (census of the last build, or a fresh probe)."""
    t0 = time.time()
    census = placement_census(ws, force_probe=force_probe, timeout_s=timeout_s)
    if indoor is None:
        indoor = infer_indoor(_setting_text(ws))
    return placement_findings(census.get("placement") or {}, indoor=indoor, duration_ms=int((time.time() - t0) * 1000))


def placement_gate_safe(census: dict[str, Any] | None, *, plan: Any = None) -> GateReport | None:
    """Round-gate entry: ``None`` when the census has no placement table (scene did not
    boot, or an older driver), a WARN-only report when anything raises — never an
    exception, so the placement check cannot kill a round."""
    try:
        table = (census or {}).get("placement")
        if not isinstance(table, dict):
            return None
        text = " ".join(str(getattr(plan, k, "") or "") for k in ("setting", "environment", "title"))
        return placement_findings(table, indoor=infer_indoor(text))
    except Exception as e:  # noqa: BLE001 — advisory instrumentation must not fail the round
        log.warning("scene placement gate failed: %s", e)
        return GateReport(gate=GATE, passed=True, findings=[
            _f(Severity.WARN, f"placement probe failed: {type(e).__name__}: {e}"[:400], target="scene", kind="probe_failed")])


def placement_table_text(table: dict[str, Any] | PlacementTable, *, max_rows: int = 40) -> str:
    """The per-asset table behind the findings (tool observations / prompts)."""
    t = table if isinstance(table, PlacementTable) else PlacementTable.model_validate(table or {})
    if t.error:
        return f"placement table unavailable: {t.error}"
    lines = [f"placement: {t.checked} checked of {t.total} placed; exempt {t.exempt or '{}'}; ground_y {t.ground_y}"
             + ("; TRUNCATED" if t.truncated else "")]
    lines.append("asset | gap to support (m) | support | sunk (m) | into | water | attached")
    for r in [r for r in t.assets if not r.exempt][:max_rows]:
        lines.append(f"{r.qualified} | {'-' if r.ground_gap_m is None else f'{r.ground_gap_m:+.3f}'} | {r.support or '-'} | "
                     f"{r.sunk_m:.3f} | {r.sunk_into or '-'} | {'yes' if r.on_water else '-'} | {', '.join(r.attached[:3]) or '-'}")
    for p in t.interpenetrations[:10]:
        lines.append(f"interpenetration {p.zone_a}/{p.a} × {p.zone_b}/{p.b}: {p.aabb_overlap:.0%} box, {p.inside_frac:.0%} inside")
    lines += [f"note: {n}" for n in t.notes[:3]]
    return "\n".join(lines)
