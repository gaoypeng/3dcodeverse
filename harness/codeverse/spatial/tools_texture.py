"""Texturing tools: ``texture_pass`` (plan → generate → apply → gate on the built
GLB) and ``texture_preview`` (a labelled sheet of the current textured GLB /
generated textures).  Registered for the object tracks, every language.

The tools read ``spec.json`` / ``plan.json`` from the workspace; the image model
and judge come from the spec's backends (planner for the material plan, judge for
the ship gate) unless ``ctx.extra['texture_services']`` injects a
``codeverse.texturing.run.TextureServices`` bundle of fakes (tests).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from codeverse.contracts.common import Track
from codeverse.contracts.plan import StaticPlan
from codeverse.contracts.spec import Spec
from codeverse.spatial.observe import rel_path, text_observation
from codeverse.spatial.registry import Observation, ToolContext, ToolUsageError, tool
from codeverse.spatial.tool_common import glb_path, lazy, load_plan, spec_dict

_OBJECT_TRACKS = (Track.STATIC_OBJECT.value, Track.ARTICULATED_OBJECT.value)


class TexturePassArgs(BaseModel):
    judge: bool = Field(default=True, description="run the before/after VLM ship gate (False = ship on seam gate only)")
    model: str = Field(default="", description="planner chat model id for the material plan (default: spec planner)")
    size: int = Field(default=1024, ge=256, le=2048, description="texture size in px")


def _spec_plan(ctx: ToolContext) -> tuple[Spec, StaticPlan]:
    try:
        spec = Spec.model_validate(spec_dict(ctx))
    except Exception as e:  # pydantic ValidationError
        raise ToolUsageError(f"spec.json missing or invalid: {e}") from e
    plan = load_plan(ctx.workspace.plan_path)
    if not isinstance(plan, StaticPlan):
        raise ToolUsageError("texture_pass needs an object plan (StaticPlan / ArticulatedPlan)")
    return spec, plan


@tool("texture_pass", TexturePassArgs,
      "Text-to-image texturing of the built GLB: material plan (VLM) → tileable textures (image model) → "
      "analytic UVs + PBR materials → artifacts/object_textured.glb, shipped only if the judge score does not drop.",
      tracks=_OBJECT_TRACKS, cost_hint="slow")
def texture_pass_tool(ctx: ToolContext, args: TexturePassArgs) -> Observation:
    glb = glb_path(ctx)
    spec, plan = _spec_plan(ctx)
    texture_requested = lazy("codeverse.texturing.run", "texture_requested")
    if not texture_requested(spec):
        # ONE owner for "does this run texture?" (codeverse.texturing.run.texture_requested).
        # The tool is registered for every object track, so without this an agent could —
        # and did — buy a texture pass in a run whose spec says texture: false.
        raise ToolUsageError(
            "this run did not ask for texturing (spec.options.texture is false), so the "
            "texture pass is off; finish the geometry instead",
            "3dcv make ... --texture   # or `3dcv texture pass <slug>` after the run")
    texture_pass = lazy("codeverse.texturing.run", "texture_pass")
    services = ctx.extra.get("texture_services")  # the ONE injection point (TextureServices)
    rep = texture_pass(ctx.workspace, spec, plan, model_id=args.model or spec.backends.planner,
                       judge=args.judge, glb_in=glb, size=args.size, services=services)
    s = rep.summary()
    root = ctx.workspace.root
    lines = [
        f"texture pass: {'SHIPPED' if rep.shipped else 'not shipped'} — {s['reason'] or 'ok'}",
        f"textures: {s['n_textures']} generated ({', '.join(rep.plan.texture_ids()) or '-'}); seam failed: {s['seam_failed'] or 'none'}",
        f"parts textured: {s['parts_textured']} / skipped: {len(rep.apply.parts_skipped) if rep.apply else 0}",
    ]
    if rep.gate is not None and rep.gate.overall_before is not None:
        lines.append(f"judge before {rep.gate.overall_before:.3f} → after {rep.gate.overall_after:.3f} "
                     f"(Δ {rep.gate.delta:+.3f}; {rep.gate.materials_criterion} Δ {rep.gate.materials_delta:+.3f})")
    if rep.glb_out:
        lines.append(f"textured GLB: {rel_path(rep.glb_out, root)}")
    lines.append(f"cost ${rep.usage.cost_usd:.4f}, {rep.duration_s:.0f}s")
    images = []
    if rep.gate is not None and rep.gate.renders_after and rep.gate.renders_after.contact_sheet:
        images.append(rep.gate.renders_after.contact_sheet)
    return text_observation(lines, numbers=s, images=images)


class TexturePreviewArgs(BaseModel):
    views: list[str] = Field(default=["front_right_34", "back_left_34", "front", "top"], description="view names")


@tool("texture_preview", TexturePreviewArgs,
      "Render artifacts/object_textured.glb (after texture_pass) as a labelled contact sheet + list the generated textures.",
      tracks=_OBJECT_TRACKS, cost_hint="slow")
def texture_preview(ctx: ToolContext, args: TexturePreviewArgs) -> Observation:
    from codeverse.spatial.tool_common import cached_render_glb, resolve_views

    ws = ctx.workspace
    glb = ws.artifacts / "object_textured.glb"
    if not glb.is_file():
        raise ToolUsageError("artifacts/object_textured.glb does not exist — run texture_pass first", "texture_pass()")
    presets = resolve_views(args.views)
    rs = cached_render_glb(ctx, glb, views=presets, mode="shaded", size=512, sheet=True)
    tex_dir = ws.artifacts / "textures"
    pngs = sorted(p.name for p in tex_dir.glob("*.png")) if tex_dir.is_dir() else []
    text = f"textured GLB rendered ({len(rs.views)} views). textures: {', '.join(pngs) or 'none'}"
    images = [rs.contact_sheet] if rs.contact_sheet else [v.path for v in rs.views]
    return text_observation(text, numbers={"n_textures": len(pngs)}, images=images)
