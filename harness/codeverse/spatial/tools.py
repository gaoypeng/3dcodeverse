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

import time
from typing import Any

from pydantic import BaseModel, Field

from codeverse.config import get_settings
from codeverse.contracts.artifacts import BuildResult, GateReport, Measurement, Severity
from codeverse.spatial.connectivity import check_connectivity as _check_connectivity
from codeverse.spatial.contract import check_contract as _check_contract
from codeverse.spatial.measure import GlbLoadError, measure_glb, measure_summary_table
from codeverse.spatial.observe import (
    gate_observation,
    rel_path,
    sanitize_text,
    tail_lines,
    truncate,
)
from codeverse.spatial.registry import Observation, ToolContext, ToolUsageError, tool
from codeverse.spatial.sections import cross_section as _cross_section
from codeverse.spatial.tool_common import (
    ToolUnavailable,
    call_adaptive,
    glb_path,
    language_of,
    lazy,
    load_plan,
    unavailable_obs,
)

_STDERR_TAIL_LINES = 30


class NoArgs(BaseModel):
    """This tool takes no arguments."""


# --------------------------------------------------------------------------- build
def _lint_lines(report: GateReport, root, *, errors_only: bool) -> list[str]:
    out = []
    for f in report.findings:
        if errors_only and f.severity != Severity.ERROR:
            continue
        if not errors_only and f.severity == Severity.ERROR:
            continue
        if f.severity == Severity.INFO:
            continue
        loc = f" ({rel_path(f.target, root)})" if f.target else ""
        out.append(f"- {f.severity.value.upper()}{loc}: {sanitize_text(f.message, root)}")
        if f.fix_hint:
            out.append(f"    fix: {sanitize_text(f.fix_hint, root)}")
    return out


def _measure_after_build(ctx: ToolContext, br: BuildResult) -> tuple[Measurement | None, str]:
    if not br.glb_path:
        return None, "build reported success but no GLB path"
    try:
        m = measure_glb(br.glb_path)
    except GlbLoadError as e:
        return None, f"GLB unreadable: {e}"
    ctx.workspace.write_json(ctx.workspace.artifacts / "measurement.json", m)
    return m, measure_summary_table(m)


@tool("build", NoArgs, "Lint + build the code in src/ with the language runtime, export artifacts/object.glb and measure it. Call after every edit.", cost_hint="slow")
def build(ctx: ToolContext, args: NoArgs) -> Observation:
    ws = ctx.workspace
    t0 = time.time()
    try:
        language = language_of(ctx)
        get_runtime = lazy("codeverse.languages", "get_runtime")
        rt = get_runtime(language)
    except ToolUnavailable as e:
        return unavailable_obs("build", e)
    lint: GateReport = rt.lint(ws)
    lint_errors = _lint_lines(lint, ws.root, errors_only=True)
    lint_warns = _lint_lines(lint, ws.root, errors_only=False)
    if lint_errors:
        text = "LINT FAILED — fix these before building:\n" + "\n".join(lint_errors)
        if lint_warns:
            text += "\nwarnings:\n" + "\n".join(lint_warns[:10])
        return Observation(ok=False, text=truncate(text), numbers={"stage": "lint", "lint_errors": len(lint_errors)},
                           duration_ms=int((time.time() - t0) * 1000))
    br: BuildResult = call_adaptive(rt.build, ws, timeout_s=get_settings().limits.build_timeout_s)
    ws.write_json(ws.artifacts / "build_last.json", br)
    numbers: dict[str, Any] = {"stage": "build", "ok": br.ok, "duration_ms": br.duration_ms}
    if not br.ok:
        where = f" at {rel_path(br.error_file, ws.root)}:{br.error_line}" if br.error_file else ""
        lines = [f"BUILD FAILED: {br.error_type or 'Error'}: {sanitize_text(br.error_message, ws.root)}{where}"]
        tail = tail_lines(sanitize_text(br.stderr_tail, ws.root), _STDERR_TAIL_LINES)
        if tail:
            lines.append("stderr (tail):\n" + tail)
        if lint_warns:
            lines.append("lint hints:\n" + "\n".join(lint_warns[:10]))
        numbers.update({"error_type": br.error_type, "error_file": rel_path(br.error_file, ws.root), "error_line": br.error_line})
        return Observation(ok=False, text=truncate("\n".join(lines), 3000), numbers=numbers,
                           duration_ms=int((time.time() - t0) * 1000))
    m, table = _measure_after_build(ctx, br)
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
    return Observation(ok=m is not None, text=truncate("\n".join(lines), 3000), numbers=numbers,
                       duration_ms=int((time.time() - t0) * 1000))


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
    return Observation(ok=True, text=truncate(measure_summary_table(m)), numbers=numbers)


# --------------------------------------------------------------------------- gates
@tool("check_connectivity", NoArgs, "Contact graph of all parts: floating parts (with the exact gap vector to close), interpenetration, stray islands.")
def check_connectivity(ctx: ToolContext, args: NoArgs) -> Observation:
    glb = glb_path(ctx)
    report = _check_connectivity(glb)
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
    out_dir = ctx.workspace.artifacts / "tool_renders" / f"r{ctx.round_index:02d}_sections"
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{axis}_{int(round(args.at * 100)):03d}" + (f"_{len(args.parts)}p" if args.parts else "")
    obs = _cross_section(glb, axis, args.at, out_dir / f"section_{tag}.png", parts=args.parts or None)
    return obs


# register the remaining tool modules (order matters only for the prompt card listing)
import codeverse.spatial.cookbook_tool  # noqa: E402,F401
import codeverse.spatial.tools_render  # noqa: E402,F401
import codeverse.spatial.tools_scene  # noqa: E402,F401

