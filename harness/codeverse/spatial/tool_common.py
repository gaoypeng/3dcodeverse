"""Shared plumbing for the spatial tools: context lookups, lazy imports of
sibling packages (render / joints / probes / runtimes) with a typed
``ToolUnavailable`` failure, render-output caching and plan/spec loading.

Tools never touch globals: everything flows through :class:`ToolContext`.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from codeverse.contracts.artifacts import RenderSet, Severity
from codeverse.contracts.plan import ArticulatedPlan, GraphicsPlan, Plan, ScenePlan, StaticPlan
from codeverse.conventions import OBJECT_VIEWS, ViewPreset
from codeverse.proc import read_json_or_none
from codeverse.spatial.registry import ToolContext, ToolUnavailable, ToolUsageError
from codeverse.workspace import Workspace

__all__ = [
    "ToolUnavailable", "lazy", "spec_dict", "language_of",
    "glb_path", "reference_path", "load_plan", "resolve_views", "check_mode", "tool_out_dir", "render_cache_dir",
    "cached_render_glb", "gl_metrics_summary", "VIEW_BY_NAME", "RENDER_MODES",
]

VIEW_BY_NAME: dict[str, ViewPreset] = {v.name: v for v in OBJECT_VIEWS}
RENDER_MODES = ("shaded", "wire", "normals", "silhouette", "depth", "clay")


#: ``ToolUnavailable`` now lives in ``registry`` (``ToolDef.call`` catches it for
#: every tool); re-exported here because that is where tools import it from.


def lazy(module: str, attr: str) -> Callable[..., Any]:
    """Import ``module.attr`` now; raise :class:`ToolUnavailable` with a clear reason."""
    try:
        mod = importlib.import_module(module)
    except Exception as e:  # ModuleNotFoundError, SyntaxError in a sibling, ...
        raise ToolUnavailable(f"{module} not importable ({type(e).__name__}: {e})") from e
    fn = getattr(mod, attr, None)
    if fn is None:
        raise ToolUnavailable(f"{module} has no '{attr}'")
    return fn


# --------------------------------------------------------------------------- context
def spec_dict(ctx: ToolContext) -> dict[str, Any]:
    spec = ctx.extra.get("spec")
    if spec is not None:
        return spec.model_dump(mode="json") if hasattr(spec, "model_dump") else dict(spec)
    p = ctx.workspace.spec_path
    if p.is_file():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def language_of(ctx: ToolContext) -> str:
    lang = ctx.language or str(spec_dict(ctx).get("language", ""))
    if not lang:
        raise ToolUsageError("language unknown: pass --language or put it in spec.json")
    return lang



def reference_path(ctx: ToolContext, index: int, *, tool: str) -> tuple[Path, dict]:
    """``spec.references[index]`` as ``(absolute path, reference dict)``.

    Usage errors name ``tool`` in their example call (``compare_silhouette`` /
    ``compare_reference`` share this lookup).
    """
    refs = spec_dict(ctx).get("references") or []
    if not refs:
        raise ToolUsageError("the spec has no reference images — nothing to compare against")
    if index >= len(refs):
        raise ToolUsageError(f"reference_index {index} out of range (have {len(refs)})",
                             f"{tool}(reference_index=0)")
    ref = refs[index]
    ref = ref if isinstance(ref, dict) else {"path": ref.path, "role": ref.role, "note": ref.note}
    p = Path(ref["path"])
    if not p.is_absolute():
        p = ctx.workspace.root / p
    return p, ref


def _latest_build_status(ws: Workspace) -> dict[str, Any] | None:
    """The newest of ``artifacts/build_last.json`` (written by the build tools) and
    ``artifacts/build.json`` (written by the language runtimes), or None when
    neither is readable — a workspace whose GLB was placed by hand (tests,
    imports, first-measure flows) stays usable."""
    newest: dict[str, Any] | None = None
    newest_mtime = float("-inf")
    for name in ("build_last.json", "build.json"):
        p = ws.artifacts / name
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        data = read_json_or_none(p)
        if data is not None and mtime > newest_mtime:
            newest, newest_mtime = data, mtime
    return newest


def glb_path(ctx: ToolContext) -> Path:
    """``artifacts/object.glb``, refusing when the LATEST build did not produce it.

    Bare ``is_file()`` let every consumer (measure / render_views / compare /
    texture tools) operate on the previous round's geometry as if it were current
    after a failed or lint-blocked build."""
    p = ctx.workspace.artifacts / "object.glb"
    if not p.is_file():
        raise ToolUsageError("artifacts/object.glb does not exist yet — run `build` first", "build()")
    status = _latest_build_status(ctx.workspace)
    if status is not None and not status.get("ok"):
        raise ToolUsageError(
            "the last build failed — artifacts/object.glb is from an earlier build; fix and build again",
            "build()")
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
        raise ToolUsageError("views must not be empty", "render_views(views=['front_right_34'])")
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
    """``artifacts/tool_renders/r<NN>_<name>`` (created) — THE output location for
    anything a tool renders/writes for the agent, so every tool artifact is
    round-stamped and lands in one place."""
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
    """``render_glb`` with an on-disk RenderSet cache (same args → same files)."""
    out_dir = render_cache_dir(ctx, glb, views=[v.name for v in views], mode=mode, size=size,
                               isolate=list(isolate or []), explode=explode, sheet=sheet)
    marker = out_dir / "renderset.json"
    if marker.is_file():
        try:
            rs = RenderSet.model_validate_json(marker.read_text())
            if all(Path(v.path).is_file() for v in rs.views) and (not rs.contact_sheet or Path(rs.contact_sheet).is_file()):
                return rs
        except ValidationError:
            pass
    render_glb = lazy("codeverse.spatial.render", "render_glb")
    rs = render_glb(glb, out_dir, views=list(views), mode=mode, width=size, height=size,
                    isolate=list(isolate) if isolate else None, explode=explode, sheet=sheet)
    if not isinstance(rs, RenderSet):
        raise ToolUnavailable(f"render_glb returned {type(rs).__name__}, expected RenderSet")
    marker.write_text(rs.model_dump_json())
    return rs


# --------------------------------------------------------------------------- graphics metrics
def gl_metrics_summary(ws: Workspace, *, hints: bool = True, root: Path | None = None) -> tuple[list[str], dict[str, Any], bool]:
    """Frame stats + ``gl_frames`` findings of a graphics build as (lines, numbers, ok).

    Reads ``artifacts/metrics.json`` through ``languages._gl_common.read_metrics``
    — the one reader — and formats it the same way for every caller (the ``build``
    tool, ``gl_probe`` and ``gl_frames``).  ``hints`` appends each finding's fix
    hint; ``root`` sanitises workspace paths out of the messages.  Returns
    ``(["(no frame metrics)"], {}, True)`` when the build wrote none.
    """
    from codeverse.spatial.observe import sanitize_text

    try:
        read_metrics = lazy("codeverse.languages._gl_common", "read_metrics")
        m = read_metrics(ws)
    except ToolUnavailable:
        m = None
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
