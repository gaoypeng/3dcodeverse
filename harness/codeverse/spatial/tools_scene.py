"""Track/language-specific tools: joint_sweep (articulated), shader_probe /
scene_probe / scene_views (scene_threejs).  All delegate lazily to sibling
modules (``spatial.joints``, ``spatial.probes``, ``spatial.render_scene``) written in
parallel; a missing sibling raises ``ToolUnavailable``, which ``ToolDef.call``
turns into a clear ``tool X unavailable`` Observation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import GateReport, RenderSet
from codeverse.contracts.common import Language, Track
from codeverse.spatial.observe import fmt_numbers, gate_observation, render_observation, truncate
from codeverse.spatial.registry import Observation, ToolContext, ToolUsageError, tool
from codeverse.spatial.tool_common import lazy, load_plan, tool_out_dir


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


@tool("joint_sweep", JointSweepArgs, "Sweep URDF joints through their ranges: self-collision / limit findings for ALL joints, "
      "plus pose renders. Pass joints=[...] for the joints you changed — rendering every joint's poses is the slow part "
      "(three views per pose); the collision check always covers the whole robot.",
      tracks=(Track.ARTICULATED_OBJECT.value,), cost_hint="slow")
def joint_sweep(ctx: ToolContext, args: JointSweepArgs) -> Observation:
    fn = lazy("codeverse.spatial.joints", "joint_sweep_observation")
    out_dir = tool_out_dir(ctx, "joints")
    res = fn(ctx.workspace, joints=args.joints or None, n_random=args.n_samples,
             joint=args.joints[0] if len(args.joints) == 1 else None, out_dir=out_dir)
    return _as_observation(res, ctx.workspace.root, title="joint sweep")


# --------------------------------------------------------------------------- scenes
class NoArgs(BaseModel):
    """This tool takes no arguments."""


@tool("shader_probe", NoArgs, "Compile every GLSL/ShaderMaterial in the scene headlessly and report shader errors with line numbers.",
      languages=(Language.SCENE_THREEJS.value,), cost_hint="slow")
def shader_probe(ctx: ToolContext, args: NoArgs) -> Observation:
    fn = lazy("codeverse.spatial.probes", "check_shaders")
    res = fn(ctx.workspace)
    return _as_observation(res, ctx.workspace.root, title="shader probe")


@tool("scene_probe", NoArgs, "Load the scene headlessly: object/material/light census, triangle count, fps, console errors.",
      languages=(Language.SCENE_THREEJS.value,), cost_hint="slow")
def scene_probe(ctx: ToolContext, args: NoArgs) -> Observation:
    fn = lazy("codeverse.spatial.probes", "probe_scene")
    res = fn(ctx.workspace)
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
    render_scene = lazy("codeverse.spatial.render_scene", "render_scene")
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
    rs = render_scene(ctx.workspace, out_dir, cameras=cams, orbit=orbit, times=tuple(args.times), sheet=True)
    obs = _as_observation(rs, ctx.workspace.root, title=f"scene views ({args.cameras}, t={args.times})")
    table = _frame_table(out_dir)
    return obs.model_copy(update={"text": truncate(obs.text + "\n\n" + table)}) if table else obs


def _frame_table(out_dir) -> str:
    """The deterministic numbers behind the pictures — exposure/coverage per camera plus the
    measured motion between the times.  Without them the agent is asked to LOOK at a sheet and
    guess whether its sway is visible or its dusk is too dark; with them it can read the answer."""
    read_metrics = lazy("codeverse.spatial.render_scene", "read_metrics")
    frame_summary_text = lazy("codeverse.spatial.frame_metrics", "frame_summary_text")
    try:
        metrics = read_metrics(out_dir)
        return frame_summary_text(metrics) if metrics else ""
    except Exception:  # noqa: BLE001 — a tool observation must never fail on instrumentation
        return ""
