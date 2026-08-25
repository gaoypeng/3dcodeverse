"""``3dcv`` — the 3dcodeverse command line.

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
from codeverse.cli.cost_cmd import cost_app
from codeverse.cli.doctor import doctor_app
from codeverse.cli.flywheel_cmd import flywheel_app
from codeverse.cli.gallery_cmd import gallery_app
from codeverse.cli.texture_cmd import texture_app
from codeverse.cli.tools_cmd import tools
from codeverse.config import get_settings
from codeverse.contracts.common import TRACK_LANGUAGES, Budget, Language, Track
from codeverse.contracts.spec import Constraints, ReferenceImage, RunOptions, Spec

app = typer.Typer(name="3dcv", help="3dcodeverse: LLMs write raw 3D code; the harness builds, judges, refines, records.",
                  pretty_exceptions_enable=False)
app.add_typer(flywheel_app, name="flywheel", help="Dataset export / pairs / captions / index.")
app.add_typer(gallery_app, name="gallery", help="Look at runs locally: `serve` on localhost, `build` one shareable HTML file.")
app.add_typer(bench_app, name="bench", help="Prompt batteries: run + report.")
app.add_typer(cost_app, name="cost", help="Cost audit: per stage/role/model, waste, $ per passing artifact.")
app.add_typer(doctor_app, name="doctor", help="Environment checks.")
app.add_typer(texture_app, name="texture", help="Text-to-image texturing: object pass / scene pack.")
app.command("tools", help="List spatial tools or run one: `3dcv tools list` | `3dcv tools <name> --json '{...}' --workspace ws`.")(tools)

RunsDirOpt = Annotated[Path | None, typer.Option("--runs-dir", help="runs root (default: settings.runs_dir)")]


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context, version: Annotated[bool, typer.Option("--version", is_eager=True)] = False) -> None:
    if version:
        console.print(f"3dcv {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


# --------------------------------------------------------------------------- make / resume
@app.command()
def make(
    prompt: Annotated[str, typer.Argument(help="what to build")],
    track: Annotated[Track, typer.Option("--track")] = Track.STATIC_OBJECT,
    language: Annotated[Language | None, typer.Option("--language", help="default: the track's first language "
                                                      "(blender / urdf_blender / scene_threejs / glsl_shader)")] = None,
    generator: Annotated[str | None, typer.Option(help="api-agent:gemini:gemini-3.7-flash | gemini-cli:... | claude-code:... | codex:... | agy:...")] = None,
    planner: Annotated[str | None, typer.Option()] = None,
    judge: Annotated[str | None, typer.Option()] = None,
    captioner: Annotated[str | None, typer.Option()] = None,
    image: Annotated[list[Path] | None, typer.Option("--image", help="reference image(s)")] = None,
    reference: Annotated[bool, typer.Option("--reference/--no-reference", help="reference grounding: synthesize a "
                        "neutral studio product shot of the prompt with the image model, validate it, and use it as "
                        "the fidelity anchor for planning, the compare_reference tool, the silhouette gate and the "
                        "judge. Ignored when --image is given (your own references always win); ~$0.15 for 2 views")] = False,
    reference_views: Annotated[int, typer.Option("--reference-views", min=1, max=4, help="how many reference views to "
                        "synthesize (1 = 3/4 only, 2 = + straight front elevation)")] = 2,
    profile: Annotated[str | None, typer.Option("--profile", help="cost/quality dial: economy | balanced | quality "
                                               "(sets models, judge samples, rounds, candidates, turn cap, "
                                               "montage size and the texture pass together)")] = None,
    rounds: Annotated[int | None, typer.Option("--rounds", min=0, help="refine rounds after the baseline (default: the profile's)")] = None,
    max_usd: Annotated[float | None, typer.Option("--max-usd")] = None,
    max_minutes: Annotated[float | None, typer.Option("--max-minutes")] = None,
    candidates: Annotated[int | None, typer.Option("--candidates", min=1, help="best-of-N baseline: N parallel candidates, keep the best (default: settings.default_candidates or 1)")] = None,
    slug: Annotated[str | None, typer.Option("--slug")] = None,
    runs_dir: RunsDirOpt = None,
    dim: Annotated[list[str] | None, typer.Option("--dim", help="width=1.2 (meters); repeatable")] = None,
    must: Annotated[list[str] | None, typer.Option("--must", help="hard requirement; repeatable")] = None,
    must_not: Annotated[list[str] | None, typer.Option("--must-not")] = None,
    style: Annotated[str, typer.Option("--style")] = "",
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    texture: Annotated[bool, typer.Option("--texture", help="run the text-to-image texture pass after the rounds (frozen on spec.options)")] = False,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    force: Annotated[bool, typer.Option("--force", help="overwrite an existing run dir")] = False,
    no_run: Annotated[bool, typer.Option("--no-run", help="only create the workspace + spec.json")] = False,
) -> None:
    """Create a run (workspace + spec.json) and execute the track pipeline."""
    image, dim, must, must_not, tag = image or [], dim or [], must or [], must_not or [], tag or []
    for p in image:
        if not p.is_file():
            raise C.CliError(f"reference image not found: {p}")
    if language is None:
        language = TRACK_LANGUAGES[track][0]
    run_slug = C.make_slug(prompt, track.value, language.value, slug)
    settings = get_settings()
    # ONE resolver for the whole dial, so `--profile X` and `CV3D_PROFILE=X` land the same
    # values (they used to disagree on candidates + texture); an explicit flag beats both.
    dial = C.resolve_dial(settings, profile, rounds=rounds, candidates=candidates,
                          max_usd=max_usd, max_minutes=max_minutes, texture=texture)
    rounds, max_usd, max_minutes = dial.rounds, dial.max_usd, dial.max_minutes
    candidates, texture = dial.candidates, dial.texture
    backends = settings.backends(generator=generator, planner=planner, judge=judge, captioner=captioner)
    # validate the whole Spec BEFORE touching the filesystem: an invalid
    # track/language combination must not leave an orphan run directory behind.
    try:
        spec = Spec(
            id=run_slug, track=track, language=language, prompt=prompt,
            references=[ReferenceImage(path=str(p.resolve())) for p in image],
            constraints=Constraints(dimensions_m=C.parse_kv_floats(dim, "--dim") or None, must_have=must,
                                    must_not=must_not, style=style),
            budget=Budget(max_rounds=rounds, max_usd=max_usd, max_minutes=max_minutes),
            # options.profile records the dial this run resolved to, whichever way it was
            # named (flag, CV3D_PROFILE, config.yaml), so `3dcv resume` re-applies it
            backends=backends, options=RunOptions(candidates=candidates, texture=texture,
                                                  profile=dial.profile),
            seed=seed, tags=tag,
        )
    except ValueError as e:
        raise C.CliError(f"invalid run spec: {e}") from e
    ws = C.create_workspace(C.runs_root(runs_dir) / run_slug, force=force)
    ws.write_json(ws.spec_path, spec)
    ws.commit("spec")
    console.print(kv_table("run", {"slug": run_slug, "workspace": ws.root, "track": track.value,
                                   "language": language.value, "generator": backends.generator,
                                   "profile": f"{dial.profile} ({C.active_profile(settings).expectation()})",
                                   "judge": f"{backends.judge} n={dial.judge_samples} "
                                            f"({dial.judge_max_px}px/{dial.judge_detail_crops}crop)",
                                   "rounds": rounds, "max_usd": max_usd,
                                   "candidates": candidates, "texture": texture}))
    if reference:
        spec = _ground_in_reference(spec, ws, n_views=reference_views)
    if no_run:
        ok(f"spec written: {ws.spec_path} (not run; `3dcv resume {run_slug}` to start)")
        return
    _run_track(spec, ws, resume=False, candidates=candidates)


def _ground_in_reference(spec: Spec, ws, *, n_views: int) -> Spec:
    """`--reference`: synthesize + validate a reference image and attach it to the spec.

    Never fatal — a run that cannot get a usable reference simply runs without one.
    """
    from codeverse.cost import Role, Stage, call_context
    from codeverse.cost.instrument import run_ledger
    from codeverse.events import EventLog
    from codeverse.reference import ground_spec

    events = EventLog(ws.events_path)
    with run_ledger(ws.root, run=ws.root.name), call_context(stage=Stage.PLAN, role=Role.PLANNER, label="reference"):
        grounded, refset, why = ground_spec(spec, ws, n_views=n_views, events=events)
    (ok if grounded is not spec else warn)(f"reference grounding: {why}")
    for v in refset.views:
        detail = v.verdict.failure() if v.verdict and not v.accepted else (Path(v.path).name if v.path else "-")
        if v.accepted and v.dimension_conflict:
            got = f"{v.aspect:.2f}" if v.aspect else "?"
            detail += f"  (aspect {got} disagrees with the stated dimensions — shape target only)"
        console.print(f"  {'KEPT    ' if v.accepted else 'REJECTED'} {v.view}: {detail}")
    if refset.usage.cost_usd:
        console.print(f"  reference cost ${refset.usage.cost_usd:.4f} ({refset.source})")
    return grounded


def _run_track(spec: Spec, ws, *, resume: bool, candidates: int | None = None) -> None:
    from codeverse.cost.instrument import run_ledger

    get_track = C.lazy("codeverse.tracks", "get_track")
    options: dict = {"n_candidates": candidates} if candidates else {}
    options.update(C.round_policy_options(spec))
    try:
        # every model call and agent session of this run lands in telemetry/cost.jsonl
        with run_ledger(ws.root, run=ws.root.name):
            record = get_track(spec.track, **options).run(spec, ws, resume=resume)
    except KeyboardInterrupt:
        raise C.CliError(f"interrupted; resume with `3dcv resume {ws.root.name}`", code=130) from None
    except Exception as e:  # the track failed outside its own error handling
        err_console.print_exception(max_frames=8)
        raise C.CliError(f"run failed: {type(e).__name__}: {e} (workspace {ws.root}; see events.jsonl)") from e
    print_record_summary(record, ws.root)
    if record.status.value == "failed":
        raise typer.Exit(code=1)


def _finished_reason(ws, raised: dict) -> str:
    """Why this run must not be re-entered, or "" when resuming it is meaningful.

    A run that reached a terminal state has nothing to resume, and re-entering it is
    destructive, not idempotent: `3dcv resume` on a run that ended stop_reason='pass'
    re-ran the plan stage as a real billed model call and rewrote run_state.status from
    'passed' back to 'planning', leaving a finished run stuck mid-pipeline while still
    holding its best_score.  A BUDGET stop is the documented exception — raising a cap is
    how you continue one — so it only blocks when no cap was raised.
    """
    from codeverse.contracts.run import RunStatus
    from codeverse.orchestrator.state import RunState, StateCorrupt

    try:
        state = RunState.load(ws)
    except StateCorrupt:
        return ""  # let the track report it the way it always has
    if state is None:
        return ""
    if state.status in (RunStatus.PASSED, RunStatus.PLATEAU) or (state.status is RunStatus.BUDGET and not raised):
        detail = f"status={state.status.value}" + (f" stop_reason={state.stop_reason!r}" if state.stop_reason else "")
        if state.status is RunStatus.BUDGET:
            return f"{detail}: raise a cap to continue it (--max-usd / --max-minutes / --rounds)"
        return f"{detail} best_score={state.best_score}"
    return ""


@app.command()
def resume(
    slug: str,
    runs_dir: RunsDirOpt = None,
    candidates: Annotated[int | None, typer.Option("--candidates", min=1, help="best-of-N baseline width (only matters before round 0 ran)")] = None,
    max_usd: Annotated[float | None, typer.Option("--max-usd", help="raise the budget cap before resuming (rewrites spec.json)")] = None,
    max_minutes: Annotated[float | None, typer.Option("--max-minutes", help="raise the time cap before resuming")] = None,
    rounds: Annotated[int | None, typer.Option("--rounds", min=0, help="new max refine rounds (rewrites spec.json)")] = None,
    force: Annotated[bool, typer.Option("--force", help="re-enter a run that already finished (it will be re-planned and re-scored)")] = False,
) -> None:
    """Resume an interrupted / partial run (or start a `--no-run` one).

    ``--max-usd`` / ``--max-minutes`` / ``--rounds`` rewrite the spec's budget
    first — the only way to continue a BUDGET-stopped run.  A run that already
    reached a terminal state is refused unless ``--force``: re-entering it spends
    money and overwrites its final state."""
    ws = C.open_workspace(slug, runs_dir)
    spec = C.load_spec(ws)
    if spec.options.profile:  # the dial the run was created with (judge samples, montage px, turn cap)
        get_settings().apply_profile(spec.options.profile, force=True)
    raised = {k: v for k, v in {"max_usd": max_usd, "max_minutes": max_minutes, "max_rounds": rounds}.items() if v is not None}
    if not force and (why := _finished_reason(ws, raised)):
        raise C.CliError(f"run {ws.root.name} already finished ({why}); nothing to resume.  "
                         f"`3dcv status {ws.root.name}` to look at it, or --force to re-enter it "
                         f"(that re-plans, re-scores and overwrites the final state).")
    if raised:
        from codeverse.events import EventLog

        spec = spec.model_copy(update={"budget": spec.budget.model_copy(update=raised)})
        ws.write_json(ws.spec_path, spec)
        EventLog(ws.events_path).emit("budget.raised", **raised)
    _run_track(spec, ws, resume=True, candidates=candidates)


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
    record = None
    try:
        record = load_record(ws)
        print_record_summary(record, ws.root)
    except RecordError as e:
        warn(f"no record yet ({e})")
    _print_candidates(ws)
    if record is not None and record.extra.get("texturing"):
        t = record.extra["texturing"]
        console.print(kv_table("texturing", {
            "shipped": t.get("shipped"), "delta": t.get("delta"), "reason": t.get("reason", ""),
            "textures": t.get("n_textures", len(t.get("textures", {}) or {})),
            "glb": t.get("glb_textured", "") or "-"}))
    console.print(f"[dim]`3dcv show {ws.root.name}` for the DELIVERABLE / QUALITY EVIDENCE / COST & SETTINGS view[/dim]")
    evs = EventLog(ws.events_path).read()
    if evs:
        console.print(f"[dim]last {min(events, len(evs))} of {len(evs)} events:[/dim]")
        for ev in evs[-events:]:
            extra = {k: v for k, v in ev.items() if k not in ("t", "event")}
            console.print(f"  {_event_time(ev)}  {ev.get('event', '?'):<14} {json.dumps(extra, default=str)[:160]}")


def _print_candidates(ws) -> None:
    """Best-of-N candidate table + pairwise verdict (rounds/candidates.json), when present."""
    p = ws.root / "rounds" / "candidates.json"
    if not p.is_file():
        return
    try:
        data = json.loads(p.read_text())
    except ValueError:
        return
    cands = data.get("candidates") or []
    rows: dict[str, str] = {"n": str(data.get("n", len(cands)))}
    for c in cands:
        mark = " *" if c.get("index") == data.get("selected") else ""
        score = c.get("score")
        rows[f"{c.get('label', c.get('index'))}{mark}"] = (
            f"score {score if score is None else round(score, 3)}  build_ok={c.get('build_ok')}")
    pw = data.get("pairwise")
    if pw:
        rows["pairwise"] = f"{pw.get('a')} vs {pw.get('b')} → {pw.get('winner')} (confidence {pw.get('confidence')})"
    console.print(kv_table("candidates (best-of-N, * = selected)", rows))


def _event_time(ev: dict) -> str:
    from datetime import datetime

    from codeverse._compat import UTC

    try:
        return datetime.fromtimestamp(float(ev["t"]), UTC).strftime("%H:%M:%S")
    except (KeyError, TypeError, ValueError, OSError):
        return "--:--:--"


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
    if spec.track is Track.GRAPHICS:
        _render_graphics(ws, spec, out_dir)
        return
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
    rubric: Annotated[str | None, typer.Option("--rubric", help="default: the rubric the round was judged with")] = None,
    model: Annotated[str | None, typer.Option("--model", help="judge chat model id")] = None,
    n: Annotated[int, typer.Option("--n", min=1)] = 1,
    runs_dir: RunsDirOpt = None,
) -> None:
    """Re-judge a round with the SAME inputs as the in-run judge (renders + measurement +
    gates + acceptance + plan digest + previous verdict + stored clay views); writes
    artifacts/judge/rNN_cli.json."""
    from codeverse.cli import _judge as J
    from codeverse.flywheel.record import load_record

    ws = C.open_workspace(slug, runs_dir)
    rec = load_record(ws)
    idx = round_index if round_index is not None else (rec.best_round if rec.best_round is not None else _latest_round(ws))
    rnd = J.load_round(ws, rec, idx)
    if rnd is None or rnd.renders is None or not rnd.renders.views:
        raise C.CliError(f"round {idx} has no renders (rounds/r{idx:02d}.json / record.json)")
    rubric_name = J.rubric_for(rec, rnd, rubric)
    inp = J.build_judge_input(ws, rec, rnd)
    judge_obj = J.make_judge(rec, rubric_name, model or rec.spec.backends.judge, n)
    n_images = J.count_prompt_images(inp, rubric_name)
    from codeverse.cost.instrument import run_ledger

    try:
        # a re-judge joins the run's ledger when it has one; otherwise the per-process log
        # (a ledger holding only this verdict would be read as the whole run's cost)
        with run_ledger(ws.root, run=ws.root.name, create=False):
            verdict = judge_obj.judge(inp)
    except ValueError as e:  # e.g. a measured rubric fed to a judge that computes nothing
        raise C.CliError(f"judge failed: {e}") from e
    except Exception as e:
        ReferenceJudgeError = C.lazy("codeverse.judges.reference", "ReferenceJudgeError")
        if isinstance(e, ReferenceJudgeError):
            raise C.CliError(f"judge failed: {e}") from e
        raise
    ws.write_json(ws.judge_path(idx, "_cli"), verdict)
    console.print(kv_table("judgment", {"rubric": verdict.rubric, "round": idx, "overall": f"{verdict.overall:.3f}",
                                        "passed": verdict.passed, "std": verdict.score_std,
                                        "prompt images": n_images if n_images is not None else "?",
                                        "stored score": f"{rnd.judgment.overall:.3f}" if rnd.judgment else "-",
                                        "summary": verdict.summary,
                                        "cost": f"${verdict.usage.cost_usd:.4f}"}))
    for it in verdict.improvement_plan:
        console.print(f"  [{it.priority}] {it.target}: {it.instruction}")


def _render_graphics(ws, spec: Spec, out_dir: Path) -> None:
    """Graphics runs have no GLB: regenerate the judged frames + sheet via the runtime."""
    import shutil

    get_runtime = C.lazy("codeverse.languages", "get_runtime")
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
    console.print(kv_table("renders", {"views": n, "sheet": sheet or "-", "dir": out_dir,
                                       "renderer": renderer, "ms": br.duration_ms}))


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


# --------------------------------------------------------------------------- run layout (show / migrate)
# appended registration — see codeverse/cli/layout_cmd.py
from codeverse.cli.layout_cmd import migrate_runs_cmd as _migrate_runs_cmd  # noqa: E402
from codeverse.cli.layout_cmd import show as _show  # noqa: E402

app.command("show", help="One run in three sections: DELIVERABLE / QUALITY EVIDENCE / COST & SETTINGS.")(_show)
app.command("migrate-runs", help="Reorganise existing runs onto deliverable/ + evidence/ + telemetry/ (idempotent).")(_migrate_runs_cmd)


if __name__ == "__main__":  # pragma: no cover
    app()
