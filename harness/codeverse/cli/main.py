"""``c3v`` — the 3dcodeverse command line.

Thin by design: every command builds typed inputs and calls into the harness
packages lazily (``cli/_common.lazy``), so the CLI imports and prints help
even while some sub-packages are incomplete.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated

import typer

from codeverse import __version__
from codeverse.cli import _common as C
from codeverse.cli._fmt import console, err_console, kv_table, ok, print_record_summary, warn
from codeverse.cli.bench_cmd import bench_app
from codeverse.cli.doctor import doctor_app
from codeverse.cli.flywheel_cmd import flywheel_app
from codeverse.cli.tools_cmd import tools
from codeverse.contracts.common import Backends, Budget, Language, Track
from codeverse.contracts.spec import Constraints, ReferenceImage, Spec

app = typer.Typer(name="c3v", help="3dcodeverse: LLMs write raw 3D code; the harness builds, judges, refines, records.",
                  pretty_exceptions_enable=False)
app.add_typer(flywheel_app, name="flywheel", help="Dataset export / pairs / captions / index.")
app.add_typer(bench_app, name="bench", help="Prompt batteries: run + report.")
app.add_typer(doctor_app, name="doctor", help="Environment checks.")
app.command("tools", help="List spatial tools or run one: `c3v tools list` | `c3v tools <name> --json '{...}' --workspace ws`.")(tools)

RunsDirOpt = Annotated[Path | None, typer.Option("--runs-dir", help="runs root (default: settings.runs_dir)")]


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context, version: Annotated[bool, typer.Option("--version", is_eager=True)] = False) -> None:
    if version:
        console.print(f"c3v {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


# --------------------------------------------------------------------------- make / resume
@app.command()
def make(
    prompt: Annotated[str, typer.Argument(help="what to build")],
    track: Annotated[Track, typer.Option("--track")] = Track.STATIC_OBJECT,
    language: Annotated[Language, typer.Option("--language")] = Language.BLENDER,
    generator: Annotated[str | None, typer.Option(help="api-agent:gemini:gemini-3.7-flash | gemini-cli:... | claude-code:... | codex:... | agy:...")] = None,
    planner: Annotated[str | None, typer.Option()] = None,
    judge: Annotated[str | None, typer.Option()] = None,
    captioner: Annotated[str | None, typer.Option()] = None,
    image: Annotated[list[Path] | None, typer.Option("--image", help="reference image(s)")] = None,
    rounds: Annotated[int, typer.Option("--rounds", min=0)] = 4,
    max_usd: Annotated[float, typer.Option("--max-usd")] = 5.0,
    max_minutes: Annotated[float, typer.Option("--max-minutes")] = 60.0,
    slug: Annotated[str | None, typer.Option("--slug")] = None,
    runs_dir: RunsDirOpt = None,
    dim: Annotated[list[str] | None, typer.Option("--dim", help="width=1.2 (meters); repeatable")] = None,
    must: Annotated[list[str] | None, typer.Option("--must", help="hard requirement; repeatable")] = None,
    must_not: Annotated[list[str] | None, typer.Option("--must-not")] = None,
    style: Annotated[str, typer.Option("--style")] = "",
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    force: Annotated[bool, typer.Option("--force", help="overwrite an existing run dir")] = False,
    no_run: Annotated[bool, typer.Option("--no-run", help="only create the workspace + spec.json")] = False,
) -> None:
    """Create a run (workspace + spec.json) and execute the track pipeline."""
    image, dim, must, must_not, tag = image or [], dim or [], must or [], must_not or [], tag or []
    for p in image:
        if not p.is_file():
            raise C.CliError(f"reference image not found: {p}")
    run_slug = C.make_slug(prompt, track.value, language.value, slug)
    ws = C.create_workspace(C.runs_root(runs_dir) / run_slug, force=force)
    backends = Backends(**{k: v for k, v in {"generator": generator, "planner": planner, "judge": judge,
                                             "captioner": captioner}.items() if v})
    try:
        spec = Spec(
            id=run_slug, track=track, language=language, prompt=prompt,
            references=[ReferenceImage(path=str(p.resolve())) for p in image],
            constraints=Constraints(dimensions_m=C.parse_kv_floats(dim, "--dim") or None, must_have=must,
                                    must_not=must_not, style=style),
            budget=Budget(max_rounds=rounds, max_usd=max_usd, max_minutes=max_minutes),
            backends=backends, seed=seed, tags=tag,
        )
    except ValueError as e:
        raise C.CliError(str(e)) from e
    ws.write_json(ws.spec_path, spec)
    ws.commit("spec")
    console.print(kv_table("run", {"slug": run_slug, "workspace": ws.root, "track": track.value,
                                   "language": language.value, "generator": backends.generator}))
    if no_run:
        ok(f"spec written: {ws.spec_path} (not run; `c3v resume {run_slug}` to start)")
        return
    _run_track(spec, ws, resume=False)


def _run_track(spec: Spec, ws, *, resume: bool) -> None:
    get_track = C.lazy("codeverse.tracks", "get_track")
    try:
        record = get_track(spec.track).run(spec, ws, resume=resume)
    except KeyboardInterrupt:
        raise C.CliError(f"interrupted; resume with `c3v resume {ws.root.name}`", code=130) from None
    except Exception as e:  # the track failed outside its own error handling
        err_console.print_exception(max_frames=8)
        raise C.CliError(f"run failed: {type(e).__name__}: {e} (workspace {ws.root}; see events.jsonl)") from e
    print_record_summary(record, ws.root)
    if record.status.value == "failed":
        raise typer.Exit(code=1)


@app.command()
def resume(slug: str, runs_dir: RunsDirOpt = None) -> None:
    """Resume an interrupted / partial run (or start a `--no-run` one)."""
    ws = C.open_workspace(slug, runs_dir)
    _run_track(C.load_spec(ws), ws, resume=True)


# --------------------------------------------------------------------------- status
@app.command()
def status(slug: str, runs_dir: RunsDirOpt = None, events: Annotated[int, typer.Option("--events", help="tail N events")] = 8) -> None:
    """Show spec / run_state / record / recent events of a run."""
    from codeverse.events import EventLog
    from codeverse.flywheel.record import RecordError, load_record

    ws = C.open_workspace(slug, runs_dir)
    spec = C.load_spec(ws)
    rows = {"workspace": ws.root, "track": spec.track.value, "language": spec.language.value,
            "generator": spec.backends.generator, "judge": spec.backends.judge, "prompt": spec.prompt}
    if ws.state_path.is_file():
        try:
            state = json.loads(ws.state_path.read_text())
            rows["run_state"] = ", ".join(f"{k}={v}" for k, v in state.items() if not isinstance(v, (dict, list)))[:300]
        except ValueError:
            rows["run_state"] = "(unreadable)"
    console.print(kv_table("status", rows))
    try:
        print_record_summary(load_record(ws), ws.root)
    except RecordError as e:
        warn(f"no record yet ({e})")
    evs = EventLog(ws.events_path).read()
    if evs:
        console.print(f"[dim]last {min(events, len(evs))} of {len(evs)} events:[/dim]")
        for ev in evs[-events:]:
            extra = {k: v for k, v in ev.items() if k not in ("t", "kind")}
            console.print(f"  {ev.get('kind')}  {json.dumps(extra, default=str)[:160]}")


# --------------------------------------------------------------------------- render / judge
@app.command()
def render(
    slug: str,
    round_index: Annotated[int | None, typer.Option("--round")] = None,
    mode: Annotated[str, typer.Option("--mode", help="shaded | wire | normals | clay | silhouette")] = "shaded",
    out: Annotated[Path | None, typer.Option("--out")] = None,
    runs_dir: RunsDirOpt = None,
) -> None:
    """Render the current artifact (object.glb or the scene) with the canonical rig."""
    ws = C.open_workspace(slug, runs_dir)
    spec = C.load_spec(ws)
    idx = round_index if round_index is not None else _latest_round(ws)
    out_dir = out or ws.renders_dir(idx) / ("cli" if mode == "shaded" else f"cli_{mode}")
    if spec.track is Track.SCENE:
        render_scene = C.lazy("codeverse.spatial.render", "render_scene")
        rs = render_scene(ws, out_dir, cameras=None)
    else:
        glb = ws.artifacts / "object.glb"
        if not glb.is_file():
            raise C.CliError(f"no artifact to render: {glb} (run a build first)")
        render_glb = C.lazy("codeverse.spatial.render", "render_glb")
        rs = render_glb(glb, out_dir, mode=mode)
    console.print(kv_table("renders", {"views": len(rs.views), "sheet": rs.contact_sheet, "dir": out_dir,
                                       "renderer": rs.renderer, "ms": rs.duration_ms}))


@app.command()
def judge(
    slug: str,
    round_index: Annotated[int | None, typer.Option("--round")] = None,
    rubric: Annotated[str | None, typer.Option("--rubric")] = None,
    model: Annotated[str | None, typer.Option("--model", help="judge chat model id")] = None,
    n: Annotated[int, typer.Option("--n", min=1)] = 1,
    runs_dir: RunsDirOpt = None,
) -> None:
    """Re-judge a round's renders (writes artifacts/judge/rNN_cli.json)."""
    from codeverse.flywheel.record import load_record

    ws = C.open_workspace(slug, runs_dir)
    rec = load_record(ws)
    idx = round_index if round_index is not None else (rec.best_round if rec.best_round is not None else _latest_round(ws))
    rnd = next((r for r in rec.rounds if r.index == idx), None)
    if rnd is None or rnd.renders is None:
        raise C.CliError(f"round {idx} has no renders in record.json")
    JudgeInput = C.lazy("codeverse.judges.base", "JudgeInput")
    VlmJudge = C.lazy("codeverse.judges.vlm_judge", "VlmJudge")
    rubric_name = rubric or {Track.STATIC_OBJECT: "static_object_v1", Track.ARTICULATED_OBJECT: "articulated_v1",
                             Track.SCENE: "scene_v1"}[rec.spec.track]
    acceptance = list(getattr(rec.plan, "acceptance", []) or [])
    inp = JudgeInput(spec=rec.spec, renders=rnd.renders, measurement=rnd.measurement, gates=rnd.gates,
                     acceptance=acceptance, round_index=idx)
    verdict = VlmJudge(rubric=rubric_name, model_id=model or rec.spec.backends.judge, n_samples=n).judge(inp)
    ws.write_json(ws.judge_path(idx, "_cli"), verdict)
    console.print(kv_table("judgment", {"rubric": verdict.rubric, "overall": f"{verdict.overall:.3f}",
                                        "passed": verdict.passed, "std": verdict.score_std, "summary": verdict.summary,
                                        "cost": f"${verdict.usage.cost_usd:.4f}"}))
    for it in verdict.improvement_plan:
        console.print(f"  [{it.priority}] {it.target}: {it.instruction}")


def _latest_round(ws) -> int:
    rdir = ws.artifacts / "renders"
    idxs = sorted(int(p.name[1:]) for p in rdir.glob("r[0-9][0-9]") if p.is_dir()) if rdir.is_dir() else []
    return idxs[-1] if idxs else 0


# --------------------------------------------------------------------------- mcp
@app.command()
def mcp(workspace: Annotated[Path, typer.Option("--workspace")]) -> None:
    """Serve the spatial tools over stdio MCP (exec `python -m codeverse.spatial.mcp_server`)."""
    if not workspace.is_dir():
        raise C.CliError(f"workspace not found: {workspace}")
    os.execvp(sys.executable, [sys.executable, "-m", "codeverse.spatial.mcp_server", "--workspace", str(workspace)])


if __name__ == "__main__":  # pragma: no cover
    app()
