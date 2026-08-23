"""Graphics-track tools: ``gl_probe`` (compile + first frame + stats) and ``gl_frames``
(render the judged times → contact sheet + frame metrics).  Both run the language
runtime's build (lint → moderngl subprocess) so the agent sees exactly what the
harness will see; languages = glsl_shader · opengl_python.
"""

from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import BuildResult, GateReport, Severity
from codeverse.contracts.common import Language
from codeverse.spatial.observe import (
    rel_path,
    sanitize_text,
    tail_lines,
    text_observation,
)
from codeverse.spatial.registry import Observation, ToolContext, ToolUsageError, tool
from codeverse.spatial.tool_common import gl_metrics_summary, language_of, lazy, tool_out_dir

GRAPHICS_LANGS = (Language.GLSL_SHADER.value, Language.OPENGL_PYTHON.value)
MAX_FRAMES = 8


class GlProbeArgs(BaseModel):
    """Compile the shader / import the program and render ONE frame at ``t``."""

    t: float = Field(default=1.0, ge=0.0, le=120.0, description="time (s) of the single probe frame")


class GlFramesArgs(BaseModel):
    times: list[float] = Field(default=[0.0, 1.0, 2.5, 4.0, 6.0], description="times (s) to render, 1..8 values")
    width: int = Field(default=0, ge=0, le=1920, description="override width (0 = plan resolution)")
    height: int = Field(default=0, ge=0, le=1080, description="override height (0 = plan resolution)")


def _runtime(ctx: ToolContext):
    lang = language_of(ctx)
    if lang not in GRAPHICS_LANGS:
        raise ToolUsageError(f"gl tools only apply to {GRAPHICS_LANGS}; workspace language is {lang!r}")
    get_runtime = lazy("codeverse.languages", "get_runtime")
    return get_runtime(lang)


def _lint_block(report: GateReport, root: Path) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    for f in report.findings:
        if f.severity == Severity.INFO:
            continue
        line = f"- {sanitize_text(f.message, root)}" + (f"\n    fix: {sanitize_text(f.fix_hint, root)}" if f.fix_hint else "")
        (errs if f.severity == Severity.ERROR else warns).append(line)
    return errs, warns


def _failure_text(br: BuildResult, root: Path, lint_warns: list[str]) -> str:
    shown = br.error_file if not Path(br.error_file).is_absolute() else rel_path(br.error_file, root)
    where = f" at {shown}:{br.error_line}" if br.error_file else ""
    lines = [f"BUILD FAILED: {br.error_type or 'Error'}{where}", sanitize_text(br.error_message, root)]
    tail = tail_lines(sanitize_text(br.stderr_tail, root), 25)
    if tail and tail not in br.error_message:
        lines.append("log tail:\n" + tail)
    if lint_warns:
        lines.append("lint hints:\n" + "\n".join(lint_warns[:8]))
    return "\n".join(lines)


def _run_build(ctx: ToolContext, *, times: list[float], preview: bool, width: int = 0, height: int = 0) -> tuple[Observation | None, BuildResult | None, list[str]]:
    """Lint → build; returns (error observation | None, build, lint warnings).

    A missing runtime raises ToolUnavailable — ``ToolDef.call`` reports it.
    """
    ws = ctx.workspace
    rt = _runtime(ctx)
    lint: GateReport = rt.lint(ws)
    errs, warns = _lint_block(lint, ws.root)
    if errs:
        text = "LINT FAILED — fix these before rendering:\n" + "\n".join(errs)
        if warns:
            text += "\nwarnings:\n" + "\n".join(warns[:8])
        return text_observation(text, ok=False, numbers={"stage": "lint", "lint_errors": len(errs)}), None, warns
    kw = {"times": times, "preview": preview}
    if width and height:
        kw.update(width=width, height=height)
    br: BuildResult = rt.build(ws, **kw)
    ws.write_json(ws.artifacts / "build_last.json", br)
    if not br.ok:
        return text_observation(_failure_text(br, ws.root, warns), ok=False, limit=3000,
                                numbers={"stage": "build", "error_type": br.error_type, "error_file": br.error_file,
                                         "error_line": br.error_line}), br, warns
    return None, br, warns


def _stats_text(ctx: ToolContext) -> tuple[str, dict]:
    """Frame stats + gate findings as (text, numbers) — shared formatter."""
    lines, numbers, _ok = gl_metrics_summary(ctx.workspace)
    return "\n".join(lines), numbers


@tool("gl_probe", GlProbeArgs, "Compile the shader / import the program and render ONE frame (default t=1 s): errors with src line numbers, or the frame + its stats. Call after every edit.",
      languages=GRAPHICS_LANGS, cost_hint="slow")
def gl_probe(ctx: ToolContext, args: GlProbeArgs) -> Observation:
    err, br, warns = _run_build(ctx, times=[args.t], preview=False)
    if err is not None:
        return err
    assert br is not None
    text, numbers = _stats_text(ctx)
    frames = sorted(Path(br.extra_paths["frames"]).glob("f*_t*.png")) if br.extra_paths.get("frames") else []
    lines = [f"PROBE OK ({br.duration_ms} ms, {br.census.get('renderer', 'GL')}) — frame at t={args.t:g}s", text]
    if warns:
        lines.append("lint warnings:\n" + "\n".join(warns[:8]))
    numbers.update({"stage": "probe", "t": args.t, "duration_ms": br.duration_ms})
    return text_observation(lines, ok=numbers.get("gate_errors", 0) == 0, numbers=numbers,
                            images=[str(p) for p in frames[:1]], limit=3000)


@tool("gl_frames", GlFramesArgs, "Render frames at the judged times (0,1,2.5,4,6 s by default) → labelled contact sheet + per-frame metrics (luminance, colour, detail, motion, NaN) + the gl_frames gate. LOOK at the sheet.",
      languages=GRAPHICS_LANGS, cost_hint="slow")
def gl_frames(ctx: ToolContext, args: GlFramesArgs) -> Observation:
    if not args.times or len(args.times) > MAX_FRAMES:
        raise ToolUsageError(f"times must hold 1..{MAX_FRAMES} values", "gl_frames(times=[0, 1, 2.5, 4, 6])")
    err, br, warns = _run_build(ctx, times=sorted(set(float(t) for t in args.times)), preview=False, width=args.width, height=args.height)
    if err is not None:
        return err
    assert br is not None
    text, numbers = _stats_text(ctx)
    out_dir = tool_out_dir(ctx, f"gl_{int(time.time()) % 100000}")
    images: list[str] = []
    sheet = br.extra_paths.get("sheet")
    if sheet and Path(sheet).is_file():
        import shutil

        dst = out_dir / "sheet.png"
        shutil.copy2(sheet, dst)
        images.append(str(dst))
    lines = [f"FRAMES OK ({br.duration_ms} ms, {br.census.get('renderer', 'GL')}) — sheet tiles labelled t=<s>; compare them for motion", text]
    if warns:
        lines.append("lint warnings:\n" + "\n".join(warns[:8]))
    numbers.update({"stage": "frames", "times": args.times, "duration_ms": br.duration_ms, "sheet": rel_path(images[0], ctx.workspace.root) if images else ""})
    return text_observation(lines, ok=numbers.get("gate_errors", 0) == 0, numbers=numbers, images=images, limit=3000)
