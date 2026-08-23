"""Shared plumbing for the spatial tools: context lookups, lazy imports of
sibling packages (render / joints / probes / runtimes) with a typed
``ToolUnavailable`` failure, render-output caching and plan/spec loading.

Tools never touch globals: everything flows through :class:`ToolContext`.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from codeverse.contracts.artifacts import RenderSet
from codeverse.contracts.plan import ArticulatedPlan, GraphicsPlan, Plan, ScenePlan, StaticPlan
from codeverse.conventions import OBJECT_VIEWS, ViewPreset
from codeverse.spatial.registry import Observation, ToolContext, ToolUsageError

VIEW_BY_NAME: dict[str, ViewPreset] = {v.name: v for v in OBJECT_VIEWS}
RENDER_MODES = ("shaded", "wire", "normals", "silhouette", "depth", "clay")


class ToolUnavailable(RuntimeError):
    """A sibling package this tool depends on is not importable / not built yet."""


def unavailable_obs(tool: str, e: BaseException) -> Observation:
    return Observation.error(f"tool {tool} unavailable: {type(e).__name__}: {e}")


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


def call_adaptive(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Call ``fn`` passing only the keyword arguments its signature accepts.

    Sibling packages are written in parallel; this keeps us robust to optional
    keywords they may not have (required ones still raise loudly).
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return fn(*args, **kwargs)
    accepted = {k: v for k, v in kwargs.items() if k in sig.parameters}
    return fn(*args, **accepted)


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


def track_of(ctx: ToolContext) -> str:
    return ctx.track or str(spec_dict(ctx).get("track", ""))


def glb_path(ctx: ToolContext) -> Path:
    p = ctx.workspace.artifacts / "object.glb"
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


def render_cache_dir(ctx: ToolContext, glb: Path, **key_parts: Any) -> Path:
    """Deterministic output dir under artifacts/tool_renders keyed by glb stamp + args."""
    raw = json.dumps({"glb": _file_stamp(glb), **key_parts}, sort_keys=True, default=str)
    key = hashlib.sha1(raw.encode()).hexdigest()[:8]
    return ctx.workspace.artifacts / "tool_renders" / f"r{ctx.round_index:02d}_{key}"


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
    out_dir.mkdir(parents=True, exist_ok=True)
    rs = call_adaptive(render_glb, glb, out_dir, views=list(views), mode=mode, width=size, height=size,
                       isolate=list(isolate) if isolate else None, explode=explode, sheet=sheet)
    if not isinstance(rs, RenderSet):
        raise ToolUnavailable(f"render_glb returned {type(rs).__name__}, expected RenderSet")
    marker.write_text(rs.model_dump_json())
    return rs
