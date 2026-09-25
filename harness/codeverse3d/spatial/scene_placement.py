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
``GateFinding`` rows whose messages contain the cap words and whose ``fix_hint`` names
the move ("lower X by 0.23 m onto Terrain").  Sunk is judged relative to the asset's
height and its name words because the harness's own starter scene buries rocks
20–50 % of their height, digs a pond basin 1.4 m under the terrain and drives jetty
posts 0.97 m into the pond bed — a naive "> 0.10 m" rule flagged all of them
(measured 2026-08-26).  Word matching is a set lookup on ``to_snake`` words.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse3d.conventions import to_snake
from codeverse3d.proc import read_json_or_none
from codeverse3d.spatial.node import runtime_js_dir
from codeverse3d.workspace import Workspace

if TYPE_CHECKING:
    from codeverse3d.spatial.probes import SceneProbeResult

log = logging.getLogger(__name__)

GATE = "scene_placement"
CONTACT_M = 0.02                 # a foot this close to what is under it rests on it
FLOATING_OUTDOOR_M, FLOATING_INDOOR_M = 0.05, 0.02
FLOATING_ERROR_M, FLOATING_ERROR_COUNT = 0.15, 3   # this high, or more than this many, → ERROR
SUNK_M, SUNK_ERROR_M = 0.10, 0.30
SUNK_ERROR_FRAC = 0.25           # an ERROR burial must also swallow this fraction of the height
OVERLAP_WARN, OVERLAP_ERROR = 0.20, 0.60
MAX_FINDINGS_PER_KIND = 8

#: setting / environment words that make a scene "indoors" (tighter floating tolerance)
INDOOR_WORDS = frozenset({
    "attic", "basement", "bathroom", "bedroom", "cabin", "cellar", "chamber", "classroom", "cockpit", "corridor", "garage", "hall", "hut", "indoor",
    "indoors", "inside", "interior", "kitchen", "lab", "laboratory", "library", "lobby", "office", "room", "studio", "tent", "warehouse", "workshop",
})
#: What counts as seated, ONE file shared with the boot-time settle (``host_placement.settleScene``),
#: so the gate never reports what the settle deliberately leaves (audit 2026-09-24 N27): name words
#: exempt from "sunken" — below ground by definition (``buried_ok``), following the terrain
#: (``slope_ok``), or driven / grown in up to ``partial_ok_frac`` of their height (``partial_ok``).
#: The settle's other acceptance — a floating asset that touches a neighbour is mounted on it — is
#: ``AssetRow.attached`` here.
PLACEMENT_WORDS = "placement_words.json"


class _SeatedWords(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    buried_ok: frozenset[str]
    slope_ok: frozenset[str]
    partial_ok: frozenset[str]
    partial_ok_frac: float


@functools.cache
def _seated_words() -> _SeatedWords:
    return _SeatedWords.model_validate_json((runtime_js_dir() / "lib" / PLACEMENT_WORDS).read_text(encoding="utf-8"))


def _nums(v: object) -> list[float] | None:
    """Three numbers, or None: a census group with no geometry measures its box as nulls
    (sota_sydney_opera's HarbourBridgeZone), which must skip a check, not crash the gate."""
    if not isinstance(v, list | tuple) or len(v) != 3:
        return None
    try:
        return [float(x) for x in v]
    except (TypeError, ValueError):
        return None


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
    inner: list[str] = Field(
        default_factory=list,
        description="named descendants of this row (host_placement.MAX_INNER_NAMES) — a zone "
                    "often wraps its content in one group, and the plan-contents check has to "
                    "see through it",
    )
    families: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="per named family below this row with >= 2 members: {n, size_m} — the median "
                    "largest extent of ONE member, so a wrapper holding twelve fence panels is "
                    "scale-checked as a fence panel, not as the 15 m run",
    )

    def instance_size(self, key: str) -> tuple[int, float]:
        """``(members, median largest extent)`` of the family the plan's ``key`` names inside
        this row, ``(0, 0.0)`` when the row is not a wrapper of such instances."""
        best = (0, 0.0)
        for fam, v in self.families.items():
            if key in to_snake(fam) and int(v.get("n", 0)) >= 2 and float(v.get("size_m", 0.0)) > 0 and int(v["n"]) > best[0]:
                best = (int(v["n"]), float(v["size_m"]))
        return best

    @property
    def qualified(self) -> str:
        """``Zone/Name`` — routes to the zone's file in refine tasks, stays unique per asset."""
        return f"{self.zone}/{self.name}" if self.zone else self.name

    @property
    def height(self) -> float:
        size = _nums((self.bbox or {}).get("size"))
        return size[1] if size else 0.0


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
    """Does the plan's setting / environment text say the scene is indoors?  (The word
    list is the fallback for a plan written before ``ScenePlan.interior`` existed; the
    typed flag wins — :func:`is_interior`.)"""
    return bool(_words(text) & INDOOR_WORDS)


def is_interior(plan: Any) -> bool:
    """The plan's typed ``interior`` flag (D69) when it says so, else the setting text."""
    flag = plan.get("interior") if isinstance(plan, dict) else getattr(plan, "interior", None)
    return bool(flag) or infer_indoor(setting_text(plan))


#: the fix hints / messages that name a language's API, per scene language (``census["language"]``;
#: a census without one is three.js, as every recorded round is).  The checks and their numbers
#: are the same for every language; only the words that tell the author what to type differ.
LANGUAGE_WORDS: dict[str, dict[str, str]] = {
    "scene_threejs": {
        "seat": "seat it with heightAt(x, z) or on the surface it stands on",
        "free": "{name}.userData.placement = 'free'",
        "no_fog_msg": "scene.fog is not set — frames read as thin_atmosphere (32/48 measured runs)",
        "no_fog": "in buildEnv set scene.fog = new THREE.Fog(<sky horizon hex>, near, far) with the plan's numbers",
        "no_background_msg": "scene.background is not set (renders on the raw clear colour)",
        "no_background": "in buildEnv set scene.background to the sky colour or sky texture the plan names",
        "fog_far": ("fog far >= {need:.0f} m ({spans:g} x the plan span; the starter's shell fog is scaled to it: keep "
                    "`scene.fog = shell.fog`, or lengthen yours) and let the horizon ridge / outskirts close the world"),
        "underdressed": ("the layout's dressing counts are binding: add the missing props and the instanced ground cover "
                         "(tufts/pebbles count via InstancedMesh.count)"),
        "ring": ("vary them — at least 3 silhouettes, scale 0.6-1.6x, radius +-25 %, random yaw, clusters rather than a "
                 "ring — or drop the ring and let the starter's worldShell ridge and makeOutskirts hills close the horizon"),
        "place": "import its builder / clone its GLB",
    },
    "scene_blender": {
        "seat": "seat it with ctx.height_at(x, y) or on the surface it stands on",
        "free": '{name}["placement"] = "free"',
        "no_fog_msg": "the scene has no atmosphere: no world Volume, bounded fog volume or mist — frames read as thin_atmosphere",
        "no_fog": ("in build_env link a Volume Scatter / Principled Volume to the World Output's Volume (density "
                   "0.002-0.02, tinted like the sky horizon) or place a bounded fog volume over the play area"),
        "no_background_msg": "the world has no sky: no Sky Texture or Background colour on the world output",
        "no_background": "in build_env give scene.world a Sky Texture (or the plan's sky colour on its Background node)",
        "fog_far": ("thin the fog so 1/density >= {need:.0f} m ({spans:g} x the plan span) and let the horizon ridge / "
                    "outskirts close the world"),
        "underdressed": ("the layout's dressing counts are binding: add the missing props and the instanced ground cover "
                         "(every collection instance / geometry-nodes instance counts)"),
        "ring": ("vary them — at least 3 silhouettes, scale 0.6-1.6x, radius +-25 %, random yaw, clusters rather than a "
                 "ring — or drop the ring and close the horizon with terrain ridges and hills"),
        "place": "place a collection-instance Empty of ctx.assets['<snake>']",
    },
}


def placement_words(language: str | None) -> dict[str, str]:
    return LANGUAGE_WORDS.get(str(language or "scene_threejs"), LANGUAGE_WORDS["scene_threejs"])


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
    words, seated = _words(row.name), _seated_words()
    if words & (seated.buried_ok | seated.slope_ok):
        return None
    frac = row.sunk_m / row.height if row.height > 1e-6 else 1.0
    if words & seated.partial_ok:
        if frac <= seated.partial_ok_frac:
            return None
        return Severity.ERROR if row.sunk_m > SUNK_ERROR_M else Severity.WARN
    if row.sunk_m > SUNK_ERROR_M and frac > SUNK_ERROR_FRAC:
        return Severity.ERROR
    return Severity.WARN if row.sunk_m > SUNK_M else None


def _asset_findings(rows: list[AssetRow], *, floating_m: float, ground_y: float | None,
                    words: dict[str, str]) -> list[GateFinding]:
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
        if row.attached:   # mounted on what it touches: the settle leaves it, so does the gate
            continue
        support = _support_label(row, ground_y)
        if gap > floating_m:
            floating.append((row, gap))
        else:
            out.append(_f(Severity.WARN, f"{q} is unsupported: hovers {gap:.2f} m above {support} and touches nothing", target=q,
                          hint=f"lower {q} by {gap:.2f} m onto {support}, or attach it to a neighbour; "
                               f"if it is meant to hang free set {words['free'].format(name=row.name)}",
                          kind="unsupported", gap_m=gap, zone=row.zone))
    many = len(floating) > FLOATING_ERROR_COUNT
    for row, gap in floating:
        q = row.qualified
        support = _support_label(row, ground_y)
        sev = Severity.ERROR if (many or gap > FLOATING_ERROR_M) else Severity.WARN
        out.append(_f(sev, f"{q} is floating {gap:.2f} m above {support} (touches nothing)", target=q,
                      hint=f"lower {q} by {gap:.2f} m onto {support} — {words['seat']}; "
                           f"a deliberately airborne thing gets {words['free'].format(name=row.name)}",
                      kind="floating", gap_m=gap, zone=row.zone))
    return out


def _pair_findings(pairs: list[Interpenetration]) -> list[GateFinding]:
    out: list[GateFinding] = []
    # one finding per PAIR OF NAMES, keeping the worst overlap: a zone that stamps four
    # TerracottaPlanters against one arbour used to yield four identical ERRORs
    # (measured on t36_santorini) and flood the refine prompt with one fact
    best: dict[tuple[str, str], Interpenetration] = {}
    for p in pairs:
        key = tuple(sorted((f"{p.zone_a}/{p.a}", f"{p.zone_b}/{p.b}")))
        if key not in best or p.aabb_overlap > best[key].aabb_overlap:
            best[key] = p
    for p in best.values():
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


def placement_findings(table: dict[str, Any] | PlacementTable, *, indoor: bool = False,
                       language: str | None = None) -> GateReport:
    """The ``scene_placement`` GateReport for one census placement table (pure)."""
    t = table if isinstance(table, PlacementTable) else PlacementTable.model_validate(table or {})
    if t.error:
        return GateReport.of(GATE, [
            _f(Severity.WARN, f"placement probe failed: {t.error[:300]}", target="scene", kind="probe_failed",
               hint="harness instrumentation, not your code; the placement check was skipped this round")])
    floating_m = FLOATING_INDOOR_M if indoor else FLOATING_OUTDOOR_M
    rows = [r for r in t.assets if not r.exempt]
    findings = _cap_per_kind(_asset_findings(rows, floating_m=floating_m, ground_y=t.ground_y, words=placement_words(language))
                             + _pair_findings(t.interpenetrations))
    counts = {k: sum(1 for f in findings if f.data.get("kind") == k and f.severity != Severity.INFO)
              for k in ("floating", "sunken", "unsupported", "interpenetration")}
    n_exempt = sum(t.exempt.values())
    summary = (f"{t.checked} assets checked ({t.total} placed, {n_exempt} exempt): {counts['floating']} floating, "
               f"{counts['sunken']} sunken, {counts['unsupported']} unsupported, {counts['interpenetration']} interpenetrating"
               + (f"; {'indoor' if indoor else 'outdoor'} tolerance {floating_m * 100:.0f} cm")
               + ("; table truncated (first 400 assets / time budget)" if t.truncated else ""))
    findings.insert(0, _f(Severity.INFO, summary, target="scene", kind="summary", counts=counts, checked=t.checked, total=t.total,
                          exempt=t.exempt, truncated=t.truncated))
    return GateReport.of(GATE, findings)


# --------------------------------------------------------------------------- plan-aware checks
#: measured 2026-08-30 across the 48 scored runs of the four scene batteries: the top
#: standing defects are exactly the ones nothing measured — thin_atmosphere 32/48 and
#: undressed_scene 31/48 while the census already records ``fog`` / ``background`` and
#: every placed asset's name, zone and bbox.  These checks are pure functions of the
#: census + the plan; their ERRORs route into refine with the zone as the target.
SCALE_WARN, SCALE_ERROR = 2.5, 4.0
#: linear fog must reach past the plan's far side (fog far >= this many plan spans); exp fog
#: must not dissolve it (1/density >= span / this)
FOG_FAR_MIN_SPANS, FOG_DENSITY_MAX_PER_SPAN = 2.0, 1.5
BOUNDS_MARGIN_MIN_M = 2.0
#: a zone whose census instance count is under this fraction of its L2 layout budget is
#: underdressed.  Measured on scene_final_v1 (n=19): flat_ground 12 / undressed 11 /
#: monotonous 10 were the top standing defects while every zone had a layout with binding
#: mid/small/ground-cover counts nobody enforced.  Half is deliberately generous — the
#: budget mixes props with instanced tufts, and only a gross shortfall should gate.
DENSITY_FRACTION = 0.5
DENSITY_MIN_BUDGET = 20
#: an outdoor scene with no geometry reaching past this multiple of the bounds
#: half-extent has no backdrop ring — the world edge shows from every overview camera.
#: world_edge_visible stood in 11/19 scene_final_v1 verdicts and 3/6 of the density
#: arm while the plan's env contract demands a silhouette ring at ~0.6 x fog-far
#: (well beyond bounds); 1.25x the half-extent is a deliberately lenient floor.
BACKDROP_REACH_FACTOR = 1.25
#: stamped ring (host_census `stamps`): copies, where the ring sits relative to the plan's
#: half-extent, and how even / alike it must be before it is called stamped
RING_MIN_COPIES = 8
RING_BACKDROP_FRAC = 0.6
RING_RADIUS_CV_MAX = 0.08
RING_GAP_CV_MAX = 0.25
RING_SIZE_CV_MAX = 0.25         # loop 24's ski station: 32 peaks, radius spread 0.8 %, SIZE spread 15 % — still "identical pyramids stamped in a ring" to the judge
BACKDROP_MIN_HEIGHT_M = 2.0


def _plan_zones(plan: Any) -> list[tuple[str, list[str]]]:
    zones = (plan.get("zones") if isinstance(plan, dict) else getattr(plan, "zones", None)) or []
    out = []
    for z in zones:
        name = z.get("name") if isinstance(z, dict) else getattr(z, "name", "")
        contents = (z.get("contents") if isinstance(z, dict) else getattr(z, "contents", None)) or []
        if name:
            out.append((str(name), [str(c) for c in contents]))
    return out


def _plan_asset_sizes(plan: Any) -> dict[str, float]:
    assets = (plan.get("assets") if isinstance(plan, dict) else getattr(plan, "assets", None)) or []
    out: dict[str, float] = {}
    for a in assets:
        name = a.get("name") if isinstance(a, dict) else getattr(a, "name", "")
        size = (a.get("approx_size_m") if isinstance(a, dict) else getattr(a, "approx_size_m", None)) or ()
        try:
            m = max(float(v) for v in size)
        except (TypeError, ValueError):
            continue
        if name and m > 0.05:
            out[to_snake(name)] = m
    return out


def _plan_bounds(plan: Any) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
    b = plan.get("bounds") if isinstance(plan, dict) else getattr(plan, "bounds", None)
    if b is None:
        return None
    try:
        if isinstance(b, dict):
            c, e = b.get("center"), b.get("extents")
            lo = tuple(float(c[i]) - float(e[i]) / 2 for i in range(3))
            hi = tuple(float(c[i]) + float(e[i]) / 2 for i in range(3))
            return lo, hi
        return tuple(map(float, b.min)), tuple(map(float, b.max))
    except (TypeError, ValueError, IndexError, AttributeError):
        return None


def _layout_budget(layout: Any) -> int:
    """Total things the L2 layout put in the zone: placements + dressing counts."""
    if layout is None:
        return 0
    get = layout.get if isinstance(layout, dict) else lambda k, d=0: getattr(layout, k, d)
    placements = get("placements", []) or []
    n = 0
    for pl in placements:
        n += int((pl.get("count", 1) if isinstance(pl, dict) else getattr(pl, "count", 1)) or 1)
    for k in ("mid_props", "small_props", "ground_cover"):
        n += int(get(k, 0) or 0)
    return n


def _row_names(row: AssetRow) -> str:
    """The row's own name plus its named descendants, snake-cased for loose matching.

    A zone that wraps its content in one group ("IslandAssembly") used to hide every
    planned asset from this check: `eval/bench/out/scene_fixed` (2026-09-05) reported three
    floating_islands zones as "missing planned contents: FloatingRock, Windmill, SkyPine"
    while the zone module built each one and named it exactly that, one level down.
    """
    return " ".join(to_snake(n) for n in [row.name, *row.inner])


def contract_findings(census: dict[str, Any] | None, plan: Any,
                      layouts: dict[str, Any] | None = None, unavailable: Sequence[str] = ()) -> list[GateFinding]:
    """Deterministic plan-vs-census checks: env atmosphere present, every zone dressed
    with its planned contents, plausible scale, content inside the world bounds — and,
    when the zone has an L2 layout, its density budget actually met.

    ``unavailable`` = assets the asset stage could not build (the zones were told
    "NOT AVAILABLE — do not reference"): a zone is not missing what it was told not to
    place.  Measured 2026-09-07 (ab_temple_hero): the ERROR "missing planned contents:
    BronzeCenser" recurred every round for a hero that never existed, with the hint
    "clone its GLB"."""
    if not isinstance(census, dict) or plan is None:
        return []
    words = placement_words(census.get("language"))
    out: list[GateFinding] = []
    # -- density: the layout's counts are binding, and the census counts every instance
    groups = {to_snake(g.get("name", "")): g for g in (census.get("groups") or [])
              if isinstance(g, dict)}
    for zone_name, layout in (layouts or {}).items():
        budget = _layout_budget(layout)
        if budget < DENSITY_MIN_BUDGET:
            continue
        g = groups.get(to_snake(zone_name))
        if g is None:
            continue   # zone_empty below covers a missing group
        have = int(g.get("instances") or 0)
        if have < DENSITY_FRACTION * budget:
            out.append(_f(Severity.ERROR,
                          f"zone {zone_name} holds ~{have} instances but its layout budgeted {budget} "
                          f"(placements + mid/small props + ground cover)",
                          target=zone_name, kind="underdressed", have=have, budget=budget,
                          hint=words["underdressed"]))
    # -- atmosphere: the two env facts the census measures on every boot
    if "fog" in census and census.get("fog") is None:
        out.append(_f(Severity.ERROR, words["no_fog_msg"], target="env", kind="no_fog", hint=words["no_fog"]))
    if "background" in census and census.get("background") is None:
        out.append(_f(Severity.ERROR, words["no_background_msg"], target="env", kind="no_background",
                      hint=words["no_background"]))
    # -- fog that ends inside the world: measured 2026-09-08 over eleven exterior runs, every
    # one with fog far >= 2 x the plan span scored >= 0.60 and the three at 1.4-1.6 x scored
    # 0.42-0.60 with "no aerial perspective", "world edge", "backdrop floating in the sky" —
    # past fog far the land is the background colour while the unfogged sky stays sharp
    fog = census.get("fog")
    pb = _plan_bounds(plan)
    if isinstance(fog, dict) and pb and not is_interior(plan):
        span = max(pb[1][0] - pb[0][0], pb[1][2] - pb[0][2])
        far, density = fog.get("far"), fog.get("density")
        short = ""
        if isinstance(far, int | float) and span > 0 and far < FOG_FAR_MIN_SPANS * span:
            short = f"fog far {far:g} m ends inside the plan's {span:g} m world ({far / span:.1f}x)"
        elif isinstance(density, int | float) and span > 0 and density > FOG_DENSITY_MAX_PER_SPAN / span:
            short = f"fog density {density:g} dissolves the plan's {span:g} m world within {1 / density:.0f} m"
        if short:
            out.append(_f(Severity.WARN, short + " — past it the land is the background colour while the unfogged sky "
                          "stays sharp: a world edge and backdrops floating in the sky",
                          target="env", kind="fog_short", fog=fog, plan_span_m=round(span, 1),
                          hint=words["fog_far"].format(need=FOG_FAR_MIN_SPANS * span, spans=FOG_FAR_MIN_SPANS)))
    # -- a stamped ring: >= 8 same-size copies evenly on a circle round the world's edge.
    # Five of six exteriors on 2026-09-09 drew their horizon as "a ring of identical cones
    # stamped round the perimeter" / "rocks in a perfect circle" and the judge called each a
    # toy backdrop, twice.  The census measures the ring (host_census `stamps`); a rotunda's
    # columns or chairs round a table sit INSIDE the content and are not it.
    bounds = _plan_bounds(plan)
    if bounds and isinstance(census.get("groups"), list):
        lo, hi = bounds
        half = max(hi[0] - lo[0], hi[2] - lo[2]) / 2
        for g in census["groups"]:
            for st in (g.get("stamps") if isinstance(g, dict) else None) or []:
                if not isinstance(st, dict):
                    continue
                n, r = int(st.get("n") or 0), float(st.get("radius_m") or 0)
                rcv, gcv, scv = (float(st.get(k) or 0) for k in ("radius_cv", "gap_cv", "size_cv"))
                if (n >= RING_MIN_COPIES and half > 0 and r >= RING_BACKDROP_FRAC * half
                        and rcv <= RING_RADIUS_CV_MAX and gcv <= RING_GAP_CV_MAX and scv <= RING_SIZE_CV_MAX):
                    where = f"{g.get('name', '?')}/{st.get('name', '?')}"
                    out.append(_f(Severity.WARN,
                                  f"{where}: {n} near-identical copies stamped evenly on a {r:.0f} m ring round the world "
                                  f"(radius spread {rcv:.0%}, spacing spread {gcv:.0%}, size spread {scv:.0%}) — a toy backdrop",
                                  target=str(g.get("name", "overall")), kind="stamped_ring", n=n, radius_m=r,
                                  radius_cv=rcv, gap_cv=gcv, size_cv=scv,
                                  hint=words["ring"]))
    # -- backdrop ring: outdoor worlds must have geometry past the play area
    groups = census.get("groups")
    if bounds and isinstance(groups, list) and not is_interior(plan):
        lo, hi = bounds
        cx, cz = (lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2
        half = max(hi[0] - lo[0], hi[2] - lo[2]) / 2
        need = BACKDROP_REACH_FACTOR * half
        reach = 0.0
        for g in groups:
            b = g.get("bbox") if isinstance(g, dict) else None
            bmin, bmax = (_nums(b.get("min")), _nums(b.get("max"))) if isinstance(b, dict) else (None, None)
            if bmin is None or bmax is None or g.get("kind") not in ("content", "ground"):
                continue
            if (bmax[1] - bmin[1]) < BACKDROP_MIN_HEIGHT_M:
                continue
            r = max(abs(bmin[0] - cx), abs(bmax[0] - cx), abs(bmin[2] - cz), abs(bmax[2] - cz))
            reach = max(reach, r)
        if 0 < reach < need:
            out.append(_f(Severity.ERROR,
                          f"no backdrop ring: the farthest standing geometry reaches {reach:.0f} m from centre "
                          f"but the world edge hides only past ~{need:.0f} m",
                          target="env", kind="no_backdrop", reach_m=round(reach, 1), need_m=round(need, 1),
                          hint="build the env plan's silhouette ring (24-40 SOLID pieces — hills / treeline / "
                               "rooftops — at ~0.6 x fog-far radius, 3-8 m tall, darkened): the fog supplies the "
                               "haze, the ring hides the edge"))
    table = census.get("placement") if isinstance(census.get("placement"), dict) else None
    rows = [AssetRow.model_validate(r) for r in (table.get("assets") or [])] if table else []
    if not rows:
        return out
    # -- every zone dressed with what the plan put there (names matched loosely on words)
    by_zone: dict[str, list[AssetRow]] = {}
    for r in rows:
        by_zone.setdefault(to_snake(r.zone), []).append(r)
    all_names = " ".join(_row_names(r) for r in rows)
    gone = {to_snake(u) for u in unavailable}
    for zone_name, contents in _plan_zones(plan):
        zk = to_snake(zone_name)
        placed = by_zone.get(zk, [])
        # what the zone was allowed to place: an asset the asset stage never built was
        # announced "NOT AVAILABLE" to it, for zone_empty exactly as for missing_content
        expected = [c for c in contents if to_snake(c) not in gone]
        if not placed and expected:
            out.append(_f(Severity.ERROR, f"zone {zone_name} placed nothing (plan lists: {', '.join(expected[:6])})",
                          target=zone_name, kind="zone_empty",
                          hint=f"build the zone group named '{zone_name}' and place its planned contents"))
            continue
        zone_names = " ".join(_row_names(r) for r in placed)
        missing = [c for c in expected if to_snake(c) not in zone_names and to_snake(c) not in all_names]
        if missing:
            out.append(_f(Severity.ERROR,
                          f"zone {zone_name} is missing planned contents: {', '.join(missing[:5])}"
                          + (f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""),
                          target=zone_name, kind="missing_content", missing=missing[:8],
                          hint=f"place each listed asset ({words['place']}) inside this "
                               "zone's bbox AND give the object the plan's name for it — every gate and the "
                               "judge find it by that name, so a lantern called 'LanternPost1' reads as absent"))
    # -- plausible scale vs the plan's approx_size_m
    sizes = _plan_asset_sizes(plan)
    for r in rows:
        size = _nums((r.bbox or {}).get("size"))
        if r.exempt or size is None:
            continue
        rk = to_snake(r.name)
        key = next((k for k in sizes if k in rk), "")
        if not key:
            continue
        # a wrapper of instances is measured as ONE instance: the plan sized the fence panel, the
        # row is the whole run (host_placement `families`)
        n, measured = r.instance_size(key)
        what = f"each of the {n} {key} instances in {r.qualified}" if n else r.qualified
        if not n:
            measured = max(size)
        f = measured / sizes[key]
        if f > SCALE_ERROR or f < 1 / SCALE_ERROR:
            sev = Severity.ERROR
        elif f > SCALE_WARN or f < 1 / SCALE_WARN:
            sev = Severity.WARN
        else:
            continue
        out.append(_f(sev, f"{what} measures {measured:.2f} m but the plan sized {key} at ~{sizes[key]:.2f} m ({f:.1f}x)",
                      target=r.qualified, kind="scale", factor=round(f, 2), zone=r.zone, instances=n,
                      hint=f"scale {'each ' + key if n else r.name} so its largest dimension is ~{sizes[key]:.2f} m as planned"))
    # -- content inside the world bounds
    bounds = _plan_bounds(plan)
    if bounds:
        lo, hi = bounds
        margin = max(BOUNDS_MARGIN_MIN_M, 0.05 * max(hi[0] - lo[0], hi[2] - lo[2]))
        for r in rows:
            bmin, bmax = _nums((r.bbox or {}).get("min")), _nums((r.bbox or {}).get("max"))
            if r.exempt or bmin is None or bmax is None:
                continue
            fully_out = (bmin[0] > hi[0] + margin or bmax[0] < lo[0] - margin
                         or bmin[2] > hi[2] + margin or bmax[2] < lo[2] - margin)
            if fully_out:
                out.append(_f(Severity.ERROR, f"{r.qualified} sits entirely outside the plan bounds "
                                              f"(x {bmin[0]:.0f}..{bmax[0]:.0f}, z {bmin[2]:.0f}..{bmax[2]:.0f})",
                              target=r.qualified, kind="out_of_bounds", zone=r.zone,
                              hint="move it inside the plan bounds or into its zone bbox"))
    return out


def placement_census(ws: Workspace, probe: Callable[..., SceneProbeResult], *, force_probe: bool = False,
                     timeout_s: float = 60.0) -> dict[str, Any]:
    """The census dict carrying ``placement``: the last build's ``artifacts/census.json``,
    or a fresh ``probe`` (the language's ``SceneRuntime.probe``) when it is missing / predates
    the table / ``force_probe``."""
    census = None if force_probe else read_json_or_none(ws.artifacts / "census.json")
    if isinstance(census, dict) and isinstance(census.get("placement"), dict):
        return census
    res = probe(ws, timeout_s=timeout_s)
    if res.errors:
        return {"placement": {"error": res.errors[0]}}
    census = res.census or {}
    if not isinstance(census.get("placement"), dict):
        census["placement"] = {"error": "the scene did not boot, so nothing was placed" if not census else "probe driver returned no placement table"}
    return census


def setting_text(plan: Any) -> str:
    """The plan text the indoor/outdoor rule reads (``setting`` / ``environment`` /
    ``title``), from a plan dict or a ScenePlan; ``""`` for anything else."""
    if isinstance(plan, dict):
        return " ".join(str(plan.get(k) or "") for k in ("setting", "environment", "title"))
    return " ".join(str(getattr(plan, k, "") or "") for k in ("setting", "environment", "title"))


def placement_gate(ws: Workspace, census: dict[str, Any] | None, plan: Any) -> GateReport | None:
    """THE ``scene_placement`` verdict: the round's gate (``ScenePipeline.gates``) and the
    ``check_placement`` tool both return it, on the same inputs — the census, the plan and
    what the run's stages recorded in the workspace (the L2 zone layouts, the assets the
    asset stage could not build).  Until 2026-09-22 the tool reported
    :func:`placement_findings` alone, so it never showed the plan checks (fog, backdrop,
    zone contents, scale, bounds, density) the round then failed on."""
    assets = _stage_result(ws, "assets")
    unavailable = [name for name, r in assets.items() if isinstance(r, dict) and not r.get("ok", True)]
    return placement_gate_safe(census, plan=plan, layouts=_stage_result(ws, "layouts"), unavailable=unavailable)


def _stage_result(ws: Workspace, name: str) -> dict[str, Any]:
    """A cached stage's result (``orchestrator.StageRunner`` writes ``stages/<name>.json``), ``{}`` when absent."""
    res = (read_json_or_none(ws.stages / f"{name}.json") or {}).get("result")
    return res if isinstance(res, dict) else {}


def placement_gate_safe(census: dict[str, Any] | None, *, plan: Any = None,
                        layouts: dict[str, Any] | None = None, unavailable: Sequence[str] = ()) -> GateReport | None:
    """Round-gate entry: ``None`` when the census has no placement table (scene did not
    boot, or an older driver), a WARN-only report when anything raises — never an
    exception, so the placement check cannot kill a round."""
    try:
        table = (census or {}).get("placement")
        if not isinstance(table, dict):
            return None
        report = placement_findings(table, indoor=is_interior(plan), language=(census or {}).get("language"))
        extra = _cap_per_kind(contract_findings(census, plan, layouts=layouts, unavailable=unavailable))
        if extra:
            report = GateReport.of(GATE, report.findings + extra, duration_ms=report.duration_ms)
        return report
    except Exception as e:  # noqa: BLE001 — advisory instrumentation must not fail the round
        log.warning("scene placement gate failed: %s", e)
        return GateReport.of(GATE, [
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
