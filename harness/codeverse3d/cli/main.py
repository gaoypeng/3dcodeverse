"""``3dcodeverse`` — the command line: the typer app, every registration, and ``make`` /
``resume`` / ``pick`` / ``mcp``.  Commands import the harness lazily so ``--help`` stays fast."""

from __future__ import annotations

import contextlib
import faulthandler
import json
import os
import signal
import sys
from pathlib import Path
from typing import Annotated

import typer

from codeverse3d import __version__
from codeverse3d.cli import _common as C
from codeverse3d.cli._common import (
    RunsDirOpt,
    console,
    err_console,
    kv_table,
    ok,
    print_observation,
    print_record_summary,
    warn,
)
from codeverse3d.cli.cost_cmd import cost_app
from codeverse3d.cli.flywheel_cmd import flywheel_app
from codeverse3d.cli.skills_cmd import skills_app
from codeverse3d.config import get_settings
from codeverse3d.contracts.common import TRACK_INFO, TRACK_LANGUAGES, Budget, Language, Track
from codeverse3d.contracts.spec import Constraints, ReferenceImage, RunOptions, Spec


# ===================================================================== doctor
def doctor(
    live: Annotated[bool, typer.Option("--live", help="make one tiny Gemini call")] = False,
    gpu: Annotated[bool, typer.Option("--gpu/--no-gpu", help="probe headless Chrome WebGL")] = True,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check python deps, Blender, node/three/puppeteer, keys, CLIs and MCP."""
    from codeverse3d.doctor import run_doctor

    rows = run_doctor(live=live, gpu=gpu)
    if as_json:
        console.print_json(json.dumps([{"check": c, "status": s, "detail": d} for c, s, d in rows]))
    else:
        console.print(C.doctor_table(rows))
    if any(s == "FAIL" for _, s, _ in rows):
        raise typer.Exit(code=1)


# ===================================================================== tools_cmd
def tools(
    name: Annotated[str, typer.Argument(help="'list' or a tool name")] = "list",
    args_json: Annotated[str, typer.Option("--json", help="arguments object as JSON")] = "{}",
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
    round_index: Annotated[int, typer.Option("--round")] = 0,
    as_json: Annotated[bool, typer.Option("--json-out", help="print the Observation as JSON")] = False,
    cards: Annotated[bool, typer.Option("--cards", help="(list) print prompt cards instead of a table")] = False,
) -> None:
    from codeverse3d.spatial import registry

    if name == "list":
        defs = registry.list_tools()
        if cards:
            console.print(registry.tool_cards())
            return
        from rich.table import Table

        t = Table(title="spatial tools")
        for col in ("name", "cost", "tracks", "languages", "description"):
            t.add_column(col)
        for d in defs:
            t.add_row(d.name, d.cost_hint, ",".join(d.tracks) or "*", ",".join(d.languages) or "*", d.description)
        console.print(t)
        return
    try:
        tdef = registry.get_tool(name)
    except KeyError as e:
        raise C.CliError(str(e)) from e
    try:
        arguments = json.loads(args_json)
    except ValueError as e:
        raise C.CliError(f"--json is not valid JSON: {e}") from e
    ws_root = workspace or Path.cwd()
    ws = C.open_workspace(str(ws_root))
    spec = C.load_spec(ws)
    ctx = registry.ToolContext(workspace=ws, round_index=round_index, language=spec.language.value, track=spec.track.value)
    obs = tdef.call(ctx, arguments)
    print_observation(obs, as_json=as_json)
    if not obs.ok:
        raise typer.Exit(code=1)


# ===================================================================== gallery_cmd
gallery_app = typer.Typer(no_args_is_help=True)

RootsArg = Annotated[list[Path] | None, typer.Argument(
    help="run roots (default: ./runs, every ./eval/bench/out/*/runs and every <repo>/eval/bench/out/*/runs that exists)")]


def resolve_roots(roots: list[Path] | None) -> list[Path]:
    """Explicit roots (validated) or the defaults; a clear error when there are none."""
    from codeverse3d.addons.gallery.index import default_roots

    if roots:
        missing = [r for r in roots if not Path(r).is_dir()]
        if missing:
            raise C.CliError("not a directory: " + ", ".join(str(m) for m in missing))
        return [Path(r) for r in roots]
    found = default_roots(Path.cwd())
    seen = {p.resolve() for p in found}
    found += [p for p in sorted((C.EVAL_ROOT / "bench" / "out").glob("*/runs")) if p.is_dir() and p.resolve() not in seen]
    if not found:
        fallback = Path(get_settings().runs_dir)
        if fallback.is_dir():
            return [fallback]
        raise C.CliError(f"no run roots found (looked for runs/ and eval/bench/out/*/runs under {Path.cwd()}, "
                         f"and {C.EVAL_ROOT / 'bench' / 'out'}/*/runs); "
                         f"pass one explicitly: `3dcodeverse gallery serve path/to/runs`")
    return found


@gallery_app.command("serve")
def serve_cmd(
    roots: RootsArg = None,
    port: Annotated[int, typer.Option("--port", min=0, max=65535, help="0 = pick a free port")] = 8765,
    host: Annotated[str | None, typer.Option("--host", help="default 127.0.0.1; any non-loopback address must be "
                                                            "typed here explicitly")] = None,
    open_browser: Annotated[bool, typer.Option("--open/--no-open", help="open the page in a browser")] = True,
    reload: Annotated[bool, typer.Option("--reload", help="re-scan the roots on every page load "
                                                          "(cheap: records only, images stay lazy)")] = False,
    title: Annotated[str, typer.Option("--title")] = "3dcodeverse gallery",
) -> None:
    """Serve the gallery (and the run directories) on localhost."""
    from codeverse3d.addons.gallery.server import GalleryError, serve

    root_paths = resolve_roots(roots)

    def announce(app, url: str) -> None:
        ix = app.index
        console.print(kv_table("gallery", {
            "url": url, "runs": len(ix.entries()), "roots": len(ix.sections),
            "index built in": f"{ix.build_ms} ms", "reload": reload,
            "sections": ", ".join(f"{s.label}({len(s.entries)})" for s in ix.sections)}))
        console.print("[dim]Ctrl-C to stop[/dim]")

    try:
        serve(root_paths, host=host, port=port, reload=reload,
              open_browser=open_browser, title=title, on_start=announce)
    except GalleryError as e:
        raise C.CliError(str(e)) from e
    ok("gallery stopped")


@gallery_app.command("build")
def build_cmd(
    roots: RootsArg = None,
    out: Annotated[Path, typer.Option("--out", help="output .html")] = Path("gallery.html"),
    embed: Annotated[bool, typer.Option("--embed", help="inline the contact sheets as data: URIs so the page can "
                                                        "be shared (much bigger; links still point here)")] = False,
    title: Annotated[str | None, typer.Option("--title")] = None,
    thumb_px: Annotated[int, typer.Option("--thumb-px", min=128, help="embedded thumbnail long edge")] = 720,
) -> None:
    """Write the gallery as one self-contained HTML file."""
    from codeverse3d.addons.gallery.page import build_static

    path, n, index = build_static(resolve_roots(roots), out, title=title, embed=embed, thumb_px=thumb_px)
    broken = sum(1 for e in index.entries() if e.state != "ok")
    if broken:
        warn(f"{broken} run(s) have no usable record.json (shown as broken cards)")
    ok(f"gallery of {n} runs → {path} ({path.stat().st_size // 1024} KB)")
    if not embed:
        console.print("[dim]file:// links; `--embed` inlines the images, `3dcodeverse gallery serve` makes them clickable[/dim]")


app = typer.Typer(
    name="3dcodeverse",
    help="3dcodeverse: LLMs write raw 3D code; the harness builds, judges, refines, records.",
    pretty_exceptions_enable=False,
)
app.add_typer(flywheel_app, name="flywheel", help="Dataset export / pairs / captions.")
app.add_typer(
    gallery_app,
    name="gallery",
    help="Look at runs locally: `serve` on localhost, `build` one shareable HTML file.",
)
app.add_typer(
    cost_app, name="cost", help="Cost audit: per stage/role/model, waste, $ per run."
)
app.add_typer(
    skills_app, name="skills", help="The skill library: list / show / validate / read-rate report."
)
app.command("doctor", help="Environment checks.")(doctor)
app.command(
    "tools",
    help="List spatial tools or run one: `3dcodeverse tools list` | `3dcodeverse tools <name> --json '{...}' --workspace ws`.",
)(tools)

@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context, version: Annotated[bool, typer.Option("--version", is_eager=True)] = False
) -> None:
    # `kill -USR1 <pid>` dumps every thread's stack to stderr (py-spy needs ptrace, which WSL denies)
    if hasattr(signal, "SIGUSR1") and sys.__stderr__ is not None:
        # best-effort: a captured stderr (CliRunner's StringIO) has no fileno
        with contextlib.suppress(Exception):
            faulthandler.register(signal.SIGUSR1, file=sys.__stderr__, all_threads=True)
    if version:
        console.print(f"3dcodeverse {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()
    # build the settings here so a bad value is one typed error, not a traceback from any command
    try:
        get_settings()
    except ValueError as e:
        raise C.CliError(f"bad configuration: {e}", code=2) from e


# --------------------------------------------------------------------------- make / resume
@app.command()
def make(
    prompt: Annotated[str, typer.Argument(help="what to build")],
    track: Annotated[Track, typer.Option("--track")] = Track.STATIC_OBJECT,
    language: Annotated[
        Language | None,
        typer.Option(
            "--language",
            help="default: the track's first language "
            "(blender / urdf_blender / scene_threejs / glsl_shader)",
        ),
    ] = None,
    generator: Annotated[
        str | None,
        typer.Option(
            help="agy:gemini-4-argon[-low|-medium|-high] | claude-code:... | codex:... | single-shot:gemini:..."
        ),
    ] = None,
    planner: Annotated[str | None, typer.Option()] = None,
    judge: Annotated[str | None, typer.Option()] = None,
    image: Annotated[list[Path] | None, typer.Option("--image", help="reference image(s)")] = None,
    variant: Annotated[
        str,
        typer.Option(
            "--variant",
            help="per-model prompt/skill variant (an overlay under codeverse3d/prompts/variants/<name>/ "
            "and codeverse3d/skills/variants/<name>/, frozen on the spec); default: C3D_VARIANT, else "
            "C3D_VARIANTS[<generator>] by the id as typed and then by its served model, else the base",
        ),
    ] = "",
    rounds: Annotated[
        int | None,
        typer.Option(
            "--rounds", min=0, help="refine rounds after the baseline (default: 4)"
        ),
    ] = None,
    max_minutes: Annotated[float | None, typer.Option("--max-minutes", min=0)] = None,
    candidates: Annotated[
        int | None,
        typer.Option(
            "--candidates",
            min=1,
            help="best-of-N baseline: N parallel candidates, keep the best (default: settings.default_candidates or 1)",
        ),
    ] = None,
    slug: Annotated[str | None, typer.Option("--slug")] = None,
    runs_dir: RunsDirOpt = None,
    dim: Annotated[
        list[str] | None, typer.Option("--dim", help="width=1.2 (meters); repeatable")
    ] = None,
    must: Annotated[
        list[str] | None, typer.Option("--must", help="hard requirement; repeatable")
    ] = None,
    must_not: Annotated[list[str] | None, typer.Option("--must-not")] = None,
    style: Annotated[str, typer.Option("--style")] = "",
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    no_pick: Annotated[
        bool,
        typer.Option(
            "--no-pick",
            help="stop after the rounds: no round is picked, no deliverable/ is written "
            "(`3dcodeverse pick <slug>` does it later)",
        ),
    ] = False,
    seed: Annotated[
        int,
        typer.Option(
            "--seed",
            help="a run label, not a sampling seed: another value re-plans (it keys the plan cache); "
            "the models, the coding agent, the judge and the builds (always seed 0) do not read it",
        ),
    ] = 0,
    force: Annotated[bool, typer.Option("--force", help="overwrite an existing run dir")] = False,
    no_run: Annotated[
        bool,
        typer.Option(
            "--no-run",
            help="only create the workspace + spec.json and stop",
        ),
    ] = False,
) -> None:
    """Create a run (workspace + spec.json) and execute the track pipeline."""
    image, dim, must, must_not, tag = image or [], dim or [], must or [], must_not or [], tag or []
    if not prompt.strip():
        raise C.CliError("the prompt is empty: say what to build", code=2)
    C.check_minutes(max_minutes)
    for p in image:
        if not p.is_file():
            raise C.CliError(f"reference image not found: {p}")
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(p) as im:
                im.verify()
        except (UnidentifiedImageError, OSError, SyntaxError) as e:
            raise C.CliError(f"reference image is not a readable image: {p} ({e})") from None
    if language is None:
        language = TRACK_LANGUAGES[track][0]
    run_slug = C.make_slug(prompt, track.value, language.value, slug)
    settings = get_settings()
    if candidates is None:
        candidates = settings.default_candidates
    if candidates > 1 and not TRACK_INFO[track].best_of_n:
        raise _no_best_of_n(track)
    backends = settings.backends(generator=generator, planner=planner, judge=judge)
    C.check_backends(backends)
    # resolved once here and frozen on the spec
    chosen = C.resolve_variant(settings, backends.generator, variant)
    # validate the whole Spec before touching the filesystem (no orphan run directory)
    try:
        spec = Spec(
            id=run_slug,
            track=track,
            language=language,
            prompt=prompt,
            references=[ReferenceImage(path=str(p.resolve())) for p in image],
            constraints=Constraints(
                dimensions_m=C.parse_kv_floats(dim, "--dim") or None,
                must_have=must,
                must_not=must_not,
                style=style,
            ),
            budget=Budget(**{k: v for k, v in
                             {"max_rounds": rounds, "max_minutes": max_minutes}.items() if v is not None}),
            backends=backends,
            options=RunOptions(candidates=candidates, variant=chosen.name),
            seed=seed,
            tags=tag,
        )
    except ValueError as e:
        raise C.CliError(f"invalid run spec: {e}") from e
    # the run mutex: taken before the workspace is created (or --force wipes one), held through the run
    with C.mutating(C.runs_root(runs_dir) / run_slug, what=f"3dcodeverse make {run_slug}", action="create"):
        ws = C.create_workspace(C.runs_root(runs_dir) / run_slug, force=force)
        ws.write_json(ws.spec_path, spec)
        ws.commit("spec")
        console.print(
            kv_table(
                "run",
                {
                    "slug": run_slug,
                    "workspace": ws.root,
                    "track": track.value,
                    "language": language.value,
                    "generator": backends.generator,
                    "judge": f"{backends.judge} n={settings.judge.samples} "
                    f"({settings.judge.max_px}px/{settings.judge.detail_crops}crop)",
                    "rounds": spec.budget.max_rounds,
                    "candidates": candidates,
                    "variant": chosen.label,
                },
            )
        )
        if no_run:
            ok(f"spec written: {ws.spec_path} (not run; `3dcodeverse resume {run_slug}` to start)")
            return
        _run_track(spec, ws, resume=False, candidates=candidates, pick=not no_pick)


def _no_best_of_n(track: Track) -> C.CliError:
    return C.CliError(f"--candidates is for tracks whose baseline is one session; a {track.value} run "
                      "writes its baseline in its pre-round stages")


def _run_track(spec: Spec, ws, *, resume: bool, candidates: int | None = None, force: bool = False,
               pick: bool = True) -> None:
    from codeverse3d.cost.instrument import run_ledger
    from codeverse3d.proc import install_stop_handlers
    from codeverse3d.tracks import get_track

    install_stop_handlers()
    options: dict = {"n_candidates": candidates} if candidates else {}
    try:
        with run_ledger(ws.root, run=ws.root.name):
            record = get_track(spec.track, **options).run(spec, ws, resume=resume, force=force)
    except KeyboardInterrupt:
        raise C.CliError(
            f"interrupted; resume with `3dcodeverse resume {ws.root.name}`", code=130
        ) from None
    except Exception as e:  # the track failed outside its own error handling
        from codeverse3d.orchestrator import StateCorrupt
        from codeverse3d.tracks.lifecycle import SpecChanged

        if isinstance(e, SpecChanged):
            raise C.CliError(str(e), code=2) from None
        if isinstance(e, StateCorrupt):
            raise C.CliError(f"{e} — unreadable run state; move run_state.json aside and resume from the "
                             f"round journal (rounds/)", code=2) from None
        err_console.print_exception(max_frames=8)
        raise C.CliError(
            f"run failed: {type(e).__name__}: {e} (workspace {ws.root}; see events.jsonl)"
        ) from e
    if pick:
        with run_ledger(ws.root, run=ws.root.name):
            _pick_and_package(ws, by="score")
    print_record_summary(record, ws.root)
    if record.status.value == "failed":
        raise typer.Exit(code=1)


def _pick_and_package(ws, *, by: str, round_index: int | None = None,
                      judge_model: str | None = None) -> int | None:
    """Choose the round (or take ``round_index``) and package it."""
    from codeverse3d.addons import select

    idx = round_index if round_index is not None else select.pick(ws.root, by=by, pairwise_model=judge_model)
    if idx is None:
        warn("no round was judged, so none was picked: name one with `3dcodeverse pick <slug> --round N`")
        return None
    try:
        select.package(ws.root, idx, method="round" if round_index is not None else by)
    except ValueError as e:
        raise C.CliError(str(e), code=2) from e
    ok(f"picked round {idx} ({'named' if round_index is not None else f'by {by}'}) → {ws.deliverable}")
    return idx


def _finished_reason(ws, raised: dict) -> str:
    """Why this run must not be re-entered, or "" when resuming it is meaningful.

    A finished run is refused (re-entering re-runs the paid plan stage), except BUDGET and
    MAX_ROUNDS stops whose cap was raised, and AGENT_QUOTA / JUDGE_UNAVAILABLE stops.
    """
    from codeverse3d.contracts.run import RunStatus
    from codeverse3d.orchestrator import RunState, StateCorrupt

    try:
        state = RunState.load(ws)
    except StateCorrupt:
        return ""
    if state is None:
        return ""
    status = state.status
    detail = f"status={status.value}" + (
        f" stop_reason={state.stop_reason!r}" if state.stop_reason not in ("", status.value) else ""
    )
    if status is RunStatus.BUDGET:
        return "" if raised else f"{detail}: raise a cap to continue it (--max-minutes / --rounds)"
    if status is RunStatus.MAX_ROUNDS:
        return "" if "max_rounds" in raised else f"{detail}: raise --rounds to continue it"
    finished = (RunStatus.NO_CHANGE, RunStatus.NO_REFINE_TASKS)
    return detail if status in finished else ""


def _clock_spent(ws, max_minutes: float) -> str:
    """Why a resume would stop at once, or "": the active minutes (kept across ``--force``) are at the cap."""
    from codeverse3d.orchestrator import BudgetSnapshot, RunState, StateCorrupt

    try:
        state = RunState.load(ws)
    except StateCorrupt:
        return ""
    snap = (state.extra.get("budget_snapshot") if state is not None else None) or {}
    used = BudgetSnapshot.model_validate(snap).active_s / 60.0
    return f"its clock is spent ({used:.1f} of {max_minutes:g} active minutes)" if used >= max_minutes else ""


@app.command()
def resume(
    slug: str,
    runs_dir: RunsDirOpt = None,
    candidates: Annotated[
        int | None,
        typer.Option(
            "--candidates", min=1, help="best-of-N baseline width (only matters before round 0 ran)"
        ),
    ] = None,
    max_minutes: Annotated[
        float | None,
        typer.Option("--max-minutes", min=0, help="raise the time cap before resuming"),
    ] = None,
    rounds: Annotated[
        int | None,
        typer.Option("--rounds", min=0, help="new max refine rounds (rewrites spec.json)"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="start the run again from round 0: the old rounds and record are ALWAYS archived "
            "under rounds/pre_force/ (so drifted pre-round stages regenerate); also re-enters a "
            "finished run and allows resuming after a spec edit (the run then re-plans)",
        ),
    ] = False,
    no_pick: Annotated[bool, typer.Option("--no-pick", help="do not pick + package a round afterwards")] = False,
) -> None:
    """Resume an interrupted / partial run (or start a `--no-run` one); a finished run needs --force."""
    C.check_minutes(max_minutes)
    ws = C.open_workspace(slug, runs_dir)
    # the run mutex, before the spec is rewritten
    with C.mutating(ws, what=f"3dcodeverse resume {ws.root.name}"):
        spec = C.load_spec(ws)
        if candidates and candidates > 1 and not TRACK_INFO[spec.track].best_of_n:
            raise _no_best_of_n(spec.track)
        raised = {
            k: v
            for k, v in {"max_minutes": max_minutes, "max_rounds": rounds}.items()
            if v is not None
        }
        if spent := _clock_spent(ws, raised.get("max_minutes", spec.budget.max_minutes)):
            raise C.CliError(f"run {ws.root.name}: {spent} — raise --max-minutes to continue it"
                             + (" (--force keeps the clock: it would archive every round and stop at once)" if force else ""))
        if not force and (why := _finished_reason(ws, raised)):
            raise C.CliError(
                f"run {ws.root.name} already finished ({why}); nothing to resume.  "
                f"`3dcodeverse status {ws.root.name}` to look at it, or --force to re-enter it "
                f"(that re-plans, re-scores and overwrites the final state)."
            )
        if raised:
            from codeverse3d.proc import EventLog

            spec = spec.model_copy(update={"budget": spec.budget.model_copy(update=raised)})
            ws.write_json(ws.spec_path, spec)
            EventLog(ws.events_path).emit("budget.raised", **raised)
        _run_track(spec, ws, resume=True, candidates=candidates, force=force, pick=not no_pick)


@app.command()
def pick(
    slug: str,
    by: Annotated[str, typer.Option("--by", help="score | pairwise (the pairwise judge decides "
                                                 "between the top two when they are within judge noise)")] = "score",
    round_index: Annotated[int | None, typer.Option("--round", min=0, help="hand over this round instead")] = None,
    judge: Annotated[str | None, typer.Option("--judge", help="pairwise judge model (default: the run's judge)")] = None,
    runs_dir: RunsDirOpt = None,
) -> None:
    """Choose the round of a finished run to hand over and package it: deliverable/ + selection.json."""
    from codeverse3d.addons import select
    from codeverse3d.cost.instrument import run_ledger
    from codeverse3d.record.record import RecordError

    if by not in ("score", "pairwise"):
        raise C.CliError(f"--by must be score or pairwise, not {by!r}", code=2)
    if judge and (by != "pairwise" or round_index is not None):
        raise C.CliError("--judge is the pairwise judge: it goes with --by pairwise (and no --round)", code=2)
    ws = C.open_workspace(slug, runs_dir)
    # writes deliverable/ + selection.json: one writer per run dir
    with (C.mutating(ws, what=f"3dcodeverse pick {ws.root.name}", action="pick"),
          run_ledger(ws.root, run=ws.root.name, create=False)):
        try:
            idx = _pick_and_package(ws, by=by, round_index=round_index, judge_model=judge)
            rows = select.round_rows(ws.root)
        except RecordError as e:
            raise C.CliError(str(e), code=2) from e
    if idx is None:
        raise typer.Exit(code=1)
    console.print(C.rounds_table(rows, idx))


# --------------------------------------------------------------------------- render / judge
from codeverse3d.cli.inspect_cmd import judge, render  # noqa: E402

app.command()(render)
app.command()(judge)


# --------------------------------------------------------------------------- mcp
@app.command()
def mcp(workspace: Annotated[Path, typer.Option("--workspace")]) -> None:
    """Serve the spatial tools over stdio MCP."""
    if not workspace.is_dir():
        raise C.CliError(f"workspace not found: {workspace}")
    os.execvp(
        sys.executable,
        [sys.executable, "-m", "codeverse3d.spatial.mcp_server", "--workspace", str(workspace)],
    )


# --------------------------------------------------------------------------- the single-run view (show, status)
from codeverse3d.cli.layout_cmd import show, status  # noqa: E402

app.command()(show)
app.command()(status)


if __name__ == "__main__":  # pragma: no cover
    app()
