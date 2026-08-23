"""Track/language-specific tools: joint_sweep (articulated), shader_probe /
scene_probe / scene_views (scene_threejs).  All delegate lazily to sibling
modules (``spatial.joints``, ``spatial.probes``, ``spatial.render``) written in
parallel; a missing sibling degrades to a clear ``tool X unavailable`` Observation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import GateReport, RenderSet
from codeverse.contracts.common import Language, Track
from codeverse.spatial.observe import fmt_numbers, gate_observation, render_observation, truncate
from codeverse.spatial.registry import Observation, ToolContext, ToolUsageError, tool
from codeverse.spatial.tool_common import (
    ToolUnavailable,
    call_adaptive,
    lazy,
    load_plan,
    unavailable_obs,
)


def _as_observation(result: Any, root, *, title: str) -> Observation:
    """Normalise whatever a sibling returns (Observation / GateReport / RenderSet / dict / str)."""
    if isinstance(result, Observation):
        return result
    if isinstance(result, GateReport):
        return gate_observation(result, title=title)
    if isinstance(result, RenderSet):
        return render_observation(result, root, note=title)
    if isinstance(result, BaseModel):
        result = result.model_dump(mode="json")
    if isinstance(result, dict):
        images = [str(p) for p in (result.get("images") or []) if p]
        errors = result.get("errors") or result.get("console_errors") or []
        text = title + ": " + fmt_numbers({k: v for k, v in result.items() if k not in ("images",)}, max_items=40)
        if errors:
            text += "\nerrors:\n" + "\n".join(f"  ! {str(e)[:200]}" for e in list(errors)[:10])
        return Observation(ok=not errors, text=truncate(text), numbers=result, images=images[:6])
    return Observation(ok=True, text=truncate(f"{title}: {result}"))


# --------------------------------------------------------------------------- articulated
class JointSweepArgs(BaseModel):
    joints: list[str] = Field(default=[], description="joint names to sweep (empty = all)")
    n_samples: int = Field(default=8, ge=2, le=32, description="poses per joint across its range")


@tool("joint_sweep", JointSweepArgs, "Sweep URDF joints through their ranges: pose renders + self-collision / limit findings.",
      tracks=(Track.ARTICULATED_OBJECT.value,), cost_hint="slow")
def joint_sweep(ctx: ToolContext, args: JointSweepArgs) -> Observation:
    try:
        fn = lazy("codeverse.spatial.joints", "joint_sweep_observation")
    except ToolUnavailable as e:
        return unavailable_obs("joint_sweep", e)
    out_dir = ctx.workspace.artifacts / "tool_renders" / f"r{ctx.round_index:02d}_joints"
    out_dir.mkdir(parents=True, exist_ok=True)
    # package E's signature: (ws, *, n_random, seed, render, out_dir, joint, expected_direction)
    res = call_adaptive(fn, ctx.workspace, joints=args.joints or None, n_samples=args.n_samples,
                        n_random=args.n_samples, joint=args.joints[0] if len(args.joints) == 1 else None,
                        out_dir=out_dir, round_index=ctx.round_index)
    return _as_observation(res, ctx.workspace.root, title="joint sweep")


# --------------------------------------------------------------------------- scenes
class NoArgs(BaseModel):
    """This tool takes no arguments."""


@tool("shader_probe", NoArgs, "Compile every GLSL/ShaderMaterial in the scene headlessly and report shader errors with line numbers.",
      languages=(Language.SCENE_THREEJS.value,), cost_hint="slow")
def shader_probe(ctx: ToolContext, args: NoArgs) -> Observation:
    try:
        fn = lazy("codeverse.spatial.probes", "check_shaders")
    except ToolUnavailable as e:
        return unavailable_obs("shader_probe", e)
    res = call_adaptive(fn, ctx.workspace, round_index=ctx.round_index)
    return _as_observation(res, ctx.workspace.root, title="shader probe")


@tool("scene_probe", NoArgs, "Load the scene headlessly: object/material/light census, triangle count, fps, console errors.",
      languages=(Language.SCENE_THREEJS.value,), cost_hint="slow")
def scene_probe(ctx: ToolContext, args: NoArgs) -> Observation:
    try:
        fn = lazy("codeverse.spatial.probes", "probe_scene")
    except ToolUnavailable as e:
        return unavailable_obs("scene_probe", e)
    res = call_adaptive(fn, ctx.workspace, round_index=ctx.round_index)
    obs = _as_observation(res, ctx.workspace.root, title="scene probe")
    census = obs.numbers.get("census") if isinstance(obs.numbers, dict) else None
    if isinstance(census, dict) and census:
        obs.text = obs.text + "\ncensus: " + fmt_numbers(census, max_items=30)
    return obs


class SceneViewsArgs(BaseModel):
    cameras: str = Field(default="authored", description="authored = the plan/scene cameras · orbit = overview rig · all = both")
    times: list[float] = Field(default=[0.0, 1.5], description="animation times (s) to capture")


@tool("scene_views", SceneViewsArgs, "Render the scene from its authored cameras and/or the overview rig at given times (contact sheet + views).",
      languages=(Language.SCENE_THREEJS.value,), cost_hint="slow")
def scene_views(ctx: ToolContext, args: SceneViewsArgs) -> Observation:
    if args.cameras not in ("authored", "orbit", "all"):
        raise ToolUsageError("cameras must be authored | orbit | all", "scene_views(cameras='authored')")
    if not args.times or len(args.times) > 6:
        raise ToolUsageError("times must hold 1..6 values", "scene_views(times=[0.0, 1.5])")
    try:
        render_scene = lazy("codeverse.spatial.render", "render_scene")
    except ToolUnavailable as e:
        return unavailable_obs("scene_views", e)
    cams = None
    if args.cameras in ("authored", "all") and ctx.workspace.plan_path.is_file():
        try:
            plan = load_plan(ctx.workspace.plan_path)
            cams = list(getattr(plan, "cameras", []) or []) or None
        except ToolUsageError:
            cams = None
    orbit = args.cameras in ("orbit", "all") or (args.cameras == "authored" and cams is None)
    key = f"{args.cameras}_{'_'.join(f'{t:g}' for t in args.times)}".replace(".", "p")
    out_dir = ctx.workspace.artifacts / "tool_renders" / f"r{ctx.round_index:02d}_scene_{key}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rs = call_adaptive(render_scene, ctx.workspace, out_dir, cameras=cams, orbit=orbit, times=tuple(args.times), sheet=True)
    return _as_observation(rs, ctx.workspace.root, title=f"scene views ({args.cameras}, t={args.times})")
