"""Rendering tools: render_views, render_sheet, isolate, compare_silhouette.

All rendering goes through ``tool_common.cached_render_glb`` (→ ``render_glb``
from package C1, imported lazily) so repeated calls with the same arguments are
free.  Images returned are absolute paths; text only shows workspace-relative
paths.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from codeverse.conventions import OBJECT_VIEWS, OBJECT_VIEWS_QUICK
from codeverse.spatial.measure import GlbLoadError, measure_glb, measure_summary_table
from codeverse.spatial.observe import fmt_numbers, image_budget, render_observation
from codeverse.spatial.registry import Observation, ToolContext, ToolUsageError, tool
from codeverse.spatial.silhouette import compare_silhouette as _compare_silhouette
from codeverse.spatial.tool_common import (
    VIEW_BY_NAME,
    cached_render_glb,
    check_mode,
    glb_path,
    render_cache_dir,
    resolve_views,
    spec_dict,
)

_DEFAULT_VIEWS = [v.name for v in OBJECT_VIEWS_QUICK]
_MAX_SIZE = 1024


class RenderViewsArgs(BaseModel):
    views: list[str] = Field(default=list(_DEFAULT_VIEWS),
                             description=f"view names from {[v.name for v in OBJECT_VIEWS]}")
    mode: str = Field(default="shaded", description="shaded | wire | normals | silhouette | depth | clay")
    isolate: list[str] = Field(default=[], description="render only these parts (others hidden)")
    explode: float = Field(default=0.0, ge=0.0, le=3.0, description="exploded view factor (0 = assembled)")
    size: int = Field(default=512, ge=128, le=_MAX_SIZE, description="image size in px (square)")


def _render(ctx: ToolContext, tool_name: str, *, views: list[str], mode: str, isolate: list[str], explode: float, size: int, sheet: bool = True, note: str = "") -> Observation:
    # a missing renderer raises ToolUnavailable → ToolDef.call turns it into the
    # "tool <name> unavailable" Observation (one error boundary for every tool)
    glb = glb_path(ctx)
    presets = resolve_views(views)
    check_mode(mode)
    rs = cached_render_glb(ctx, glb, views=presets, mode=mode, size=size, isolate=isolate or None, explode=explode, sheet=sheet)
    return render_observation(rs, ctx.workspace.root, note=note)


@tool("render_views", RenderViewsArgs, "Render the built object from named camera views (contact sheet + views). Use to SEE what you built.", cost_hint="slow")
def render_views(ctx: ToolContext, args: RenderViewsArgs) -> Observation:
    note = f"{args.mode} render" + (f", isolate={args.isolate}" if args.isolate else "") + (f", explode={args.explode:g}" if args.explode else "")
    return _render(ctx, "render_views", views=args.views, mode=args.mode, isolate=args.isolate, explode=args.explode, size=args.size, note=note)


class RenderSheetArgs(BaseModel):
    mode: str = Field(default="shaded", description="shaded | wire | normals | silhouette | depth | clay")


@tool("render_sheet", RenderSheetArgs, "One labelled 8-view contact sheet of the built object (all canonical views).", cost_hint="slow")
def render_sheet(ctx: ToolContext, args: RenderSheetArgs) -> Observation:
    obs = _render(ctx, "render_sheet", views=[v.name for v in OBJECT_VIEWS], mode=args.mode, isolate=[], explode=0.0, size=512,
                  note=f"{args.mode} 8-view sheet")
    if obs.ok and obs.images:
        obs.images = obs.images[:1]  # the sheet alone is the deliverable here
    return obs


class IsolateArgs(BaseModel):
    part: str = Field(description="exact part (node) name to show alone")
    views: list[str] = Field(default=list(_DEFAULT_VIEWS), description="view names")


@tool("isolate", IsolateArgs, "Render ONE part alone (others hidden) + its measurement row — inspect a single part's shape and placement.", cost_hint="slow")
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
    obs = _render(ctx, "isolate", views=args.views, mode="shaded", isolate=[args.part], explode=0.0, size=512,
                  note=f"isolated part '{args.part}'")
    if not obs.ok and not obs.images:
        return obs
    obs.text = obs.text + "\n" + measure_summary_table(row)
    p = row.parts[0]
    obs.numbers.update({"part": p.name, "bbox_min_m": list(p.bbox_min), "bbox_max_m": list(p.bbox_max),
                        "tris": p.tri_count, "islands": p.islands})
    return obs


class CompareSilhouetteArgs(BaseModel):
    view: str = Field(default="front", description="view to render in silhouette mode")
    reference_index: int = Field(default=0, ge=0, description="index into spec.references")


@tool("compare_silhouette", CompareSilhouetteArgs, "Silhouette IoU / aspect-ratio error between a rendered view and a reference image (+ diff image).", cost_hint="slow")
def compare_silhouette(ctx: ToolContext, args: CompareSilhouetteArgs) -> Observation:
    glb = glb_path(ctx)
    refs = spec_dict(ctx).get("references") or []
    if not refs:
        raise ToolUsageError("the spec has no reference images — nothing to compare against")
    if args.reference_index >= len(refs):
        raise ToolUsageError(f"reference_index {args.reference_index} out of range (have {len(refs)})", "compare_silhouette(reference_index=0)")
    ref = refs[args.reference_index]
    ref_path = Path(ref["path"] if isinstance(ref, dict) else ref.path)
    if not ref_path.is_absolute():
        ref_path = ctx.workspace.root / ref_path
    if not ref_path.is_file():
        return Observation.error(f"reference image {ref_path.name} not found")
    if args.view not in VIEW_BY_NAME:
        raise ToolUsageError(f"unknown view {args.view!r}; choose from {list(VIEW_BY_NAME)}", "compare_silhouette(view='front')")
    preset = VIEW_BY_NAME[args.view]
    rs = cached_render_glb(ctx, glb, views=[preset], mode="silhouette", size=512, sheet=False)
    if not rs.views:
        return Observation.error("compare_silhouette: renderer produced no view")
    out_dir = render_cache_dir(ctx, glb, silhouette=args.view, ref=args.reference_index)
    diff = out_dir / f"silhouette_diff_{args.view}_ref{args.reference_index}.png"
    res = _compare_silhouette(rs.views[0].path, ref_path, diff_png=diff)
    verdict = ("good match" if res["iou"] >= 0.8 else "rough match" if res["iou"] >= 0.6 else "poor match")
    if not res["reliable"]:
        verdict += " (UNRELIABLE: background mask failed on one image — judge visually)"
    text = (f"silhouette '{args.view}' vs reference #{args.reference_index} ({ref_path.name}): IoU {res['iou']:.2f} → {verdict}; "
            f"aspect w/h render {res['render_aspect']:.2f} vs ref {res['ref_aspect']:.2f} (err {res['aspect_ratio_err']:.0%}). "
            f"Diff image: red = reference only, blue = render only. " + fmt_numbers({k: v for k, v in res.items() if k != "diff_png_path"}))
    images = image_budget([str(diff), rs.views[0].path])
    return Observation(ok=True, text=text, numbers={"view": args.view, **res}, images=images)
