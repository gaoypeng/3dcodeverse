"""``3dcode render`` / ``3dcode judge`` — act on one existing run.

Owns the ``render`` command (the working tree, which ends at the run's last round) with
its round-label refusal and the graphics-track frame copy, and the ``judge`` re-judge that
writes ``artifacts/judge/rNN_cli.json`` (default: the round ``addons/select`` picks).  The
single-run VIEW (``show``, and ``status`` = ``show --section status``) is ``layout_cmd.py``'s.
Its sibling ``cli/main.py`` owns the typer app, ``make`` / ``resume`` (spec building + track
dispatch), ``mcp`` and the registration of every command.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from codeverse3d.cli import _common as C
from codeverse3d.cli._common import RunsDirOpt, console, kv_table
from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import RENDER_MODES
from codeverse3d.contracts.common import Track
from codeverse3d.contracts.spec import Spec


# --------------------------------------------------------------------------- render / judge
def render(
    slug: str,
    round_index: Annotated[int | None, typer.Option("--round")] = None,
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
    if mode != "shaded" and spec.track in (Track.SCENE, Track.GRAPHICS):  # was silently rendered shaded
        raise C.CliError(f"--mode {mode} is for object runs; a {spec.track.value} run renders shaded only", code=2)
    if spec.track is Track.GRAPHICS and (width or height):  # the frames are the runtime's size: dropped silently
        raise C.CliError("--width/--height do not apply to a graphics run: its frames are rendered at the runtime's size",
                         code=2)
    idx = _render_round_or_refuse(ws, round_index)
    out_dir = out or ws.renders_dir(idx) / ("cli" if mode == "shaded" else f"cli_{mode}")
    # writes into the run (a graphics render even rebuilds it): one writer per run dir
    with C.mutating(ws, what=f"3dcode render {ws.root.name}", action="render"):
        if spec.track is Track.GRAPHICS:
            _render_graphics(ws, spec, out_dir)
            return
        # the size settings are honoured by the in-run renders (tracks/static_object.py,
        # tracks/scene.py) and were silently dropped by the one command whose whole job is
        # rendering, so a CLI render did not match the one the judge saw
        r = get_settings().render
        if spec.track is Track.SCENE:
            from codeverse3d.spatial.render_scene import render_scene

            rs = render_scene(ws, out_dir, cameras=None, width=width or r.scene_width,
                              height=height or r.scene_height)
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
    runs_dir: RunsDirOpt = None,
) -> None:
    """Re-judge a round with the SAME inputs as the in-run judge (renders + measurement +
    gates + acceptance + plan digest + previous verdict + stored clay views); writes
    artifacts/judge/rNN_cli.json."""
    from codeverse3d.addons import select
    from codeverse3d.cli import _judge as J
    from codeverse3d.record.record import RecordError, load_record

    ws = C.open_workspace(slug, runs_dir)
    try:
        rec = load_record(ws)
    except RecordError as e:
        raise C.CliError(str(e), code=2) from e
    picked = select.pick(ws.root, record=rec) if round_index is None else None
    idx = round_index if round_index is not None else (picked if picked is not None else _latest_round(ws))
    rnd = J.load_round(ws, rec, idx)
    if rnd is None or rnd.renders is None or not rnd.renders.views:
        raise C.CliError(f"round {idx} has no renders (rounds/r{idx:02d}.json / record.json)")
    rubric_name = J.rubric_for(rec, rnd, rubric)
    inp = J.build_judge_input(ws, rec, rnd)
    # an archived run keeps its sheet but may have pruned the per-view PNGs: say so, instead of
    # the judge's own FileNotFoundError / JudgeImageError escaping as a traceback
    missing = [v.path for v in inp.renders.views if not Path(v.path).is_file()]
    if missing:
        raise C.CliError(f"round {idx}: {len(missing)} of its {len(inp.renders.views)} judged renders are not on disk "
                         f"(e.g. {missing[0]}): nothing to re-judge it from", code=2)
    judge_obj = J.make_judge(rec.spec, rubric_name, model or rec.spec.backends.judge, n)
    n_images = J.count_prompt_images(inp, rubric_name, judge_obj)
    from codeverse3d.cost.instrument import run_ledger

    # writes artifacts/judge/rNN_cli.json into the run: one writer per run dir
    with C.mutating(ws, what=f"3dcode judge {ws.root.name}", action="judge"):
        try:
            # a re-judge joins the run's ledger when it has one; otherwise the per-process
            # log (a ledger holding only this verdict would be read as the whole run's cost)
            with run_ledger(ws.root, run=ws.root.name, create=False):
                verdict = judge_obj.judge(inp)
        except ValueError as e:  # e.g. a measured rubric fed to a judge that computes nothing
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
    """Graphics runs have no GLB: regenerate the judged frames + sheet via the runtime."""
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


def _render_round_or_refuse(ws, round_index: int | None) -> int:
    """Which round this render is labelled as — refusing when the label would lie.

    ``render`` renders the WORKING TREE, which ends at the last round the run wrote
    (2026-09-22: nothing restores an earlier round).  ``--round`` only chose the output
    folder, so `3dcode render X --round 3` wrote r03-labelled images of round 4's code.
    Rendering another round would need it checked out, which this command does not do —
    every round's own renders are already in ``artifacts/renders/rNN/``, and
    ``3dcode pick <slug> --round N`` packages any round with its GLB.
    """
    tree = _latest_round(ws)
    if round_index is not None and round_index != tree:
        raise C.CliError(
            f"cannot render round {round_index}: `render` renders the working tree, which is at "
            f"round {tree}, and --round only labels the output folder.  Round {round_index}'s renders "
            f"are in {ws.renders_dir(round_index)}; `3dcode pick {ws.root.name} --round {round_index}` "
            f"packages it.",
            code=2)
    return tree


def _latest_round(ws) -> int:
    rdir = ws.artifacts / "renders"
    idxs = (
        sorted(int(p.name[1:]) for p in rdir.glob("r[0-9][0-9]") if p.is_dir())
        if rdir.is_dir()
        else []
    )
    return idxs[-1] if idxs else 0


