"""Core spatial tools registered with ``@tool``: build, measure, check_connectivity,
check_contract, cross_section.  Rendering tools live in ``tools_render``, scene /
articulation tools in ``tools_scene`` and the cookbook reader in ``cookbook_tool``
(all imported at the bottom so ``import codeverse.spatial.tools`` registers every
tool).

House rules: observations are compact (errors first, fix hints attached), text
never contains absolute host paths (images are absolute — the harness converts),
and every tool is cheap to call repeatedly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.config import fewer_turns_enabled, get_settings
from codeverse.contracts.artifacts import BuildResult, GateReport, Measurement
from codeverse.spatial.connectivity import check_connectivity as _check_connectivity
from codeverse.spatial.contract import check_contract as _check_contract
from codeverse.spatial.measure import GlbLoadError, measure_glb, measure_summary_table
from codeverse.spatial.observe import (
    build_failure_lines,
    error_file_display,
    gate_observation,
    lint_lines,
    rel_path,
    sanitize_text,
    text_observation,
    truncate,
)
from codeverse.spatial.registry import NoArgs, Observation, ToolContext, ToolUsageError, tool
from codeverse.spatial.sections import cross_section as _cross_section
from codeverse.spatial.tool_common import (
    gl_metrics_summary,
    glb_path,
    language_of,
    lazy,
    load_plan,
    tool_out_dir,
)

_STDERR_TAIL_LINES = 30


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


#: languages whose build has no GLB deliverable — build ok is reported through
#: the language's own artifacts instead of 'no GLB path'
_SCENE_LANGS = ("scene_threejs",)
_GL_LANGS = ("glsl_shader", "opengl_python")


def _no_glb_summary(ctx: ToolContext, br: BuildResult, language: str) -> tuple[bool, list[str], dict[str, Any]]:
    """(ok, lines, numbers) for a successful build without a GLB (scene / graphics)."""
    ws = ctx.workspace
    census = dict(br.census) if isinstance(br.census, dict) else {}
    if language in _GL_LANGS:
        # same frame-stats formatter the gl_probe / gl_frames tools use
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


#: what `build` says about itself when the gates ride along (docs/COST.md §29): the agent
#: must not spend two more round trips asking for what the build observation already holds.
_BUILD_INCLUDES_CHECKS = ("On success it ALSO runs check_connectivity and check_contract and reports them "
                          "under CONNECTIVITY / CONTRACT — do not call those two tools separately.")
_FOLDED_MAX_FINDINGS = 6
_FOLDED_SECTION_CHARS = 900   # per section, errors first — two sections + the table fit the build limit


def _folded_checks(ctx: ToolContext, glb: Path, m: Measurement, language: str) -> tuple[list[str], dict[str, Any]]:
    """The connectivity + contract gates as they would appear from their own tools, folded
    into the build observation: (lines, numbers).  Each gate is a few hundred ms on a
    built GLB; each as a separate tool call was a 4 s round trip carrying the whole
    context (docs/COST.md §29).  Same checkers, same gate JSON files, same formatter."""
    ws = ctx.workspace
    lines: list[str] = []
    numbers: dict[str, Any] = {}
    verdicts: list[str] = []

    def fold(name: str, report: GateReport) -> None:
        ws.write_json(ws.gates_dir(ctx.round_index) / f"{name.lower()}_tool.json", report)
        obs = gate_observation(report, title=f"{name}: {'PASS' if report.passed else 'FAIL'}",
                               max_findings=_FOLDED_MAX_FINDINGS)
        n_err = int(obs.numbers["errors"])
        lines.append(truncate(obs.text, _FOLDED_SECTION_CHARS))
        numbers[f"{name.lower()}_errors"] = n_err
        verdicts.append(f"{name.lower()} " + ("PASS" if report.passed else f"FAIL ({n_err} error(s))"))

    conn = _check_connectivity(glb, language=language)
    fold("CONNECTIVITY", conn)
    if ws.plan_path.is_file():
        contract = _check_contract(m, load_plan(ws.plan_path), language=language)
        fold("CONTRACT", contract)
        passed = conn.passed and contract.passed
    else:
        lines.append("CONTRACT: skipped — no plan.json in the workspace")
        passed = conn.passed
    numbers["checks_passed"] = passed
    tail = ("no separate check_connectivity / check_contract call is needed" if passed
            else "fix the ERRORs listed under CONNECTIVITY / CONTRACT below, then build again")
    summary = "CHECKS: " + " · ".join(verdicts) + " — " + tail
    return [summary, *lines], numbers


@tool("build", NoArgs, "Lint + build the code in src/ with the language runtime, export artifacts/object.glb and measure it. Call after every edit.", cost_hint="slow",
      describe_extra=lambda: _BUILD_INCLUDES_CHECKS if fewer_turns_enabled() else "")
def build(ctx: ToolContext, args: NoArgs) -> Observation:
    # ToolDef.call stamps Observation.duration_ms for every tool — no timing here
    ws = ctx.workspace
    language = language_of(ctx)
    rt = lazy("codeverse.languages", "get_runtime")(language)
    lint: GateReport = rt.lint(ws)
    lint_errors = lint_lines(lint, ws.root, errors_only=True)
    lint_warns = lint_lines(lint, ws.root, errors_only=False)
    if lint_errors:
        text = "LINT FAILED — fix these before building:\n" + "\n".join(lint_errors)
        if lint_warns:
            text += "\nwarnings:\n" + "\n".join(lint_warns[:10])
        return text_observation(text, ok=False, numbers={"stage": "lint", "lint_errors": len(lint_errors)})
    br: BuildResult = rt.build(ws, timeout_s=get_settings().limits.build_timeout_s)
    ws.write_json(ws.artifacts / "build_last.json", br)
    numbers: dict[str, Any] = {"stage": "build", "ok": br.ok, "duration_ms": br.duration_ms}
    if not br.ok:
        lines = build_failure_lines(br, ws.root, lint_warns, tail_n=_STDERR_TAIL_LINES)
        numbers.update({"error_type": br.error_type, "error_file": error_file_display(br.error_file, ws.root), "error_line": br.error_line})
        return text_observation(lines, ok=False, numbers=numbers, limit=3000)
    if not br.glb_path and language in _SCENE_LANGS + _GL_LANGS:
        # languages without a GLB deliverable: report the language's own artifacts
        ok, extra_lines, extra_numbers = _no_glb_summary(ctx, br, language)
        lines = [f"BUILD OK ({br.duration_ms} ms)"] + extra_lines
        numbers.update(extra_numbers)
        m = None
    else:
        m, table = _measure_after_build(ctx, br)
        ok = m is not None
        lines = [f"BUILD OK ({br.duration_ms} ms) → {rel_path(br.glb_path, ws.root)}"]
        if br.extra_paths:
            lines.append("extras: " + ", ".join(f"{k}={rel_path(v, ws.root)}" for k, v in br.extra_paths.items()))
        lines.append(table)
    if m is not None:
        numbers.update({"extents_m": list(m.extents), "tri_count": m.tri_count, "n_parts": len(m.parts),
                        "n_islands": m.n_islands, "ground_gap_m": m.ground_gap_m,
                        "footprint_offset_m": m.footprint_offset_m, "parts": [p.name for p in m.parts]})
        if m.extra.get("findings"):
            numbers["findings"] = m.extra["findings"]
    if lint_warns:
        lines.append("lint warnings:\n" + "\n".join(lint_warns[:10]))
    census_warn = br.census.get("warnings") if isinstance(br.census, dict) else None
    if census_warn:
        lines.append("build warnings:\n" + "\n".join(f"- {sanitize_text(str(w), ws.root)}" for w in list(census_warn)[:10]))
    if m is not None and fewer_turns_enabled():
        try:
            folded, folded_numbers = _folded_checks(ctx, Path(br.glb_path), m, language)
        except (ToolUsageError, ValueError, OSError) as e:  # a broken plan.json must not hide a good build
            folded, folded_numbers = [f"CHECKS: skipped ({type(e).__name__}: {e})"], {}
        # errors first (house rule): the verdict and both sections right under BUILD OK, so
        # the head/tail truncation eats the middle of the measurement table, never a gate error
        lines[1:1] = folded
        numbers.update(folded_numbers)
    return text_observation(lines, ok=ok, numbers=numbers, limit=3000)


# --------------------------------------------------------------------------- measure
class MeasureArgs(BaseModel):
    parts: list[str] = Field(default=[], description="limit the table to these part names (empty = all)")


@tool("measure", MeasureArgs, "Measure the built object: overall bbox/extents, ground gap, per-part size/tris/islands (Y-up meters).")
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
@tool("check_connectivity", NoArgs, "Contact graph of all parts: floating parts (with the exact gap vector to close), interpenetration, stray islands.")
def check_connectivity(ctx: ToolContext, args: NoArgs) -> Observation:
    glb = glb_path(ctx)
    report = _check_connectivity(glb, language=language_of(ctx))
    ctx.workspace.write_json(ctx.workspace.gates_dir(ctx.round_index) / "connectivity_tool.json", report)
    return gate_observation(report)


@tool("check_contract", NoArgs, "Compare the built object with plan.json: missing parts, bbox deltas per part, overall size, ground contact.")
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


@tool("cross_section", CrossSectionArgs, "Slice the object with an axis-aligned plane → labelled section image + loops/area/hollow ratio.")
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


# register the remaining tool modules (order matters only for the prompt card listing)
import codeverse.spatial.cookbook_tool  # noqa: E402,F401
import codeverse.spatial.tools_graphics  # noqa: E402,F401
import codeverse.spatial.tools_reference  # noqa: E402,F401
import codeverse.spatial.tools_render  # noqa: E402,F401
import codeverse.spatial.tools_scene  # noqa: E402,F401
import codeverse.spatial.tools_texture  # noqa: E402,F401
