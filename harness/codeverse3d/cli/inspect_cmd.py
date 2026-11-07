"""``3dcodeverse render`` / ``3dcodeverse judge`` — act on one existing run: re-render its working
tree, or re-judge a round into ``artifacts/judge/rNN_cli.json``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from codeverse3d.cli import _common as C
from codeverse3d.cli._common import RunsDirOpt, console, kv_table
from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import RENDER_MODES
from codeverse3d.contracts.common import Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.contracts.spec import Spec


# --------------------------------------------------------------------------- render / judge
def render(
    slug: str,
    mode: Annotated[
        str, typer.Option("--mode", help="shaded | wire | normals | clay | silhouette")
    ] = "shaded",
    out: Annotated[Path | None, typer.Option("--out")] = None,
    width: Annotated[
        int | None,
        typer.Option("--width", min=1, help="default: render.width (scene: render.scene_width)"),
    ] = None,
    height: Annotated[
        int | None,
        typer.Option("--height", min=1, help="default: render.height (scene: render.scene_height)"),
    ] = None,
    runs_dir: RunsDirOpt = None,
) -> None:
    """Render the current artifact (object.glb or the scene) with the canonical rig."""
    ws = C.open_workspace(slug, runs_dir)
    spec = C.load_spec(ws)
    if mode not in RENDER_MODES:
        raise C.CliError(f"--mode must be one of {' | '.join(RENDER_MODES)}, not {mode!r}", code=2)
    if mode != "shaded" and spec.track in (Track.SCENE, Track.GRAPHICS):
        raise C.CliError(f"--mode {mode} is for object runs; a {spec.track.value} run renders shaded only", code=2)
    if spec.track is Track.GRAPHICS and (width or height):
        raise C.CliError("--width/--height do not apply to a graphics run: its frames are rendered at the runtime's size",
                         code=2)
    idx = max(ws.rendered_rounds(), default=0)
    out_dir = out or ws.renders_dir(idx) / ("cli" if mode == "shaded" else f"cli_{mode}")
    # a graphics render rebuilds the run: one writer per run dir
    with C.mutating(ws, what=f"3dcodeverse render {ws.root.name}", action="render"):
        if spec.track is Track.GRAPHICS:
            _render_graphics(ws, spec, out_dir)
            return
        r = get_settings().render
        if spec.track is Track.SCENE:
            from codeverse3d.conventions import SCENE_TIMES
            from codeverse3d.languages import get_runtime
            from codeverse3d.spatial.tool_common import load_plan

            plan = load_plan(ws.plan_path) if ws.plan_path.is_file() else None
            rs = get_runtime(spec.language).render_scene(
                ws, out_dir, cameras=list(plan.cameras) if isinstance(plan, ScenePlan) else None,
                times=SCENE_TIMES, width=width or r.scene_width, height=height or r.scene_height)
        else:
            glb = ws.artifacts / "object.glb"
            if not glb.is_file():
                raise C.CliError(f"no artifact to render: {glb} (run a build first)")
            from codeverse3d.spatial.render import render_glb

            rs = render_glb(glb, out_dir, mode=mode, width=width or r.width, height=height or r.height)
    console.print(
        kv_table(
            "renders",
            {
                "views": len(rs.views),
                "sheet": rs.contact_sheet,
                "dir": out_dir,
                "renderer": rs.renderer,
                "ms": rs.duration_ms,
            },
        )
    )


def judge(
    slug: str,
    round_index: Annotated[int | None, typer.Option("--round")] = None,
    rubric: Annotated[
        str | None, typer.Option("--rubric", help="default: the rubric the round was judged with")
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="judge chat model id")] = None,
    n: Annotated[int, typer.Option("--n", min=1)] = 1,
    thinking: Annotated[
        str | None, typer.Option("--thinking", help="off | low | medium | high (default: Settings.judge.thinking, "
                                                    "C3D_JUDGE__THINKING; gemini-4-argon thinks only from medium)"),
    ] = None,
    runs_dir: RunsDirOpt = None,
) -> None:
    """Re-judge a round with the same inputs as the in-run judge; writes artifacts/judge/rNN_cli.json."""
    from codeverse3d.addons import select
    from codeverse3d.cli import _judge as J
    from codeverse3d.record.record import RecordError, join_post_run, load_record

    ws = C.open_workspace(slug, runs_dir)
    try:
        rec = load_record(ws)
    except RecordError as e:
        raise C.CliError(str(e), code=2) from e
    idx = round_index if round_index is not None else select.summarise(ws.root, record=rec).round
    if idx is None:
        raise C.CliError(f"no round of {ws.root.name} built with renders: nothing to judge (--round N names one)")
    rnd = J.load_round(ws, rec, idx)
    if rnd is None or rnd.renders is None or not rnd.renders.views:
        raise C.CliError(f"round {idx} has no renders (rounds/r{idx:02d}.json / record.json)")
    rubric_name = J.rubric_for(rec, rnd, rubric)
    with C.replay_variant(rec):
        inp = J.build_judge_input(ws, rec, rnd)
    missing = [v.path for v in inp.renders.views if not Path(v.path).is_file()]
    if missing:
        raise C.CliError(f"round {idx}: {len(missing)} of its {len(inp.renders.views)} judged renders are not on disk "
                         f"(e.g. {missing[0]}): nothing to re-judge it from", code=2)
    try:
        judge_obj = J.make_judge(rec.spec, rubric_name, model or rec.spec.backends.judge, n,
                                 **({"thinking": thinking} if thinking else {}))
    except ValueError as e:
        raise C.CliError(str(e), code=2) from e
    n_images = J.count_prompt_images(inp, rubric_name, judge_obj)
    # writes into the run: one writer per run dir; judged under the run's variant
    with C.mutating(ws, what=f"3dcodeverse judge {ws.root.name}", action="judge"), C.replay_variant(rec):
        try:
            with join_post_run(ws):
                verdict = judge_obj.judge(inp)
        except ValueError as e:
            raise C.CliError(f"judge failed: {e}") from e
        except Exception as e:
            from codeverse3d.judges.vlm_judge import ReferenceJudgeError

            if isinstance(e, ReferenceJudgeError):
                raise C.CliError(f"judge failed: {e}") from e
            raise
        ws.write_json(ws.judge_path(idx, "_cli"), verdict)
    console.print(
        kv_table(
            "judgment",
            {
                "rubric": verdict.rubric,
                "round": idx,
                "overall": f"{verdict.overall:.3f}",
                "passed": verdict.passed,
                "std": verdict.score_std,
                "thinking": getattr(judge_obj, "thinking", "-"),
                "prompt images": n_images if n_images is not None else "?",
                "stored score": f"{rnd.judgment.overall:.3f}" if rnd.judgment else "-",
                "summary": verdict.summary,
                "cost": f"${verdict.usage.cost_usd:.4f}",
            },
        )
    )
    for it in verdict.improvement_plan:
        console.print(f"  [{it.priority}] {it.target}: {it.instruction}")


def _render_graphics(ws, spec: Spec, out_dir: Path) -> None:
    import shutil

    from codeverse3d.languages import get_runtime

    br = get_runtime(spec.language).build(ws)
    if not br.ok:
        raise C.CliError(f"graphics build failed: {br.error_type}: {br.error_message}")
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = Path(br.extra_paths.get("frames") or (ws.artifacts / "frames"))
    n = 0
    for p in sorted(frames_dir.glob("*.png")) if frames_dir.is_dir() else []:
        shutil.copy2(p, out_dir / p.name)
        n += 1
    sheet = br.extra_paths.get("sheet") or ""
    if sheet and Path(sheet).is_file():
        shutil.copy2(sheet, out_dir / "sheet.png")
        sheet = str(out_dir / "sheet.png")
    renderer = br.census.get("renderer", "moderngl") if isinstance(br.census, dict) else "moderngl"
    console.print(
        kv_table(
            "renders",
            {
                "views": n,
                "sheet": sheet or "-",
                "dir": out_dir,
                "renderer": renderer,
                "ms": br.duration_ms,
            },
        )
    )
