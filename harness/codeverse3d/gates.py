"""Per-track gate assembly and render rig: what ``3dcode check`` runs and what ``3dcode judge-pack`` renders.

Everything here is deterministic measurement on the built artifacts; no model is called.
"""

from __future__ import annotations

import importlib
import logging
import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    Measurement,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ArticulatedPlan, GraphicsPlan, Plan, ScenePlan, StaticPlan
from codeverse3d.contracts.spec import PROJECT_GATE_PREFIX, ProjectCheck, Spec
from codeverse3d.conventions import (
    BBOX_TOLERANCE_M,
    MAX_TRIS_OBJECT,
    OBJECT_CLAY_VIEWS,
    OBJECT_VIEWS,
    SCENE_TIMES,
    to_pascal,
    to_snake,
)
from codeverse3d.languages import get_runtime
from codeverse3d.languages._gl_common import METRICS_NAME, SHEET_NAME, read_metrics
from codeverse3d.spatial.contract import check_contract, check_fixed_parts, planned_joins
from codeverse3d.spatial.frame_metrics import frame_gate_from_renders
from codeverse3d.spatial.frame_motion import motion_text_for
from codeverse3d.spatial.joints_sweep import SWEEP_GATE, sweep_gate
from codeverse3d.spatial.measure import GlbLoadError, measure_glb
from codeverse3d.spatial.render import RenderError, render_glb
from codeverse3d.spatial.scene_placement import placement_gate_safe
from codeverse3d.spatial.silhouette import compare_silhouette, measure_reference
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

OBJECT_TRACKS = (Track.STATIC_OBJECT, Track.ARTICULATED_OBJECT)


# ===================================================================== detail budget
#: triangles a well-detailed single part is worth.  Corpus: median tri/part ≈ 430, and the
#: highest-scoring hard run (garden arch, 0.929) sits at 1936.  Target the good end.
TRIS_PER_UNIT_TARGET = 2_000
TRIS_PER_UNIT_FLOOR = 350
TRIS_PER_UNIT_MAX = 20_000

BUILD_S_PER_UNIT = 3.0
MIN_BUILD_S = 20
BUDGET_GATE = "detail_budget"


@dataclass(frozen=True)
class DepthBudget:
    """What THIS object is allowed to spend, sized from its own plan."""

    n_units: int
    min_tris: int
    target_tris: int
    max_tris: int
    max_build_s: int


def _clamp(v: float, lo: float, hi: float) -> int:
    return int(max(lo, min(hi, v)))


def depth_budget(plan: StaticPlan, build_timeout_s: int) -> DepthBudget:
    """Triangle + build-time budget sized from the plan's part/instance count."""
    n_units = plan.leaf_count
    target = _clamp(TRIS_PER_UNIT_TARGET * n_units, 6_000, 200_000)
    return DepthBudget(
        n_units=n_units,
        min_tris=_clamp(TRIS_PER_UNIT_FLOOR * n_units, 1_500, 40_000),
        target_tris=target,
        max_tris=_clamp(TRIS_PER_UNIT_MAX * n_units, 60_000, MAX_TRIS_OBJECT),
        # the subprocess is killed at build_timeout_s, so never allow more than
        # that minus the exporter's share, and never less than MIN_BUILD_S
        max_build_s=int(_clamp(int(BUILD_S_PER_UNIT * n_units), MIN_BUILD_S, max(MIN_BUILD_S, build_timeout_s - 30))),
    )


def budget_gate(measurement: Measurement, build: BuildResult, budget: DepthBudget) -> GateReport:
    """``detail_budget`` gate: is this object as dense as its own plan says it should be?"""
    findings: list[GateFinding] = []
    tris = int(measurement.tri_count or 0)
    per_unit = tris / max(1, budget.n_units)
    if tris > budget.max_tris:
        findings.append(GateFinding(
            gate=BUDGET_GATE, severity=Severity.ERROR, target="overall",
            message=f"{tris:,} triangles exceeds this object's ceiling of {budget.max_tris:,} "
                    f"({budget.n_units} built objects × {TRIS_PER_UNIT_MAX:,})",
            fix_hint=f"over this object's ceiling of {budget.max_tris:,}: reduce the segment / subdivision "
                     f"counts on the heaviest parts",
            data={"kind": "over_tri_budget", "tris": tris, "max": budget.max_tris}))
    elif tris < budget.min_tris:
        findings.append(GateFinding(
            gate=BUDGET_GATE, severity=Severity.WARN, target="overall",
            message=f"{tris:,} triangles ({per_unit:.0f} per built object) is below this object's detail floor "
                    f"of {budget.min_tris:,} — it is a stack of primitives, not a modelled object",
            fix_hint=f"{tris:,} triangles is under this object's floor of {budget.min_tris:,} (target "
                     f"{budget.target_tris:,}): add the detail the real object carries INSIDE the parts the "
                     f"plan names, not as new parts",
            data={"kind": "under_tri_budget", "tris": tris, "floor": budget.min_tris,
                  "target": budget.target_tris, "per_unit": round(per_unit, 1)}))
    ms = int(build.duration_ms or 0)
    if ms > budget.max_build_s * 1000:
        findings.append(GateFinding(
            gate=BUDGET_GATE, severity=Severity.WARN, target="overall",
            message=f"the build took {ms / 1000:.1f} s, over this object's {budget.max_build_s} s budget",
            fix_hint="find the part with the most segments/subdivision/booleans and simplify it",
            data={"kind": "slow_build", "ms": ms, "budget_s": budget.max_build_s}))
    if not findings:
        findings.append(GateFinding(
            gate=BUDGET_GATE, severity=Severity.INFO, target="overall",
            message=f"{tris:,} triangles ({per_unit:.0f} per built object) inside the "
                    f"{budget.min_tris:,}–{budget.max_tris:,} budget",
            data={"kind": "tri_budget_ok", "tris": tris, "target": budget.target_tris}))
    return GateReport.of(BUDGET_GATE, findings)


# ===================================================================== project checks
def _named(report: GateReport, check: ProjectCheck) -> GateReport:
    """``report`` under ``project:<name>`` (a check returning several reports keeps them apart as
    ``project:<name>.<its gate>``), its findings renamed with it, so caps see one namespace."""
    own = report.gate or ""
    if own.startswith(PROJECT_GATE_PREFIX):
        gate = own
    elif own in ("", check.name):
        gate = PROJECT_GATE_PREFIX + check.name
    else:
        gate = f"{PROJECT_GATE_PREFIX}{check.name}.{own}"
    findings = [f.model_copy(update={"gate": gate}) for f in report.findings]
    return report.model_copy(update={"gate": gate, "findings": findings})


def run_project_checks(spec: Spec, workspace: Path, glb: Path, measurement: Measurement | None,
                       round_index: int) -> list[GateReport]:
    """Every ``Spec.checks`` entry (``package.module:function``) on this GLB, in the spec's order.  A check that
    raises costs nothing but a WARN saying so: a project's bug must not fail or cap the verdict."""
    out: list[GateReport] = []
    for check in spec.checks:
        t0 = time.time()
        try:
            module, _, attr = check.entry.partition(":")
            fn = getattr(importlib.import_module(module), attr)
            got = fn(glb=glb, workspace=workspace, spec=spec, measurement=measurement, round_index=round_index,
                     args=dict(check.args))
            reports = [got] if isinstance(got, GateReport) else list(got)
            out += [_named(r, check) for r in reports]
        except Exception as e:  # noqa: BLE001 — a project's bug must not fail or cap the verdict
            log.warning("project check %s failed: %s", check.entry, e)
            gate = PROJECT_GATE_PREFIX + check.name
            out.append(GateReport.of(gate, [GateFinding(
                gate=gate, severity=Severity.WARN,
                message=f"project check {check.entry} raised {type(e).__name__}: {str(e)[:300]}")],
                duration_ms=int((time.time() - t0) * 1000)))
    return out


# ===================================================================== planned-motion gate
MOTION_GATE = "motion_direction"
#: its findings' ``data.kind``: the joint moves the way the plan says / the other way / not measured
MOTION_OK, MOTION_WRONG, MOTION_SKIPPED = "as_planned", "wrong_direction", "skipped"

#: phrase → direction key understood by ``spatial.joints_sweep.motion_direction_check``.
#: Longer phrases first so "pulls out" wins over "out"; explicit axes first of all.
_PHRASES: tuple[tuple[str, str], ...] = (
    ("-y", "-y"), ("+y", "+y"), ("-z", "-z"), ("+z", "+z"), ("-x", "-x"), ("+x", "+x"),
    ("towards the front", "front"), ("toward the front", "front"), ("to the front", "front"),
    ("opens to the front", "front"), ("opens out", "front"), ("pulls out", "front"), ("slides out", "front"),
    ("pull out", "front"), ("slide out", "front"), ("swings out", "front"), ("outward", "front"), ("forward", "front"),
    ("towards the viewer", "front"), ("toward the viewer", "front"), ("frontward", "front"),
    ("towards the back", "back"), ("toward the back", "back"), ("to the back", "back"), ("pushes in", "back"),
    ("slides in", "back"), ("backward", "back"), ("rearward", "back"), ("inward", "back"),
    ("upward", "up"), ("upwards", "up"), ("opens up", "up"), ("lifts", "up"), ("lift", "up"), ("raises", "up"),
    ("raise", "up"), ("rises", "up"),
    ("downward", "down"), ("downwards", "down"), ("lowers", "down"), ("drops", "down"),
    ("folds down", "down"), ("opens down", "down"),
    ("to the left", "left"), ("leftward", "left"), ("swings left", "left"),
    ("to the right", "right"), ("rightward", "right"), ("swings right", "right"),
    ("front", "front"), ("back", "back"), ("up", "up"), ("down", "down"), ("left", "left"), ("right", "right"),
    ("out", "front"),
)

_SKIP_MARKERS = ("not ", "n't", "either", "both", "around", "rotates about", "spins")
_DIR_GROUPS = {"front": "y", "back": "y", "-y": "y", "+y": "y", "up": "z", "down": "z", "+z": "z", "-z": "z",
               "left": "x", "right": "x", "-x": "x", "+x": "x"}


def _contains_word(padded: str, phrase: str) -> bool:
    """Whole-word containment (phrase boundaries are spaces or punctuation)."""
    i = padded.find(phrase)
    while i >= 0:
        before = padded[i - 1] if i > 0 else " "
        after = padded[i + len(phrase)] if i + len(phrase) < len(padded) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        i = padded.find(phrase, i + 1)
    return False


def expected_direction(motion: str) -> str | None:
    """Direction key for an obvious motion description, else ``None``."""
    text = " ".join((motion or "").lower().replace("_", " ").split())
    if not text or any(m in text for m in _SKIP_MARKERS):
        return None
    padded = f" {text} "
    hits: list[str] = []
    for phrase, key in _PHRASES:
        if _contains_word(padded, phrase):
            hits.append(key)
            if key in ("-y", "+y", "-z", "+z", "-x", "+x"):
                return key
    if not hits:
        return None
    axes = {_DIR_GROUPS[h] for h in hits}
    if len(axes) > 1:
        # "opens up and out" — up/front conflict: the longest phrase decides only if one axis dominates
        first_axis = _DIR_GROUPS[hits[0]]
        if any(_DIR_GROUPS[h] != first_axis for h in hits[1:]):
            return None
    return hits[0]


def axis_fix_hint(joint_name: str, axis: Sequence[float] | None, expected: str) -> str:
    if axis is not None and len(axis) == 3:
        neg = tuple(-float(a) for a in axis)
        return (f"in src/robot.urdf set joint '{joint_name}' <axis xyz=\"{neg[0]:g} {neg[1]:g} {neg[2]:g}\"/> "
                f"(negate the axis; keep lower/upper as they are, q=0 stays the rest pose) so positive motion goes {expected}")
    return f"negate the <axis> of joint '{joint_name}' in src/robot.urdf (keep lower/upper as they are) so motion goes {expected}"


def motion_checks(ws: Workspace, plan: Plan | None) -> GateReport | None:
    """``motion_direction``: the plan's motion text ("pulls out to the front") vs the built URDF's real direction,
    for every joint with an obvious direction; ``None`` when nothing is measurable."""
    joints = list(getattr(plan, "joints", None) or [])
    wanted = [(j, expected_direction(j.motion)) for j in joints]
    wanted = [(j, d) for j, d in wanted if d is not None]
    if not wanted:
        return None
    from codeverse3d.spatial.joints_model import UrdfError, load_urdf
    from codeverse3d.spatial.joints_sweep import find_urdf, motion_direction_check

    urdf = find_urdf(ws)
    if urdf is None:
        return None
    t0 = time.time()
    findings: list[GateFinding] = []
    try:
        robot = load_urdf(urdf, ws.artifacts / "meshes")
    except UrdfError as e:
        return GateReport.of(MOTION_GATE, [GateFinding(
            gate=MOTION_GATE, severity=Severity.WARN, target="robot.urdf", message=f"motion checks skipped: {e}",
            data={"kind": MOTION_SKIPPED})])
    urdf_names = {to_snake(n): n for n in getattr(robot, "joints", {})}
    for j, expected in wanted:
        name = urdf_names.get(to_snake(j.name), j.name)  # URDF joint named like the plan joint (any casing)
        try:
            chk = motion_direction_check(robot, name, expected)
        except UrdfError as e:
            findings.append(GateFinding(gate=MOTION_GATE, severity=Severity.WARN, target=j.name,
                                        message=f"motion check skipped: {e}", data={"kind": MOTION_SKIPPED, "expected": expected}))
            continue
        data = {"kind": MOTION_OK if chk.ok else MOTION_WRONG, "expected": expected, "observed_dir": list(chk.observed_dir),
                "motion": j.motion, "cos": chk.cos, "suggested_axis": list(chk.suggested_axis) if chk.suggested_axis else None}
        if chk.ok:
            findings.append(GateFinding(gate=MOTION_GATE, severity=Severity.INFO, target=j.name, message=chk.message, data=data))
        else:
            # anti-parallel: the sign is wrong, negate; orthogonal: the geometry admits an exact axis, state it.
            if chk.cos >= -0.5 and chk.suggested_axis is not None:
                sx, sy, sz = chk.suggested_axis
                hint = (f"in src/robot.urdf set joint '{j.name}' <axis xyz=\"{sx:g} {sy:g} {sz:g}\"/> "
                        f"— computed from the pivot so positive motion goes {expected}")
            else:
                hint = axis_fix_hint(j.name, getattr(j, "axis", None), expected)
            findings.append(GateFinding(
                gate=MOTION_GATE, severity=Severity.ERROR, target=j.name,
                message=f"{chk.message}. Plan says: \"{j.motion}\"",
                fix_hint=hint, data=data))
    return GateReport.of(MOTION_GATE, findings, duration_ms=int((time.time() - t0) * 1000))


# ===================================================================== reference silhouette
SILHOUETTE_GATE = "reference_silhouette"
IOU_REFINE_THRESHOLD = 0.6


def silhouette_gate(spec: Spec, renders: RenderSet) -> GateReport | None:
    """Reference-image projects: outline IoU vs the target image (a WARN below :data:`IOU_REFINE_THRESHOLD`)."""
    if not spec.references:
        return None
    try:
        m = measure_reference(renders.views, spec, compare=compare_silhouette)
    except Exception as e:  # noqa: BLE001 — advisory measurement; never fails the pack
        log.warning("compare_silhouette failed: %s", e)
        return GateReport.of(SILHOUETTE_GATE, [GateFinding(
            gate=SILHOUETTE_GATE, severity=Severity.INFO, target="overall", message=f"silhouette comparison unavailable: {e}")])
    if m is None:
        return None
    low = m.scorable and m.iou < IOU_REFINE_THRESHOLD
    msg = (f"outline IoU vs reference = {m.iou:.3f} (best-matching view: {m.view})"
           + ("" if m.reliable else " (unreliable mask)")
           + (" (the reference's proportions contradict the brief: not a shape verdict)" if m.conflict else "")
           + (f" — below {IOU_REFINE_THRESHOLD:.1f}" if low else ""))
    finding = GateFinding(gate=SILHOUETTE_GATE, severity=Severity.WARN if low else Severity.INFO, target="overall",
                          message=msg, data=m.model_dump(exclude={"per_view"}),
                          fix_hint=("match the reference outline: compare proportions (aspect ratio), overall extents and the "
                                    "silhouette of each major part against the reference image" if low else ""))
    return GateReport.of(SILHOUETTE_GATE, [finding])


# ===================================================================== scene asset modules
ASSET_GATE = "scene_assets"
#: max triangles for ONE asset instance (the scene contract's budget)
ASSET_MAX_TRIS = 15_000
#: probe zone of a bpy asset check: one collection instance at the origin, so the census GLB carries exactly the asset's geometry
BPY_CHECK_ZONE = "check"


class AssetCheck(BaseModel):
    """Result of importing an asset module and calling its builder."""

    ok: bool = False
    ran: bool = Field(default=False, description="False = the checker itself could not run (node missing)")
    fatal: bool = Field(default=False, description="the module does not import / build at all — zones must not use it")
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    size_m: tuple[float, float, float] | None = None
    min_y: float | None = None
    tris: int = Field(default=0, description="triangles the asset built itself (the per-asset budget)")
    lib_tris: int = Field(default=0, description="triangles the effect-library factories it composes built")
    meshes: int = 0
    materials: int = 0


def _soft_findings(chk: AssetCheck, expected: tuple[float, float, float] | None, *, max_tris: int = ASSET_MAX_TRIS) -> None:
    """Findings that make the asset WRONG but not broken."""
    if not chk.ok:
        return
    if chk.min_y is not None and chk.min_y < -0.02:
        chk.errors.append(f"the group sinks {abs(chk.min_y):.2f} m below y=0 — its lowest point must sit at y=0")
    if chk.tris > max_tris:
        chk.errors.append(f"{chk.tris} triangles exceeds the {max_tris} budget per asset — lower the segment counts")
    if chk.tris + chk.lib_tris > MAX_TRIS_OBJECT:
        chk.errors.append(f"the effect-library factories it calls build {chk.lib_tris} triangles, past the {MAX_TRIS_OBJECT} "
                          "ceiling for one object — pass their lighter options (fewer leaves, fewer leaf segments) or call fewer")
    if chk.meshes == 1 and chk.materials <= 1 and chk.tris + chk.lib_tris < 200:
        chk.errors.append("the asset is still a single low-poly box (one mesh, one material): build it from several "
                          "parts with distinct materials so it reads as the described object")
    if expected and chk.size_m:
        for axis, got, want in zip("whd", chk.size_m, expected, strict=False):
            if want > 0.05 and (got > want * 2.5 or got < want * 0.4):
                chk.errors.append(f"measured {axis}={got:.2f} m but the plan says {want:.2f} m — rescale to the planned size (±5 %)")
                break
    chk.ok = not chk.errors


def check_threejs_asset(ws: Workspace, rel: str, pascal: str, *, timeout_s: float = 60.0,
                        expected_size_m: tuple[float, float, float] | None = None) -> AssetCheck:
    """Import ``rel`` in node, call ``build<Pascal>(THREE, {})`` and inspect the group."""
    path = ws.root / rel
    if not path.is_file():
        return AssetCheck(ok=False, ran=True, fatal=True, errors=[f"{rel} was not written"])
    try:
        from codeverse3d.spatial.node import run_node, runtime_js_dir

        lifecycle = ws.src / "lib" / "lifecycle.js"
        args = [path.resolve().as_uri(), f"build{pascal}"] + ([lifecycle.resolve().as_uri()] if lifecycle.is_file() else [])
        res = run_node(runtime_js_dir() / "lib" / "asset_check.mjs", args, cwd=ws.root,
                       three_hook=True, timeout_s=timeout_s, check=False)
        data = res.last_json
        if data is None:
            tail = (res.stderr or res.stdout or "").strip().splitlines()[-3:]
            return AssetCheck(ok=False, ran=True, fatal=True, errors=[f"the module failed to load: {' / '.join(tail)[:300]}"])
        chk = AssetCheck(ok=bool(data.get("ok")), ran=True, errors=list(data.get("errors") or []),
                         warnings=list(data.get("warnings") or []),
                         size_m=tuple(data["size_m"]) if data.get("size_m") else None,  # type: ignore[arg-type]
                         min_y=data.get("min_y"), tris=int(data.get("tris") or 0), lib_tris=int(data.get("lib_tris") or 0),
                         meshes=int(data.get("meshes") or 0), materials=int(data.get("materials") or 0))
    except Exception as e:  # noqa: BLE001 — the checker is an optimisation, never a blocker
        log.warning("asset check could not run for %s: %s", rel, e)
        return AssetCheck(ok=True, ran=False, warnings=[f"checker unavailable: {type(e).__name__}"])
    chk.fatal = not chk.ok  # everything the JS side reports is a hard failure to import/build
    _soft_findings(chk, expected_size_m)   # …the rest is wrong-but-usable
    return chk


def _bpy_check_workspace(ws: Workspace, rel: str, snake: str) -> Workspace:
    from codeverse3d.contracts.common import ENTRY_FILE
    from codeverse3d.languages.scene_blender import render_scene_py

    scratch = Workspace(ws.artifacts / "asset_check" / snake)
    if scratch.root.exists():
        shutil.rmtree(scratch.root)
    for sub in ("assets", "zones"):
        (scratch.src / sub).mkdir(parents=True, exist_ok=True)
    scratch.artifacts.mkdir(parents=True, exist_ok=True)
    (scratch.root / ENTRY_FILE[Language.SCENE_BLENDER]).write_text(render_scene_py([], [snake], [], []))
    shutil.copyfile(ws.root / rel, scratch.src / "assets" / f"{snake}.py")
    (scratch.src / "zones" / f"{BPY_CHECK_ZONE}.py").write_text(
        f'"""The probe zone: one instance of {snake}, so the census GLB measures it."""\n'
        "import bpy\n\n\n"
        "def build(ctx):\n"
        f"    ob = bpy.data.objects.new({to_pascal(snake)!r}, None)\n"
        '    ob.instance_type = "COLLECTION"\n'
        f"    ob.instance_collection = ctx.assets[{snake!r}]\n"
        "    ctx.collection.objects.link(ob)\n"
        "    return ctx.collection\n")
    return scratch


def check_bpy_asset(ws: Workspace, runtime: Any, rel: str, snake: str, *,
                    expected_size_m: tuple[float, float, float] | None = None, timeout_s: float | None = None) -> AssetCheck:
    """Build ``src/assets/<snake>.py`` alone in Blender and inspect what it made."""
    path = ws.root / rel
    if not path.is_file():
        return AssetCheck(ok=False, ran=True, fatal=True, errors=[f"{rel} was not written"])
    try:
        scratch = _bpy_check_workspace(ws, rel, snake)
        run = runtime.run_wrapper(scratch, scratch.artifacts, zones={BPY_CHECK_ZONE: to_pascal(BPY_CHECK_ZONE)}, blend=False,
                                  timeout_s=float(timeout_s or get_settings().limits.build_timeout_s))
    except Exception as e:  # noqa: BLE001 — the checker is an optimisation, never a blocker
        log.warning("bpy asset check could not run for %s: %s", rel, e)
        return AssetCheck(ok=True, ran=False, warnings=[f"checker unavailable: {type(e).__name__}"])
    report = run.report or {}
    for e in report.get("errors") or []:
        if e.get("stage") == "asset" and e.get("name") == snake:
            where = f"{e.get('error_file') or rel}:{e.get('error_line')}" if e.get("error_line") else str(e.get("error_file") or rel)
            return AssetCheck(ok=False, ran=True, fatal=True,
                              errors=[f"{where}: {e.get('error_type')}: {str(e.get('error_message'))[:300]}"])
    if run.result.harness_failure:
        log.warning("bpy asset check could not finish for %s: %s", rel, run.result.error_type)
        return AssetCheck(ok=True, ran=False, warnings=[f"checker unavailable: {run.result.error_type}"])
    if report.get("nonfinite"):
        return AssetCheck(ok=False, ran=True, fatal=True,
                          errors=[f"{report.get('error_file') or rel}: {report.get('error_type')}: "
                                  f"{str(report.get('error_message'))[:600]}"])
    if not run.result.ok:
        return AssetCheck(ok=False, ran=True, fatal=True,
                          errors=[f"the module's build did not finish: {run.result.error_type}: {run.result.error_message}"[:300]])
    if any(e.get("stage") == "zone" for e in report.get("errors") or []):
        return AssetCheck(ok=False, ran=True, fatal=True,
                          errors=[f"build_{snake}(ctx) returned no Collection the scene could instance (ctx.assets[{snake!r}] is missing)"])
    chk = AssetCheck(ok=True, ran=True, tris=int((run.facts or {}).get("unique_tris") or 0))
    try:
        m = measure_glb(scratch.artifacts / "census.glb")
    except Exception as e:  # noqa: BLE001 — a module that built but cannot be measured is still usable
        chk.warnings.append(f"measure unavailable: {type(e).__name__}")
        return chk
    chk.size_m = (round(m.extents[0], 3), round(m.extents[1], 3), round(m.extents[2], 3))
    chk.min_y, chk.meshes, chk.materials = round(m.bbox_min[1], 3), m.n_meshes, m.materials
    chk.tris = chk.tris or m.tri_count
    _soft_findings(chk, expected_size_m)
    return chk


def asset_gate(ws: Workspace, runtime: Any, plan: ScenePlan, language: Language) -> GateReport | None:
    """``scene_assets``: every planned MODULE asset (``threejs`` / ``bpy``) imported and built alone — a fatal module
    is an ERROR, a wrong-but-usable one (sunk, over budget, a single box, off the planned size) a WARN."""
    modules = [a for a in plan.assets if a.kind in ("threejs", "bpy")]
    if not modules:
        return None
    t0 = time.time()
    findings: list[GateFinding] = []
    for a in modules:
        rel = runtime.asset_file(a)
        if a.kind == "threejs":
            chk = check_threejs_asset(ws, rel, to_pascal(a.name), expected_size_m=a.approx_size_m)
        else:
            chk = check_bpy_asset(ws, runtime, rel, to_snake(a.name), expected_size_m=a.approx_size_m)
        data = {"kind": "asset_fatal" if chk.fatal else "asset_soft" if chk.errors else "asset_ok", "file": rel,
                "tris": chk.tris, "size_m": list(chk.size_m) if chk.size_m else None, "ran": chk.ran}
        if not chk.ran:
            findings.append(GateFinding(gate=ASSET_GATE, severity=Severity.INFO, target=a.name,
                                        message=f"{rel}: not checked ({'; '.join(chk.warnings)[:200]})", data=data))
        elif chk.fatal:
            findings.append(GateFinding(gate=ASSET_GATE, severity=Severity.ERROR, target=a.name,
                                        message=f"{rel} does not build: " + "; ".join(chk.errors)[:400],
                                        fix_hint=f"fix {rel} so its builder imports and returns the asset; zones placing it get nothing until then",
                                        data=data))
        elif chk.errors:
            findings.append(GateFinding(gate=ASSET_GATE, severity=Severity.WARN, target=a.name,
                                        message=f"{rel}: " + "; ".join(chk.errors)[:400],
                                        fix_hint=f"rewrite {rel} so the deterministic asset check passes", data=data))
        else:
            size = f"{chk.size_m[0]:.2f}×{chk.size_m[1]:.2f}×{chk.size_m[2]:.2f} m" if chk.size_m else "size unmeasured"
            findings.append(GateFinding(gate=ASSET_GATE, severity=Severity.INFO, target=a.name,
                                        message=f"{rel}: builds, {chk.tris} tris, {size}", data=data))
    return GateReport.of(ASSET_GATE, findings, duration_ms=int((time.time() - t0) * 1000))


# ===================================================================== the check
class CheckResult(BaseModel):
    """What ``3dcode check`` found: the build, the measurement and every gate, written to ``artifacts/gates.json``."""

    ok: bool
    track: str
    language: str
    round_index: int = 0
    src_hash: str = ""
    build: BuildResult
    measurement: Measurement | None = None
    gates: list[GateReport] = Field(default_factory=list)

    @property
    def errors(self) -> list[tuple[str, GateFinding]]:
        return [(g.gate, f) for g in self.gates for f in g.errors]


def lint_and_build(ws: Workspace, language: Language, *, timeout_s: int | None = None) -> tuple[GateReport, BuildResult]:
    """Lint, then build when the lint has no ERROR (a lint error is recorded as the build status)."""
    rt = get_runtime(language)
    lint: GateReport = rt.lint(ws)
    if lint.errors:
        br = BuildResult(ok=False, language=language.value, error_type="LintError",
                         error_message="\n".join(f.as_line(with_target=True) for f in lint.errors)[:4000])
        ws.write_json(ws.artifacts / "build.json", br)
        return lint, br
    return lint, rt.build(ws, timeout_s=timeout_s or get_settings().limits.build_timeout_s)


def run_check(ws: Workspace, spec: Spec, plan: Plan | None, *, round_index: int = 0) -> CheckResult:
    """Lint → build → measure → every gate of the track; the result is also written to ``artifacts/gates.json``."""
    lint, build = lint_and_build(ws, spec.language)
    gates: list[GateReport] = [lint]
    measurement: Measurement | None = None
    if build.ok:
        if spec.track in OBJECT_TRACKS:
            measurement, more = object_gates(ws, spec, plan, build, round_index=round_index)
            gates += more
        elif spec.track is Track.SCENE:
            gates += scene_gates(ws, spec, plan, build)
    gates += build.gates
    result = CheckResult(ok=build.ok and not any(g.errors for g in gates), track=spec.track.value,
                         language=spec.language.value, round_index=round_index, src_hash=ws.src_hash(),
                         build=build, measurement=measurement, gates=gates)
    ws.write_json(ws.gates_path, result)
    d = ws.gates_dir(round_index)
    d.mkdir(parents=True, exist_ok=True)
    for g in gates:
        ws.write_json(d / f"{g.gate.replace(':', '_').replace('/', '_')}.json", g)
    return result


def object_gates(ws: Workspace, spec: Spec, plan: Plan | None, build: BuildResult, *,
                 round_index: int) -> tuple[Measurement | None, list[GateReport]]:
    """Measure the GLB, then connectivity, contract, detail budget, fixed parts, project checks; articulated adds
    the joint sweep and the motion-direction gate."""
    settings = get_settings()
    out: list[GateReport] = []
    glb = Path(build.glb_path) if build.glb_path else None
    if glb is None or not glb.is_file():
        return None, [GateReport.of("measure", [GateFinding(
            gate="measure", severity=Severity.ERROR, target="artifacts/object.glb",
            message="the build reported success but wrote no object.glb", fix_hint="build again; the exporter wrote nothing")])]
    try:
        measurement = measure_glb(glb)
    except GlbLoadError as e:
        return None, [GateReport.of("measure", [GateFinding(
            gate="measure", severity=Severity.ERROR, target="artifacts/object.glb", message=f"GLB unreadable: {e}")])]
    ws.write_json(ws.artifacts / "measurement.json", measurement)
    from codeverse3d.spatial.connectivity import check_connectivity

    object_plan = plan if isinstance(plan, StaticPlan) else None
    out.append(check_connectivity(glb, language=spec.language.value,
                                  planned_edges=tuple(planned_joins(object_plan, measurement)) if object_plan else ()))
    if object_plan is not None:
        out.append(check_contract(measurement, object_plan, language=spec.language.value, tol_m=BBOX_TOLERANCE_M))
        out.append(budget_gate(measurement, build, depth_budget(object_plan, settings.limits.build_timeout_s)))
    if spec.fixed_parts:
        out.append(check_fixed_parts(measurement, spec.fixed_parts))
    if spec.checks:
        out += run_project_checks(spec, ws.root, glb, measurement, round_index)
    if spec.track is Track.ARTICULATED_OBJECT:
        sweep, _robot = sweep_gate(ws)
        out.append(sweep)
        try:
            motion = motion_checks(ws, plan)
        except Exception as e:  # noqa: BLE001 — advisory gate; the sweep gate is the hard one
            log.warning("motion direction checks failed: %s", e)
            motion = None
        if motion is not None:
            out.append(motion)
    return measurement, out


def scene_gates(ws: Workspace, spec: Spec, plan: Plan | None, build: BuildResult) -> list[GateReport]:
    """Placement gate (+ the plan checks) on the build's census, then the asset-module checks."""
    out: list[GateReport] = []
    placement = placement_gate_safe(build.census if isinstance(build.census, dict) else None, plan=plan)
    if placement is not None:
        out.append(placement)
    if isinstance(plan, ScenePlan):
        assets = asset_gate(ws, get_runtime(spec.language), plan, spec.language)
        if assets is not None:
            out.append(assets)
    return out


# ===================================================================== the judged renders
#: the 4 clay views judged for holes/intersections; their own cameras, not a name filter over OBJECT_VIEWS
GEOMETRY_VIEWS = OBJECT_CLAY_VIEWS


@dataclass
class JudgeRenders:
    """What the judge sees of one round: the judged views, the clay geometry views, the gates the renders added
    and the track's own context lines."""

    renders: RenderSet
    geometry: RenderSet | None = None
    post_gates: list[GateReport] | None = None
    extra_context: str = ""


def render_for_judge(ws: Workspace, spec: Spec, plan: Plan | None, check: CheckResult, *, round_index: int) -> JudgeRenders:
    """The track's judged views into ``artifacts/renders/rNN``, plus its post-render gates and context."""
    if spec.track in OBJECT_TRACKS:
        return _object_renders(ws, spec, plan, check, round_index=round_index)
    if spec.track is Track.SCENE:
        return _scene_renders(ws, spec, plan, check, round_index=round_index)
    return _graphics_renders(ws, check, round_index=round_index)


def _object_renders(ws: Workspace, spec: Spec, plan: Plan | None, check: CheckResult, *, round_index: int) -> JudgeRenders:
    r = get_settings().render
    glb = Path(check.build.glb_path or "")
    out_dir = ws.renders_dir(round_index)
    rs = render_glb(glb, out_dir, views=list(OBJECT_VIEWS), width=r.width, height=r.height, sheet=True)
    geometry: RenderSet | None = None
    try:
        geometry = render_glb(glb, out_dir / "clay", views=list(GEOMETRY_VIEWS), mode="clay", sheet=False)
    except RenderError as e:  # optional judge context, never fatal
        log.warning("clay geometry render failed: %s", e)
    post: list[GateReport] = []
    sil = silhouette_gate(spec, rs)
    if sil is not None:
        post.append(sil)
    context = ""
    if spec.track is Track.ARTICULATED_OBJECT:
        rs, context = _pose_renders(ws, rs, check, out_dir)
    return JudgeRenders(renders=rs, geometry=geometry, post_gates=post, extra_context=context)


def _pose_renders(ws: Workspace, rs: RenderSet, check: CheckResult, out_dir: Path) -> tuple[RenderSet, str]:
    """Pose sheet + pose views appended to the object views, and the articulation context lines."""
    from codeverse3d.spatial.joints_export import ARTICULATION_SHEET_NAME, render_poses
    from codeverse3d.spatial.joints_model import UrdfError, load_urdf
    from codeverse3d.spatial.joints_sweep import find_urdf

    views: list[RenderView] = []
    urdf = find_urdf(ws)
    if urdf is not None:
        poses_dir = out_dir / "poses"
        try:
            robot = load_urdf(urdf, ws.artifacts / "meshes")
            for label, prs in render_poses(robot, poses_dir):
                if prs.views:
                    v = prs.views[0]
                    views.append(RenderView(name=f"pose_{label}", path=v.path, mode=v.mode, width=v.width, height=v.height))
            sheet = poses_dir / ARTICULATION_SHEET_NAME
            if sheet.is_file():
                views.insert(0, RenderView(name="articulation_sheet", path=str(sheet), mode="shaded"))
        except (UrdfError, RenderError) as e:
            log.warning("pose renders failed; the judge sees no articulation sheet: %s", e)
    rs = rs.model_copy(update={"views": list(rs.views) + views})
    sweep = next((g for g in check.gates if g.gate == SWEEP_GATE), None)
    motion = next((g for g in check.gates if g.gate == MOTION_GATE), None)
    lines = ["Articulation sheet: the pose_* views show the object at rest, each joint at its lower and upper limit."]
    if sweep is not None:
        errs = [f"- {f.target or 'joint'}: {f.message}" for f in sweep.errors]
        lines.append(f"Joint sweep: {'no penetrations' if not errs else str(len(errs)) + ' problems'}")
        lines.extend(errs[:10])
    if motion is not None:
        wrong = [f"- {f.target}: {f.message}" for f in motion.errors]
        lines.append("Motion direction (measured FK check): " + ("all planned directions realised" if not wrong else f"{len(wrong)} WRONG"))
        lines.extend(wrong[:6])
    return rs, "\n".join(lines)


def _scene_renders(ws: Workspace, spec: Spec, plan: Plan | None, check: CheckResult, *, round_index: int) -> JudgeRenders:
    r = get_settings().render
    runtime = get_runtime(spec.language)
    cameras = list(plan.cameras) if isinstance(plan, ScenePlan) else None
    out_dir = ws.renders_dir(round_index)
    rs = runtime.render_scene(ws, out_dir, cameras=cameras, orbit=True, times=SCENE_TIMES,  # type: ignore[attr-defined]
                              width=r.scene_width, height=r.scene_height, sheet=True)
    post: list[GateReport] = []
    try:
        post.append(frame_gate_from_renders(rs))   # scene_frames: exposure / coverage / motion from metrics.json
    except Exception as e:  # noqa: BLE001 — advisory instrumentation
        log.warning("scene frame gate failed: %s", e)
    judged = rs if not any(v.judge is not None for v in rs.views) else rs.model_copy(update={"views": [v for v in rs.views if v.judge]})
    lines = [t for t in (motion_text_for(out_dir),) if t]
    if isinstance(plan, ScenePlan):
        lines.append(f"Environment plan: {plan.environment}"[:1200])
        lines.append("Animation plan: " + "; ".join(plan.animation))
        lines.append("Cameras: " + "; ".join(f"{c.name} ({c.purpose})" for c in plan.cameras))
    return JudgeRenders(renders=judged, post_gates=post, extra_context="\n".join(lines))


def frames_render_set(ws: Workspace, build: BuildResult, round_index: int) -> RenderSet:
    """Copy the judged frames + sheet into renders/rNN and describe them as a RenderSet (views ``t=<s>s``)."""
    out = ws.renders_dir(round_index)
    out.mkdir(parents=True, exist_ok=True)
    frames_dir = Path(build.extra_paths.get("frames", ws.artifacts / "frames"))
    metrics = read_metrics(ws)
    if metrics is not None:
        shutil.copy2(ws.artifacts / METRICS_NAME, out / METRICS_NAME)
    stats = metrics[0] if metrics else None
    views: list[RenderView] = []
    for f in (stats.frames if stats else []):
        src = Path(f.path)
        if not src.is_file():
            continue
        dst = out / f"frame_t{f.time:05.2f}.png"
        shutil.copy2(src, dst)
        views.append(RenderView(name=f"t={f.time:g}s", path=str(dst), time_s=f.time))
    if not views and frames_dir.is_dir():  # metrics missing: fall back to the raw frame files
        for p in sorted(frames_dir.glob("f*_t*.png")):
            dst = out / p.name
            shutil.copy2(p, dst)
            views.append(RenderView(name=p.stem.split("_t", 1)[-1] + "s", path=str(dst)))
    sheet_src = Path(build.extra_paths.get("sheet", ws.artifacts / SHEET_NAME))
    sheet = None
    if sheet_src.is_file():
        sheet = out / "sheet.png"
        shutil.copy2(sheet_src, sheet)
    renderer = str(build.census.get("renderer", "")) if isinstance(build.census, dict) else ""
    return RenderSet(views=views, contact_sheet=str(sheet) if sheet else None, renderer=renderer or "moderngl",
                     duration_ms=build.duration_ms, out_dir=str(out))


def frame_stats_text(ws: Workspace) -> str:
    """The frame metrics as judge text."""
    m = read_metrics(ws)
    if m is None:
        return "(no frame metrics)"
    stats, gate = m
    lines = stats.summary_lines()
    for f in gate.findings:
        lines.append(f"- GATE gl_frames {f.as_line(with_severity=True, with_hint=f.severity is not Severity.INFO)}")
    return "\n".join(lines)


def _graphics_renders(ws: Workspace, check: CheckResult, *, round_index: int) -> JudgeRenders:
    rs = frames_render_set(ws, check.build, round_index)
    renderer = check.build.census.get("renderer", "") if isinstance(check.build.census, dict) else ""
    return JudgeRenders(renders=rs, post_gates=[],
                        extra_context=f"FRAME METRICS (measured, renderer {renderer or 'moderngl'}):\n{frame_stats_text(ws)}")


#: the plan a track expects in ``plan.json``
PLAN_MODELS: dict[Track, type[BaseModel]] = {
    Track.STATIC_OBJECT: StaticPlan,
    Track.ARTICULATED_OBJECT: ArticulatedPlan,
    Track.SCENE: ScenePlan,
    Track.GRAPHICS: GraphicsPlan,
}
