"""Shared plumbing for the spatial tools: context lookups, render-output caching and plan/spec loading."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from codeverse3d.contracts.artifacts import RENDER_MODES, RenderSet, Severity
from codeverse3d.contracts.plan import ArticulatedPlan, GraphicsPlan, Plan, ScenePlan, StaticPlan
from codeverse3d.conventions import OBJECT_VIEWS, ViewPreset
from codeverse3d.proc import read_json_or_none
from codeverse3d.spatial.registry import ToolContext, ToolUsageError
from codeverse3d.workspace import Workspace

__all__ = [
    "spec_dict", "language_of",
    "glb_path", "reference_path", "load_plan", "resolve_views", "check_mode", "tool_out_dir", "render_cache_dir",
    "cached_render_glb", "gl_metrics_summary", "VIEW_BY_NAME",
]

VIEW_BY_NAME: dict[str, ViewPreset] = {v.name: v for v in OBJECT_VIEWS}


# --------------------------------------------------------------------------- context
def spec_dict(ctx: ToolContext) -> dict[str, Any]:
    return read_json_or_none(ctx.workspace.spec_path) or {}


def language_of(ctx: ToolContext) -> str:
    lang = ctx.language or str(spec_dict(ctx).get("language", ""))
    if not lang:
        raise ToolUsageError("language unknown: pass --language or put it in spec.json")
    return lang



def reference_path(ctx: ToolContext, index: int) -> tuple[Path, dict]:
    """``spec.references[index]`` as ``(absolute path, reference dict)`` (``compare_reference``)."""
    refs = spec_dict(ctx).get("references") or []
    if not refs:
        raise ToolUsageError("the spec has no reference images — nothing to compare against")
    if index >= len(refs):
        raise ToolUsageError(f"reference_index {index} out of range (have {len(refs)})",
                             "compare_reference(reference_index=0)")
    ref = refs[index]
    ref = ref if isinstance(ref, dict) else {"path": ref.path, "role": ref.role, "note": ref.note}
    p = Path(ref["path"])
    if not p.is_absolute():
        p = ctx.workspace.root / p
    return p, ref


def glb_path(ctx: ToolContext) -> Path:
    """``artifacts/object.glb``, refusing when the latest build failed (a stale GLB must not read as current)."""
    p = ctx.workspace.artifacts / "object.glb"
    # build.json is the build status; unreadable/absent stays permissive (a GLB placed by hand)
    status = read_json_or_none(ctx.workspace.artifacts / "build.json")
    # check status first: a failed build leaves no file, and the agent needs that reason
    if status is not None and not status.get("ok"):
        why = str(status.get("error_message") or status.get("error_type") or "see the build output")
        raise ToolUsageError(
            f"the last build FAILED ({why[:200]}) — there is no current object.glb to read. "
            "Fix the code for that error and build again; do not measure or render until it passes.",
            "build()")
    if not p.is_file():
        raise ToolUsageError("artifacts/object.glb does not exist yet — run `build` first", "build()")
    return p


def load_plan(path: Path) -> Plan:
    """Read plan.json and validate it as the right plan type (by shape)."""
    if not path.is_file():
        raise ToolUsageError("plan.json not found in the workspace")
    data = json.loads(path.read_text())
    model: type[StaticPlan] | type[ArticulatedPlan] | type[ScenePlan] | type[GraphicsPlan]
    if "zones" in data:
        model = ScenePlan
    elif "joints" in data:
        model = ArticulatedPlan
    elif "passes" in data:
        model = GraphicsPlan
    else:
        model = StaticPlan
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise ToolUsageError(f"plan.json is not a valid {model.__name__}: {e}") from e


def plan_or_none(path: Path) -> Plan | None:
    """:func:`load_plan`, or ``None`` when plan.json is absent or invalid."""
    try:
        return load_plan(path)
    except (ToolUsageError, OSError, ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- views
def resolve_views(names: Sequence[str]) -> list[ViewPreset]:
    """Map view names → presets; unknown names are a usage error listing the options."""
    out = []
    for n in names:
        v = VIEW_BY_NAME.get(n)
        if v is None:
            raise ToolUsageError(f"unknown view {n!r}; choose from {list(VIEW_BY_NAME)}", "render_views(views=['front', 'top'])")
        out.append(v)
    if not out:
        raise ToolUsageError("views must not be empty", "render_views(views=['front_right_high'])")
    return out


def check_mode(mode: str) -> str:
    if mode not in RENDER_MODES:
        raise ToolUsageError(f"unknown mode {mode!r}; choose from {list(RENDER_MODES)}", "render_views(mode='shaded')")
    return mode


# --------------------------------------------------------------------------- render cache
def _file_stamp(p: Path) -> str:
    st = p.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def tool_out_dir(ctx: ToolContext, name: str) -> Path:
    """``artifacts/tool_renders/r<NN>_<name>`` (created): the round-stamped output dir for tool artifacts."""
    d = ctx.workspace.artifacts / "tool_renders" / f"r{ctx.round_index:02d}_{name}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def render_cache_dir(ctx: ToolContext, glb: Path, **key_parts: Any) -> Path:
    """Deterministic output dir under artifacts/tool_renders keyed by glb stamp + args."""
    raw = json.dumps({"glb": _file_stamp(glb), **key_parts}, sort_keys=True, default=str)
    return tool_out_dir(ctx, hashlib.sha1(raw.encode()).hexdigest()[:8])


def cached_render_glb(
    ctx: ToolContext,
    glb: Path,
    *,
    views: Sequence[ViewPreset],
    mode: str = "shaded",
    size: int = 512,
    isolate: Sequence[str] | None = None,
    explode: float = 0.0,
    sheet: bool = True,
) -> RenderSet:
    """``render_glb`` into a deterministic per-call out_dir; the pixel cache lives only in ``spatial.render``."""
    # resolved at call time: tests patch the module attribute
    from codeverse3d.spatial.render import render_glb

    out_dir = render_cache_dir(ctx, glb, views=[v.name for v in views], mode=mode, size=size,
                               isolate=list(isolate or []), explode=explode, sheet=sheet)
    return render_glb(glb, out_dir, views=list(views), mode=mode, width=size, height=size,
                      isolate=list(isolate) if isolate else None, explode=explode, sheet=sheet)


# --------------------------------------------------------------------------- graphics metrics
def gl_metrics_summary(ws: Workspace, *, hints: bool = True, root: Path | None = None) -> tuple[list[str], dict[str, Any], bool]:
    """Frame stats + ``gl_frames`` findings of a graphics build as (lines, numbers, ok).

    ``hints`` appends fix hints; ``root`` strips workspace paths.  ``(["(no frame metrics)"], {}, True)`` when none.
    """
    from codeverse3d.languages._gl_common import read_metrics
    from codeverse3d.spatial.observe import sanitize_text

    m = read_metrics(ws)
    if m is None:
        return ["(no frame metrics)"], {}, True
    stats, gate = m
    clean = (lambda t: sanitize_text(t, root)) if root else (lambda t: t)
    lines = list(stats.summary_lines())
    for f in gate.findings:
        if f.severity is Severity.INFO:
            continue
        line = f"- {f.severity.value.upper()} [{f.data.get('kind', '')}] {clean(f.message)}"
        if hints and f.fix_hint:
            line += f"\n    fix: {clean(f.fix_hint)}"
        lines.append(line)
    numbers: dict[str, Any] = {
        "mean_lum": stats.mean_lum, "colourfulness": stats.mean_colourfulness,
        "edge_density": stats.mean_edge_density, "mean_diff": stats.mean_diff, "static": stats.static,
        "any_nan": stats.any_nan, "gate_passed": gate.passed, "gate_errors": len(gate.errors),
        "n_frames": len(stats.frames),
    }
    return lines, numbers, not gate.errors
