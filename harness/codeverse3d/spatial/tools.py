"""Every spatial tool registered with ``@tool``; importing this module registers them all.

Observations are compact (errors first, fix hints attached) and their text never carries absolute host paths.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import RENDER_MODES, BuildResult, GateReport, Measurement
from codeverse3d.contracts.common import TRACK_LANGUAGES, Language, Track
from codeverse3d.conventions import OBJECT_VIEWS, OBJECT_VIEWS_QUICK, SCENE_TIMES
from codeverse3d.proc import read_json_or_none
from codeverse3d.spatial.connectivity import check_connectivity as _check_connectivity
from codeverse3d.spatial.contract import check_contract as _check_contract
from codeverse3d.spatial.contract import planned_joins
from codeverse3d.spatial.frame_metrics import frame_summary_text
from codeverse3d.spatial.joints_export import ARTICULATION_SHEET_NAME, render_poses
from codeverse3d.spatial.joints_poses import limit_poses
from codeverse3d.spatial.joints_sweep import find_urdf, sweep_gate
from codeverse3d.spatial.measure import GlbLoadError, measure_glb, measure_summary_table
from codeverse3d.spatial.observe import (
    build_failure_lines,
    error_file_display,
    fmt_numbers,
    gate_observation,
    lint_lines,
    rel_path,
    render_observation,
    sanitize_text,
    text_observation,
    truncate,
)
from codeverse3d.spatial.probes import run_probe
from codeverse3d.spatial.registry import NoArgs, Observation, ToolContext, ToolUsageError, tool
from codeverse3d.spatial.scene_placement import placement_census as _placement_census
from codeverse3d.spatial.scene_placement import placement_gate, placement_table_text
from codeverse3d.spatial.sections import cross_section as _cross_section
from codeverse3d.spatial.sheet import contact_sheet
from codeverse3d.spatial.silhouette import compare_silhouette as _compare_silhouette
from codeverse3d.spatial.tool_common import (
    VIEW_BY_NAME,
    cached_render_glb,
    check_mode,
    gl_metrics_summary,
    glb_path,
    language_of,
    load_plan,
    reference_path,
    render_cache_dir,
    resolve_views,
    tool_out_dir,
)

if TYPE_CHECKING:
    from codeverse3d.languages.base import SceneRuntime


# --------------------------------------------------------------------------- build
def _measure_after_build(ctx: ToolContext, br: BuildResult) -> tuple[Measurement | None, str]:
    if not br.glb_path:
        return None, "build reported success but no GLB path"
    try:
        m = measure_glb(br.glb_path)
    except GlbLoadError as e:
        return None, f"GLB unreadable: {e}"
    ctx.workspace.write_json(ctx.workspace.artifacts / "measurement.json", m)
    return m, measure_summary_table(m)


#: languages whose build has no GLB deliverable; build ok is reported through their own artifacts
SCENE_LANGUAGES = tuple(lang.value for lang in TRACK_LANGUAGES[Track.SCENE])
GRAPHICS_LANGS = tuple(lang.value for lang in TRACK_LANGUAGES[Track.GRAPHICS])


def _no_glb_summary(ctx: ToolContext, br: BuildResult, language: str) -> tuple[bool, list[str], dict[str, Any]]:
    """(ok, lines, numbers) for a successful build without a GLB (scene / graphics)."""
    ws = ctx.workspace
    census = dict(br.census) if isinstance(br.census, dict) else {}
    if language in GRAPHICS_LANGS:
        lines, numbers, ok = gl_metrics_summary(ws, hints=False, root=ws.root)
        for key in ("frames", "sheet", "gif"):
            p = br.extra_paths.get(key)
            if p:
                lines.append(f"{key}: {rel_path(p, ws.root)}")
        return ok, lines, numbers
    # scene_threejs: probe census (+ shader preflight) is the deliverable
    keep = {k: v for k, v in census.items() if isinstance(v, (int, float, str, bool)) and k != "build_report"}
    lines = ["scene probe census: " + (", ".join(f"{k}={v}" for k, v in sorted(keep.items())[:20]) or "(empty)")]
    for key in ("scene_probe", "shader_preflight"):
        p = br.extra_paths.get(key)
        if p:
            lines.append(f"{key}: {rel_path(p, ws.root)}")
    return True, lines, {"census": keep}


def _gl_gate_verdict(numbers: dict[str, Any]) -> list[str]:
    """The FRAME GATE line that goes above the '… OK' headline, or [] when it passed (a verdict, not a tool failure)."""
    n = int(numbers.get("gate_errors", 0))
    if not n:
        return []
    return [f"FRAME GATE: FAIL — {n} error(s) listed below (the code ran; fix the frames, do not re-run blind)"]


_STDERR_TAIL_LINES = 25
_LINT_WARNS_SHOWN = 10


def _lint_gate(ctx: ToolContext, rt: Any, *, verb: str) -> tuple[Observation | None, list[str]]:
    """Lint before building: (refusal observation | None, warning lines).  Lint errors skip the build and are recorded as the latest build status."""
    ws = ctx.workspace
    lint: GateReport = rt.lint(ws)
    errs = lint_lines(lint, ws.root, errors_only=True)
    warns = lint_lines(lint, ws.root, errors_only=False)
    if not errs:
        return None, warns
    ws.write_json(ws.artifacts / "build.json",
                  BuildResult(ok=False, language=language_of(ctx), error_type="LintError",
                              error_message="\n".join(errs)[:4000]))
    text = f"LINT FAILED — fix these before {verb}:\n" + "\n".join(errs)
    if warns:
        text += "\nwarnings:\n" + "\n".join(warns[:_LINT_WARNS_SHOWN])
    return text_observation(text, ok=False, numbers={"stage": "lint", "lint_errors": len(errs)}), warns


def _build_failed(ctx: ToolContext, br: BuildResult, warns: list[str]) -> Observation:
    """The BUILD FAILED observation of every build-running tool."""
    ws = ctx.workspace
    return text_observation(build_failure_lines(br, ws.root, warns, tail_n=_STDERR_TAIL_LINES), ok=False, limit=3000,
                            numbers={"stage": "build", "ok": False, "duration_ms": br.duration_ms, "error_type": br.error_type,
                                     "error_file": error_file_display(br.error_file, ws.root), "error_line": br.error_line})


def _env_unlit_only(br: BuildResult) -> str:
    """Headline for a scene build whose only errors are ``probes.unlit``, else ``""``.

    A session's own ``build`` reports it above BUILD OK, since many sessions cannot write the env module."""
    if br.ok or br.harness_failure:
        return ""
    errors = [f for g in br.gates for f in g.errors]
    if not errors or any(f.data.get("kind") != "unlit" for f in errors):
        return ""
    f = errors[0]
    return (f"SCENE UNLIT — scene_probe ERROR on {f.target}: {f.message}.  The round's build fails on it until "
            f"{f.target} lights the scene; everything else built.")


@tool("build", NoArgs, "Lint + build the code in src/ with the language runtime, export artifacts/object.glb and measure it. Call after every edit.", cost_hint="slow")
def build(ctx: ToolContext, args: NoArgs) -> Observation:
    from codeverse3d.languages import get_runtime

    ws = ctx.workspace
    language = language_of(ctx)
    rt = get_runtime(language)
    refused, lint_warns = _lint_gate(ctx, rt, verb="building")
    if refused is not None:
        return refused
    br: BuildResult = rt.build(ws, timeout_s=get_settings().limits.build_timeout_s)
    unlit_line = _env_unlit_only(br) if language in SCENE_LANGUAGES else ""
    if not br.ok and not unlit_line:
        return _build_failed(ctx, br, lint_warns)
    numbers: dict[str, Any] = {"stage": "build", "ok": br.ok, "duration_ms": br.duration_ms}
    if unlit_line:
        numbers["env_unlit"] = True
    broken = False
    if not br.glb_path and language in SCENE_LANGUAGES + GRAPHICS_LANGS:
        ok, extra_lines, extra_numbers = _no_glb_summary(ctx, br, language)
        lines = _gl_gate_verdict(extra_numbers) + [f"BUILD OK ({br.duration_ms} ms)"] + extra_lines
        numbers.update(extra_numbers)
        m = None
    else:
        m, table = _measure_after_build(ctx, br)
        ok = m is not None
        # success with nothing measurable: the tool could not answer, so no BUILD OK headline
        broken = m is None
        lines = [f"BUILD OK ({br.duration_ms} ms) → {rel_path(br.glb_path, ws.root)}" if ok
                 else f"BUILD PRODUCED NO USABLE GLB ({br.duration_ms} ms): {table}"]
        if br.extra_paths:
            lines.append("extras: " + ", ".join(f"{k}={rel_path(v, ws.root)}" for k, v in br.extra_paths.items()))
        if ok:
            lines.append(table)
    if m is not None:
        numbers.update({"extents_m": list(m.extents), "tri_count": m.tri_count, "n_parts": len(m.parts),
                        "n_islands": m.n_islands, "ground_gap_m": m.ground_gap_m,
                        "footprint_offset_m": m.footprint_offset_m, "parts": [p.name for p in m.parts]})
        if m.extra.get("findings"):
            numbers["findings"] = m.extra["findings"]
    if unlit_line:
        lines.insert(0, unlit_line)
    if lint_warns:
        lines.append("lint warnings:\n" + "\n".join(lint_warns[:_LINT_WARNS_SHOWN]))
    census_warn = br.census.get("warnings") if isinstance(br.census, dict) else None
    if census_warn:
        lines.append("build warnings:\n" + "\n".join(f"- {sanitize_text(str(w), ws.root)}" for w in list(census_warn)[:10]))
    return text_observation(lines, ok=ok, failed=broken, numbers=numbers, limit=3000)


#: tracks whose builds write artifacts/object.glb (the object-GLB toolset)
_OBJECT_TRACKS = (Track.STATIC_OBJECT.value, Track.ARTICULATED_OBJECT.value)


# --------------------------------------------------------------------------- measure
class MeasureArgs(BaseModel):
    parts: list[str] = Field(default=[], description="limit the table to these part names (empty = all)")


@tool("measure", MeasureArgs, "Measure the built object: overall bbox/extents, ground gap, per-part size/tris/islands (Y-up meters).",
      tracks=_OBJECT_TRACKS)
def measure(ctx: ToolContext, args: MeasureArgs) -> Observation:
    glb = glb_path(ctx)
    try:
        m = measure_glb(glb)
    except GlbLoadError as e:
        return Observation.error(f"measure: {e}")
    if args.parts:
        known = {p.name for p in m.parts}
        missing = [p for p in args.parts if p not in known]
        if missing:
            raise ToolUsageError(f"unknown part(s) {missing}; available: {sorted(known)}", "measure(parts=['Seat'])")
        m = m.model_copy(update={"parts": [p for p in m.parts if p.name in set(args.parts)]})
    numbers = {"extents_m": list(m.extents), "center_m": list(m.center), "bbox_min_m": list(m.bbox_min),
               "bbox_max_m": list(m.bbox_max), "tri_count": m.tri_count, "n_parts": len(m.parts),
               "n_islands": m.n_islands, "ground_gap_m": m.ground_gap_m, "footprint_offset_m": m.footprint_offset_m,
               "parts": {p.name: {"min": list(p.bbox_min), "max": list(p.bbox_max), "tris": p.tri_count,
                                  "islands": p.islands, "watertight": p.watertight} for p in m.parts[:40]}}
    return text_observation(measure_summary_table(m), numbers=numbers)


# --------------------------------------------------------------------------- gates
@tool("check_connectivity", NoArgs, "Contact graph of all parts: floating parts (with the exact gap vector to close), interpenetration, stray islands.",
      tracks=_OBJECT_TRACKS)
def check_connectivity(ctx: ToolContext, args: NoArgs) -> Observation:
    glb = glb_path(ctx)
    plan = load_plan(ctx.workspace.plan_path) if ctx.workspace.plan_path.is_file() else None
    try:  # the plan's attach_to pairs, in the GLB's part names
        planned = planned_joins(plan, measure_glb(glb)) if plan is not None else []
    except GlbLoadError as e:
        return Observation.error(f"check_connectivity: {e}")
    report = _check_connectivity(glb, language=language_of(ctx), planned_edges=planned)
    ctx.workspace.write_json(ctx.workspace.gates_dir(ctx.round_index) / "connectivity_tool.json", report)
    return gate_observation(report)


@tool("check_contract", NoArgs, "Compare the built object with plan.json: missing parts, bbox deltas per part, overall size, ground contact.",
      tracks=_OBJECT_TRACKS)
def check_contract(ctx: ToolContext, args: NoArgs) -> Observation:
    glb = glb_path(ctx)
    plan = load_plan(ctx.workspace.plan_path)
    language = language_of(ctx)
    try:
        m = measure_glb(glb)
    except GlbLoadError as e:
        return Observation.error(f"check_contract: {e}")
    report = _check_contract(m, plan, language=language)
    ctx.workspace.write_json(ctx.workspace.gates_dir(ctx.round_index) / "contract_tool.json", report)
    return gate_observation(report)


# --------------------------------------------------------------------------- sections
class CrossSectionArgs(BaseModel):
    axis: str = Field(default="y", description="slicing axis in the GLB frame: x | y (up) | z (front)")
    at: float = Field(default=0.5, description="position along the axis as a fraction of the bbox (0..1)")
    parts: list[str] = Field(default=[], description="only slice these parts (empty = all)")


@tool("cross_section", CrossSectionArgs, "Slice the object with an axis-aligned plane → labelled section image + loops/area/hollow ratio.",
      tracks=_OBJECT_TRACKS)
def cross_section(ctx: ToolContext, args: CrossSectionArgs) -> Observation:
    glb = glb_path(ctx)
    axis = args.axis.lower()
    if axis not in ("x", "y", "z"):
        raise ToolUsageError(f"axis must be x|y|z, got {args.axis!r}", "cross_section(axis='y', at=0.5)")
    if not 0.0 <= args.at <= 1.0:
        raise ToolUsageError("at must be a bbox fraction in 0..1", "cross_section(axis='y', at=0.25)")
    out_dir = tool_out_dir(ctx, "sections")
    tag = f"{axis}_{int(round(args.at * 100)):03d}" + (f"_{len(args.parts)}p" if args.parts else "")
    obs = _cross_section(glb, axis, args.at, out_dir / f"section_{tag}.png", parts=args.parts or None)
    return obs


# ===================================================================== rendering
_DEFAULT_VIEWS = [v.name for v in OBJECT_VIEWS_QUICK]
_MAX_SIZE = 1024
_MODE_DESC = " | ".join(RENDER_MODES)


class RenderViewsArgs(BaseModel):
    views: list[str] = Field(default=list(_DEFAULT_VIEWS),
                             description=f"view names from {[v.name for v in OBJECT_VIEWS]}")
    mode: str = Field(default="shaded", description=_MODE_DESC)
    isolate: list[str] = Field(default=[], description="render only these parts (others hidden)")
    explode: float = Field(default=0.0, ge=0.0, le=3.0, description="exploded view factor (0 = assembled)")
    size: int = Field(default=512, ge=128, le=_MAX_SIZE, description="image size in px (square)")


def _render(ctx: ToolContext, *, views: list[str], mode: str, isolate: list[str], explode: float, size: int, note: str = "") -> Observation:
    glb = glb_path(ctx)
    presets = resolve_views(views)
    check_mode(mode)
    rs = cached_render_glb(ctx, glb, views=presets, mode=mode, size=size, isolate=isolate or None, explode=explode)
    return render_observation(rs, ctx.workspace.root, note=note)


@tool("render_views", RenderViewsArgs, "Render the built object from named camera views (contact sheet + views). Use to SEE what you built.",
      tracks=_OBJECT_TRACKS, cost_hint="slow")
def render_views(ctx: ToolContext, args: RenderViewsArgs) -> Observation:
    note = f"{args.mode} render" + (f", isolate={args.isolate}" if args.isolate else "") + (f", explode={args.explode:g}" if args.explode else "")
    return _render(ctx, views=args.views, mode=args.mode, isolate=args.isolate, explode=args.explode, size=args.size, note=note)


class RenderSheetArgs(BaseModel):
    mode: str = Field(default="shaded", description=_MODE_DESC)


@tool("render_sheet", RenderSheetArgs, "One labelled 14-view contact sheet of the built object (all canonical views).",
      tracks=_OBJECT_TRACKS, cost_hint="slow")
def render_sheet(ctx: ToolContext, args: RenderSheetArgs) -> Observation:
    obs = _render(ctx, views=[v.name for v in OBJECT_VIEWS], mode=args.mode, isolate=[], explode=0.0, size=512,
                  note=f"{args.mode} 14-view sheet")
    if obs.ok and obs.images:
        obs.images = obs.images[:1]
    return obs


class IsolateArgs(BaseModel):
    part: str = Field(description="exact part (node) name to show alone")
    views: list[str] = Field(default=list(_DEFAULT_VIEWS), description="view names")


@tool("isolate", IsolateArgs, "Render ONE part alone (others hidden) + its measurement row — inspect a single part's shape and placement.",
      tracks=_OBJECT_TRACKS, cost_hint="slow")
def isolate(ctx: ToolContext, args: IsolateArgs) -> Observation:
    glb = glb_path(ctx)
    try:
        m = measure_glb(glb)
    except GlbLoadError as e:
        return Observation.error(f"isolate: {e}")
    names = [p.name for p in m.parts]
    if args.part not in names:
        raise ToolUsageError(f"unknown part {args.part!r}; available: {names}", f"isolate(part='{names[0] if names else 'Seat'}')")
    row = m.model_copy(update={"parts": [p for p in m.parts if p.name == args.part]})
    obs = _render(ctx, views=args.views, mode="shaded", isolate=[args.part], explode=0.0, size=512,
                  note=f"isolated part '{args.part}'")
    if not obs.ok and not obs.images:
        return obs
    obs.text = obs.text + "\n" + measure_summary_table(row)
    p = row.parts[0]
    obs.numbers.update({"part": p.name, "bbox_min_m": list(p.bbox_min), "bbox_max_m": list(p.bbox_max),
                        "tris": p.tri_count, "islands": p.islands})
    return obs


def _iou_verdict(res: dict[str, Any]) -> str:
    """Silhouette IoU → words (compare_reference)."""
    verdict = "good match" if res["iou"] >= 0.8 else "rough match" if res["iou"] >= 0.6 else "POOR match"
    if not res["reliable"]:
        verdict += " (IoU UNRELIABLE: background mask failed on one image — trust the pictures, not the number)"
    return verdict


# ===================================================================== articulation
class JointSweepArgs(BaseModel):
    joints: list[str] = Field(default=[], description="joints whose limit poses to RENDER (empty = all); "
                                                      "the collision check always covers every joint")


def _poses_for(robot, joints: list[str] | None) -> list[tuple[str, dict[str, float]]] | None:
    """``limit_poses`` narrowed to ``joints`` (rest kept); ``None`` = the full sheet."""
    if not joints:
        return None
    want = set(joints)
    return [(label, q) for label, q in limit_poses(robot) if label == "rest" or label.split("@")[0] in want]


@tool("joint_sweep", JointSweepArgs, "The round's joint_sweep gate on the built URDF — every joint through its range, "
      "one finding per link pair (overlap, unattached, buried) with its fix — plus pose renders. Pass joints=[...] for "
      "the joints you changed — rendering every joint's poses is the slow part (three views per pose); the collision "
      "check always covers the whole robot.",
      tracks=(Track.ARTICULATED_OBJECT.value,), cost_hint="slow")
def joint_sweep(ctx: ToolContext, args: JointSweepArgs) -> Observation:
    """The round's own verdict (``sweep_gate``); ``joints`` narrows only the pose renders, the collision sweep covers every joint."""
    ws = ctx.workspace
    if find_urdf(ws) is None:
        return Observation.error("joint_sweep: artifacts/robot.urdf not found — run `build` first")
    report, robot = sweep_gate(ws)
    images: list[str] = []
    if robot is not None:
        out_dir = tool_out_dir(ctx, "joints")
        render_poses(robot, out_dir, poses=_poses_for(robot, args.joints or None))
        images.append(str(out_dir / ARTICULATION_SHEET_NAME))
    return gate_observation(report, images=images)


# ===================================================================== scenes
def _scene_runtime(ctx: ToolContext) -> SceneRuntime:
    from codeverse3d.languages import get_runtime

    lang = language_of(ctx)
    if lang not in SCENE_LANGUAGES:
        raise ToolUsageError(f"scene tools only apply to {SCENE_LANGUAGES}; workspace language is {lang!r}")
    return get_runtime(lang)  # type: ignore[return-value]  # every scene language's runtime is one


@tool("shader_probe", NoArgs, "Compile every GLSL/ShaderMaterial in the scene headlessly and report shader errors with "
      "line numbers — the build's own probe + preflight, so the verdict is the build's.",
      languages=(Language.SCENE_THREEJS.value,), cost_hint="slow")
def shader_probe(ctx: ToolContext, args: NoArgs) -> Observation:
    """The build's own probe + shader preflight; a scene that never boots gets no preflight (the probe's report says why)."""
    probe, shaders, _census = run_probe(ctx.workspace, compile=True)
    died = [f.message for f in probe.findings if f.data.get("harness_failure")]
    if died:
        return Observation.error(f"shader_probe: {died[0]}")
    ran = shaders.passed or bool(shaders.findings)
    return gate_observation(shaders if ran else probe, title="shader probe")


@tool("scene_probe", NoArgs, "Load the scene headlessly: object/material/light census, draw and triangle counts, console errors.",
      languages=SCENE_LANGUAGES, cost_hint="slow")
def scene_probe(ctx: ToolContext, args: NoArgs) -> Observation:
    res = _scene_runtime(ctx).probe(ctx.workspace)
    # ok = the gate verdict; failed = the probe tool could not run
    obs = gate_observation(res.gate, title="scene probe", failed=bool(res.errors))
    if res.errors:
        obs.text += "\nerrors:\n" + "\n".join(f"  ! {e[:200]}" for e in res.errors[:10])
    if res.census:
        obs.text += "\ncensus: " + fmt_numbers(res.census, max_items=30)
        obs.numbers["census"] = res.census
    obs.text = truncate(obs.text)
    return obs


class SceneViewsArgs(BaseModel):
    cameras: str = Field(default="authored", description="authored = the plan/scene cameras · orbit = overview rig · all = both")
    times: list[float] = Field(default=list(SCENE_TIMES), description="animation times (s) to capture")


@tool("scene_views", SceneViewsArgs, "Render the scene from its authored cameras and/or the overview rig at given times (contact sheet + views).",
      languages=SCENE_LANGUAGES, cost_hint="slow")
def scene_views(ctx: ToolContext, args: SceneViewsArgs) -> Observation:
    if args.cameras not in ("authored", "orbit", "all"):
        raise ToolUsageError("cameras must be authored | orbit | all", "scene_views(cameras='authored')")
    if not args.times or len(args.times) > 6:
        raise ToolUsageError("times must hold 1..6 values", "scene_views(times=[0.0, 1.5])")
    cams = None
    if args.cameras in ("authored", "all") and ctx.workspace.plan_path.is_file():
        try:
            plan = load_plan(ctx.workspace.plan_path)
            cams = list(getattr(plan, "cameras", []) or []) or None
        except ToolUsageError:
            cams = None
    orbit = args.cameras in ("orbit", "all") or (args.cameras == "authored" and cams is None)
    key = f"{args.cameras}_{'_'.join(f'{t:g}' for t in args.times)}".replace(".", "p")
    out_dir = tool_out_dir(ctx, f"scene_{key}")
    rs = _scene_runtime(ctx).render_scene(ctx.workspace, out_dir, cameras=cams, orbit=orbit, times=tuple(args.times), sheet=True)
    obs = render_observation(rs, ctx.workspace.root, note=f"scene views ({args.cameras}, t={args.times})")
    table = _frame_table(out_dir)
    return obs.model_copy(update={"text": truncate(obs.text + "\n\n" + table)}) if table else obs


def _frame_table(out_dir: Path) -> str:
    """The numbers behind the pictures: exposure/coverage per camera plus measured motion between the times."""
    try:
        metrics = read_json_or_none(out_dir / "metrics.json")
        return frame_summary_text(metrics) if metrics else ""
    except Exception:  # noqa: BLE001
        return ""


class CheckPlacementArgs(BaseModel):
    rebuild: bool = Field(default=False, description="re-run the scene probe first (slow) instead of reading the census of the last build")


@tool("check_placement", CheckPlacementArgs,
      "The round's scene_placement gate on the last build (scene only): per placed asset the gap from its feet to what "
      "is under them, burial depth, water, contacts, plus 3-D interpenetrations between assets — findings read "
      "'floating / sunken / unsupported / cantilevered / interpenetration' with 'lower X by 0.23 m onto Terrain' hints, the "
      "share of assets tagged placement='free' (a 'free_exempt' finding when the tag hides more than a quarter of them) — "
      "and the plan checks (fog, backdrop, zone contents, scale, bounds). Reads the census of the last build/scene_probe; "
      "rebuild=true probes again. Tag ONLY a deliberately airborne thing (a hanging lantern, a bird) with "
      "obj.userData.placement = 'free' (bpy: obj['placement'] = 'free').",
      languages=SCENE_LANGUAGES, cost_hint="fast")
def check_placement(ctx: ToolContext, args: CheckPlacementArgs) -> Observation:
    ws = ctx.workspace
    census = _placement_census(ws, _scene_runtime(ctx).probe, force_probe=args.rebuild)
    plan = None
    if ws.plan_path.is_file():
        try:
            plan = load_plan(ws.plan_path)
        except ToolUsageError:
            plan = None
    report = placement_gate(ws, census, plan)
    if report is None:
        return Observation.error("check_placement: the census carries no placement table")
    obs = gate_observation(report, title="placement check")
    return obs.model_copy(update={"text": truncate(obs.text + "\n\n" + placement_table_text(census["placement"]))})


# ===================================================================== graphics
MAX_FRAMES = 8


class GlProbeArgs(BaseModel):
    """Compile the shader / import the program and render ONE frame at ``t``."""

    t: float = Field(default=1.0, ge=0.0, le=120.0, description="time (s) of the single probe frame")


class GlFramesArgs(BaseModel):
    times: list[float] = Field(default_factory=list, description="times (s) to render, up to 8 values; empty: the times the judge sees")
    width: int = Field(default=0, ge=0, le=1920, description="override width (0 = plan resolution)")
    height: int = Field(default=0, ge=0, le=1080, description="override height (0 = plan resolution)")


def _runtime(ctx: ToolContext):
    from codeverse3d.languages import get_runtime

    lang = language_of(ctx)
    if lang not in GRAPHICS_LANGS:
        raise ToolUsageError(f"gl tools only apply to {GRAPHICS_LANGS}; workspace language is {lang!r}")
    return get_runtime(lang)


def _run_build(ctx: ToolContext, *, times: list[float] | None, width: int = 0, height: int = 0) -> tuple[Observation | None, BuildResult | None, list[str]]:
    """Lint → build; returns (error observation | None, build, lint warnings)."""
    ws = ctx.workspace
    rt = _runtime(ctx)
    refused, warns = _lint_gate(ctx, rt, verb="rendering")
    if refused is not None:
        return refused, None, warns
    kw = {"times": times, "preview": False}
    if width and height:
        kw.update(width=width, height=height)
    br: BuildResult = rt.build(ws, **kw)
    if not br.ok:
        return _build_failed(ctx, br, warns), br, warns
    return None, br, warns


@tool("gl_probe", GlProbeArgs, "Compile the shader / import the program and render ONE frame (default t=1 s): errors with src line numbers, or the frame + its stats. Call after every edit.",
      languages=GRAPHICS_LANGS, cost_hint="slow")
def gl_probe(ctx: ToolContext, args: GlProbeArgs) -> Observation:
    err, br, warns = _run_build(ctx, times=[args.t])
    if err is not None:
        return err
    assert br is not None
    stats, numbers, _ok = gl_metrics_summary(ctx.workspace)
    text = "\n".join(stats)
    frames = sorted(Path(br.extra_paths["frames"]).glob("f*_t*.png")) if br.extra_paths.get("frames") else []
    lines = _gl_gate_verdict(numbers) + [f"PROBE OK ({br.duration_ms} ms, {br.census.get('renderer', 'GL')}) — frame at t={args.t:g}s", text]
    if warns:
        lines.append("lint warnings:\n" + "\n".join(warns[:_LINT_WARNS_SHOWN]))
    numbers.update({"stage": "probe", "t": args.t, "duration_ms": br.duration_ms})
    return text_observation(lines, ok=numbers.get("gate_errors", 0) == 0, numbers=numbers,
                            images=[str(p) for p in frames[:1]], limit=3000)


@tool("gl_frames", GlFramesArgs, "Render frames (by default at the times the judge sees) → labelled contact sheet + per-frame metrics (luminance, colour, detail, motion, NaN) + the gl_frames gate. LOOK at the sheet.",
      languages=GRAPHICS_LANGS, cost_hint="slow")
def gl_frames(ctx: ToolContext, args: GlFramesArgs) -> Observation:
    if len(args.times) > MAX_FRAMES:
        raise ToolUsageError(f"times must hold at most {MAX_FRAMES} values", "gl_frames() renders the judged times")
    # empty → the build's default: the frames the judge will see
    times = sorted(set(float(t) for t in args.times)) if args.times else None
    err, br, warns = _run_build(ctx, times=times, width=args.width, height=args.height)
    if err is not None:
        return err
    assert br is not None
    stats, numbers, _ok = gl_metrics_summary(ctx.workspace)
    text = "\n".join(stats)
    out_dir = tool_out_dir(ctx, f"gl_{int(time.time()) % 100000}")
    images: list[str] = []
    sheet = br.extra_paths.get("sheet")
    if sheet and Path(sheet).is_file():
        import shutil

        dst = out_dir / "sheet.png"
        shutil.copy2(sheet, dst)
        images.append(str(dst))
    lines = _gl_gate_verdict(numbers) + [f"FRAMES OK ({br.duration_ms} ms, {br.census.get('renderer', 'GL')}) — sheet tiles labelled t=<s>; compare them for motion", text]
    if warns:
        lines.append("lint warnings:\n" + "\n".join(warns[:_LINT_WARNS_SHOWN]))
    numbers.update({"stage": "frames", "times": args.times, "duration_ms": br.duration_ms, "sheet": rel_path(images[0], ctx.workspace.root) if images else ""})
    return text_observation(lines, ok=numbers.get("gate_errors", 0) == 0, numbers=numbers, images=images, limit=3000)


# ===================================================================== reference
#: what the agent compares, identity before polish
CHECKLIST = (
    "1) part inventory — is every part visible in the reference present in your model, and nothing invented? "
    "2) counts — spokes / slats / rods / panes / flutes: count them in the reference and match the number. "
    "3) proportions — the ratio of each part to the whole, not just the overall box. "
    "4) profile — where the reference curves, tapers, bevels or cuts through, does your part still read as a raw box? "
    "5) materials — per-part colour and finish."
)

class CompareReferenceArgs(BaseModel):
    view: str = Field(default="front_right_high", description="view to render for the comparison")
    reference_index: int = Field(default=0, ge=0, description="index into spec.references")
    size: int = Field(default=512, ge=256, le=1024, description="render size in px (square)")


@tool("compare_reference", CompareReferenceArgs,
      "Put the REFERENCE image and a render of your object side by side (+ outline diff and IoU). Use it to check "
      "you built the right thing: part inventory, counts, proportions, profiles.", tracks=_OBJECT_TRACKS, cost_hint="slow")
def compare_reference(ctx: ToolContext, args: CompareReferenceArgs) -> Observation:
    glb = glb_path(ctx)
    ref_path, ref = reference_path(ctx, args.reference_index)
    if not ref_path.is_file():
        return Observation.error(f"reference image {ref_path.name} not found")
    if args.view not in VIEW_BY_NAME:
        raise ToolUsageError(f"unknown view {args.view!r}; choose from {list(VIEW_BY_NAME)}",
                             "compare_reference(view='front_right_high')")
    preset = VIEW_BY_NAME[args.view]
    shaded = cached_render_glb(ctx, glb, views=[preset], mode="shaded", size=args.size, sheet=False)
    sil = cached_render_glb(ctx, glb, views=[preset], mode="silhouette", size=args.size, sheet=False)
    if not shaded.views or not sil.views:
        return Observation.error("compare_reference: renderer produced no view")
    out_dir = render_cache_dir(ctx, glb, compare_ref=args.view, ref=args.reference_index)
    diff_png = out_dir / f"outline_diff_{args.view}_ref{args.reference_index}.png"
    res = _compare_silhouette(sil.views[0].path, ref_path, diff_png=diff_png)
    sheet = contact_sheet(
        [("REFERENCE (target)", ref_path), (f"YOUR MODEL ({args.view})", shaded.views[0].path),
         ("outline: red=reference only, blue=yours", diff_png)],
        out_dir / f"compare_reference_{args.view}_ref{args.reference_index}.png", cols=3, tile=384)
    iou = float(res["iou"])
    lines = [
        f"REFERENCE #{args.reference_index} ({ref_path.name}, role={ref.get('role', 'target')}) vs your {args.view} "
        f"render: outline IoU {iou:.2f} → {_iou_verdict(res)}; aspect w/h yours {res['render_aspect']:.2f} vs reference "
        f"{res['ref_aspect']:.2f} (err {res['aspect_ratio_err']:.0%}).",
        f"Look at the sheet and check, in this order: {CHECKLIST}",
    ]
    lines.append(fmt_numbers({k: v for k, v in res.items() if k != "diff_png_path"}))
    images = [str(sheet), str(diff_png), str(shaded.views[0].path)]
    return Observation(ok=True, text="\n".join(lines),
                       numbers={"view": args.view, "reference_index": args.reference_index, **res}, images=images)
