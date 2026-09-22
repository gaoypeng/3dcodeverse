"""``3dcode`` — the 3dcodeverse command line.

Thin by design: every command builds typed inputs and calls into the harness
packages through function-local imports, so ``3dcode --help`` loads none of them.

Owns the typer app and every registration, the run-starting commands ``make`` /
``resume`` (spec building, reference grounding, track dispatch, budget raising, and the
hand-over after the run), ``pick`` and ``mcp``.  ``render`` / ``judge`` live in the sibling
``cli/inspect_cmd.py``, the single-run view ``show`` (and its ``status`` alias) in
``cli/layout_cmd.py``; all are registered here.
"""

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
from codeverse3d.cli.doctor import doctor_app
from codeverse3d.cli.flywheel_cmd import flywheel_app
from codeverse3d.cli.skills_cmd import skills_app
from codeverse3d.cli.texture_cmd import texture_app
from codeverse3d.config import get_settings
from codeverse3d.contracts.common import TRACK_LANGUAGES, Budget, Language, Track
from codeverse3d.contracts.spec import Constraints, ReferenceImage, RunOptions, Spec


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
    help="run roots (default: ./runs, every ./bench/out/*/runs and every <repo>/eval/bench/out/*/runs that exists)")]


def resolve_roots(roots: list[Path] | None) -> list[Path]:
    """Explicit roots (validated) or the defaults; a clear error when there are none."""
    from codeverse3d.addons.gallery.index import default_roots

    if roots:
        missing = [r for r in roots if not Path(r).is_dir()]
        if missing:
            raise C.CliError("not a directory: " + ", ".join(str(m) for m in missing))
        return [Path(r) for r in roots]
    found = default_roots(Path.cwd())
    seen = {p.resolve() for p in found}  # D77: the batteries live in <repo>/eval/bench/out/<b>/runs
    found += [p for p in sorted((C.EVAL_ROOT / "bench" / "out").glob("*/runs")) if p.is_dir() and p.resolve() not in seen]
    if not found:
        from codeverse3d.config import get_settings

        fallback = Path(get_settings().runs_dir)
        if fallback.is_dir():
            return [fallback]
        raise C.CliError(f"no run roots found (looked for runs/ and bench/out/*/runs under {Path.cwd()}, "
                         f"and {C.EVAL_ROOT / 'bench' / 'out'}/*/runs); "
                         f"pass one explicitly: `3dcode gallery serve path/to/runs`")
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
    title: Annotated[str, typer.Option("--title")] = "3dcode gallery",
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
        serve(root_paths, host=host, host_explicit=host is not None, port=port, reload=reload,
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
        console.print("[dim]file:// links; `--embed` inlines the images, `3dcode gallery serve` makes them clickable[/dim]")


app = typer.Typer(
    name="3dcode",
    help="3dcodeverse: LLMs write raw 3D code; the harness builds, judges, refines, records.",
    pretty_exceptions_enable=False,
)
app.add_typer(flywheel_app, name="flywheel", help="Dataset export / pairs / captions / index.")
app.add_typer(
    gallery_app,
    name="gallery",
    help="Look at runs locally: `serve` on localhost, `build` one shareable HTML file.",
)
app.add_typer(
    cost_app, name="cost", help="Cost audit: per stage/role/model, waste, $ per run."
)
app.add_typer(doctor_app, name="doctor", help="Environment checks.")
app.add_typer(
    skills_app, name="skills", help="The skill library: list / show / validate / read-rate report."
)
app.add_typer(
    texture_app, name="texture", help="Text-to-image texturing: object pass / scene pack."
)
app.command(
    "tools",
    help="List spatial tools or run one: `3dcode tools list` | `3dcode tools <name> --json '{...}' --workspace ws`.",
)(tools)

@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context, version: Annotated[bool, typer.Option("--version", is_eager=True)] = False
) -> None:
    # `kill -USR1 <pid>` dumps every thread's stack to stderr (the run log): the
    # 2026-08-28 scope_tj_r3 hang (futex wait, every socket CLOSE-WAIT, 41 min past
    # its window) was undiagnosable without it — py-spy needs ptrace rights WSL denies.
    if hasattr(signal, "SIGUSR1") and sys.__stderr__ is not None:
        # best-effort: a captured stderr (CliRunner's StringIO) has no fileno
        with contextlib.suppress(Exception):
            faulthandler.register(signal.SIGUSR1, file=sys.__stderr__, all_threads=True)
    if version:
        console.print(f"3dcode {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()
    # Build the settings HERE so a bad configuration value is one typed error instead of a
    # raw traceback out of whichever command happened to touch get_settings() first: the app
    # runs with pretty_exceptions_enable=False, and `C3D_PROFILE=bogus` used to dump a
    # Python stack from every command — including `3dcode doctor`, the one you would run to
    # find out what is wrong with your configuration.
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
            help="gemini-cli:gemini-3.6-flash | claude-code:... | codex:... | agy:... | single-shot:gemini:..."
        ),
    ] = None,
    planner: Annotated[str | None, typer.Option()] = None,
    judge: Annotated[str | None, typer.Option()] = None,
    captioner: Annotated[str | None, typer.Option()] = None,
    image: Annotated[list[Path] | None, typer.Option("--image", help="reference image(s)")] = None,
    reference: Annotated[
        bool,
        typer.Option(
            "--reference/--no-reference",
            help="reference grounding: synthesize a "
            "neutral studio product shot of the prompt with the image model, validate it, and use it as "
            "the fidelity anchor for planning, the compare_reference tool, the silhouette gate and the "
            "judge. Ignored when --image is given (your own references always win); ~$0.15 for 2 views",
        ),
    ] = False,
    reference_views: Annotated[
        int,
        typer.Option(
            "--reference-views",
            min=1,
            max=4,
            help="how many reference views to "
            "synthesize (1 = 3/4 only, 2 = + straight front elevation)",
        ),
    ] = 2,
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            help="cost/quality dial: economy | balanced | quality "
            "(sets models, judge samples, rounds, candidates, turn cap, "
            "montage size and the texture pass together)",
        ),
    ] = None,
    rounds: Annotated[
        int | None,
        typer.Option(
            "--rounds", min=0, help="refine rounds after the baseline (default: the profile's)"
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
    texture: Annotated[
        bool,
        typer.Option(
            "--texture",
            help="texture the picked round after the run (frozen on spec.options)",
        ),
    ] = False,
    no_pick: Annotated[
        bool,
        typer.Option(
            "--no-pick",
            help="stop after the rounds: no round is picked, no deliverable/ is written "
            "(`3dcode pick <slug>` does it later)",
        ),
    ] = False,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    force: Annotated[bool, typer.Option("--force", help="overwrite an existing run dir")] = False,
    no_run: Annotated[
        bool,
        typer.Option(
            "--no-run",
            help="only create the workspace + spec.json and stop "
            "(with --reference the reference pass still runs first: it makes model calls and writes "
            "the grounded spec)",
        ),
    ] = False,
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
    texture_arg = texture  # the dial folds the profile in below; remember what the USER asked
    # ONE resolver for the whole dial, so `--profile X` and `C3D_PROFILE=X` land the same
    # values (they used to disagree on candidates + texture); an explicit flag beats both.
    dial = C.resolve_dial(
        settings,
        profile,
        rounds=rounds,
        candidates=candidates,
        max_minutes=max_minutes,
        texture=texture,
    )
    rounds, max_minutes = dial.rounds, dial.max_minutes
    candidates, texture = dial.candidates, dial.texture
    # A run must not record and display a pass it cannot run.  `3dcode texture pass` already
    # refuses non-object tracks; `3dcode make` accepted --texture (and --profile quality,
    # which forces it) on scene/graphics, froze it on the spec, printed "texture True",
    # and then emitted a spurious texture.failed at finalise because there is no GLB.
    from codeverse3d.texturing.run import texture_supported

    if texture and not texture_supported(track):
        if texture_arg:  # the user asked for it explicitly: say no, and say where to go
            hint = " — use `3dcode texture scene-pack`" if track is Track.SCENE else ""
            raise C.CliError(
                f"--texture is for object tracks; {track.value} runs have no GLB to texture{hint}"
            )
        texture = False  # profile-implied: quality simply has no texture pass on this track
    backends = settings.backends(
        generator=generator, planner=planner, judge=judge, captioner=captioner
    )
    # validate the whole Spec BEFORE touching the filesystem: an invalid
    # track/language combination must not leave an orphan run directory behind.
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
            budget=Budget(max_rounds=rounds, max_minutes=max_minutes),
            # options.profile records the dial this run resolved to, whichever way it was
            # named (flag, C3D_PROFILE, config.yaml), so `3dcode resume` re-applies it
            backends=backends,
            options=RunOptions(candidates=candidates, texture=texture, profile=dial.profile),
            seed=seed,
            tags=tag,
        )
    except ValueError as e:
        raise C.CliError(f"invalid run spec: {e}") from e
    # THE run mutex, taken BEFORE the workspace is created (or --force wipes one) and
    # held through the paid reference call and the run itself: everything below mutates
    # this run directory, and two 3dcode on one slug corrupt each other (runlock.py).
    with C.mutating(C.runs_root(runs_dir) / run_slug, what=f"3dcode make {run_slug}", action="create"):
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
                    "profile": f"{dial.profile} ({C.active_profile(settings).expectation()})",
                    "judge": f"{backends.judge} n={dial.judge_samples} "
                    f"({dial.judge_max_px}px/{dial.judge_detail_crops}crop)",
                    "rounds": rounds,
                    "candidates": candidates,
                    "texture": texture,
                },
            )
        )
        if reference:
            # --reference runs even under --no-run, because the grounded spec IS the artifact
            # it produces (reference/run.py writes it back to spec.json) — but the combination
            # is otherwise read as "filesystem only", so say out loud that this part spends
            # money and can block on a degraded provider before anything is written.
            if no_run:
                warn(
                    f"--reference makes model calls now (1 planner call + {reference_views} image generation(s) "
                    f"+ {reference_views} vision check(s), ~$0.15 for 2 views) — --no-run stops after that"
                )
            spec = _ground_in_reference(spec, ws, n_views=reference_views)
        if no_run:
            ok(f"spec written: {ws.spec_path} (not run; `3dcode resume {run_slug}` to start)")
            return
        _run_track(spec, ws, resume=False, candidates=candidates, pick=not no_pick)


def _ground_in_reference(spec: Spec, ws, *, n_views: int) -> Spec:
    """`--reference`: synthesize + validate a reference image and attach it to the spec.

    Never fatal — a run that cannot get a usable reference simply runs without one.
    """
    from codeverse3d.cost import Role, Stage, call_context
    from codeverse3d.cost.instrument import run_ledger
    from codeverse3d.proc import EventLog
    from codeverse3d.reference import ground_spec

    events = EventLog(ws.events_path)
    with (
        run_ledger(ws.root, run=ws.root.name),
        call_context(stage=Stage.PLAN, role=Role.PLANNER, label="reference"),
    ):
        grounded, refset, why = ground_spec(spec, ws, n_views=n_views, events=events)
    (ok if grounded is not spec else warn)(f"reference grounding: {why}")
    for v in refset.views:
        detail = (
            v.verdict.failure()
            if v.verdict and not v.accepted
            else (Path(v.path).name if v.path else "-")
        )
        if v.accepted and v.dimension_conflict:
            got = f"{v.aspect:.2f}" if v.aspect else "?"
            detail += f"  (aspect {got} disagrees with the stated dimensions — shape target only)"
        console.print(f"  {'KEPT    ' if v.accepted else 'REJECTED'} {v.view}: {detail}")
    if refset.usage.cost_usd:
        console.print(f"  reference cost ${refset.usage.cost_usd:.4f} ({refset.source})")
    return grounded


def _run_track(spec: Spec, ws, *, resume: bool, candidates: int | None = None, force: bool = False,
               pick: bool = True) -> None:
    from codeverse3d.cost.instrument import run_ledger
    from codeverse3d.tracks import get_track

    options: dict = {"n_candidates": candidates} if candidates else {}
    options.update(C.round_policy_options(spec))
    try:
        # already inside the run mutex (make / resume take it); every model call and
        # agent session of this run lands in telemetry/cost.jsonl
        with run_ledger(ws.root, run=ws.root.name):
            record = get_track(spec.track, **options).run(spec, ws, resume=resume, force=force)
    except KeyboardInterrupt:
        raise C.CliError(
            f"interrupted; resume with `3dcode resume {ws.root.name}`", code=130
        ) from None
    except Exception as e:  # the track failed outside its own error handling
        from codeverse3d.tracks.lifecycle import SpecChanged

        if isinstance(e, SpecChanged):
            # a refusal with instructions, not a crash: no traceback, and the run
            # itself was never entered (reconcile_resume raises before any stage)
            raise C.CliError(str(e), code=2) from None
        err_console.print_exception(max_frames=8)
        raise C.CliError(
            f"run failed: {type(e).__name__}: {e} (workspace {ws.root}; see events.jsonl)"
        ) from e
    if pick:  # the hand-over: the best-scored round → deliverable/ (+ its texture pass)
        with run_ledger(ws.root, run=ws.root.name):
            _pick_and_package(ws, by="score", texture=_texture_requested(spec))
    print_record_summary(record, ws.root)
    if record.status.value == "failed":
        raise typer.Exit(code=1)


def _texture_requested(spec: Spec) -> bool:
    from codeverse3d.texturing.run import texture_requested

    return texture_requested(spec)


def _pick_and_package(ws, *, by: str, texture: bool, round_index: int | None = None,
                      judge_model: str | None = None) -> int | None:
    """``codeverse3d.addons.select``: choose the round (or take ``round_index``) and package it."""
    from codeverse3d.addons import select

    idx = round_index if round_index is not None else select.pick(ws.root, by=by, pairwise_model=judge_model)
    if idx is None:
        warn("no round was judged, so none was picked: name one with `3dcode pick <slug> --round N`")
        return None
    try:
        select.package(ws.root, idx, texture=texture, method="round" if round_index is not None else by)
    except ValueError as e:
        raise C.CliError(str(e), code=2) from e
    ok(f"picked round {idx} ({'named' if round_index is not None else f'by {by}'}) → {ws.deliverable}")
    sel = select.load_selection(ws)
    if texture and sel is not None and not sel.textured:
        warn("the texture pass did not ship a pack for this round (see `3dcode texture show` / events.jsonl)")
    return idx


def _finished_reason(ws, raised: dict) -> str:
    """Why this run must not be re-entered, or "" when resuming it is meaningful.

    A run that reached a terminal state has nothing to resume, and re-entering it is
    destructive, not idempotent: `3dcode resume` on a finished run re-ran the plan stage
    as a real billed model call and rewrote run_state.status back to 'planning', leaving
    a finished run stuck mid-pipeline.  Two stops are the documented exceptions — raising
    the cap a run stopped on is how you continue it: BUDGET (any raised cap) and
    MAX_ROUNDS (a raised ``--rounds``; the run goes on from its last round).
    """
    from codeverse3d.contracts.run import RunStatus
    from codeverse3d.orchestrator import RunState, StateCorrupt

    try:
        state = RunState.load(ws)
    except StateCorrupt:
        return ""  # let the track report it the way it always has
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
    finished = (RunStatus.STOPPED, RunStatus.NO_CHANGE, RunStatus.NO_REFINE_TASKS, RunStatus.JUDGE_UNAVAILABLE)
    return detail if status in finished else ""


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
            help="re-enter a run that already finished (it will be re-planned and re-scored), "
            "and allow resuming after a spec edit — the old rounds and record are archived "
            "under rounds/pre_force/ and the run re-plans from the edited spec",
        ),
    ] = False,
    no_pick: Annotated[bool, typer.Option("--no-pick", help="do not pick + package a round afterwards")] = False,
) -> None:
    """Resume an interrupted / partial run (or start a `--no-run` one).

    ``--max-minutes`` / ``--rounds`` rewrite the spec's budget
    first — the only way to continue a BUDGET-stopped run.  A run that already
    reached a terminal state is refused unless ``--force``: re-entering it spends
    money and overwrites its final state."""
    ws = C.open_workspace(slug, runs_dir)
    # the run mutex, before the spec is rewritten (a budget raise is a mutation)
    with C.mutating(ws, what=f"3dcode resume {ws.root.name}"):
        spec = C.load_spec(ws)
        if (
            spec.options.profile
        ):  # the dial the run was created with (judge samples, montage px, turn cap)
            try:
                get_settings().apply_profile(spec.options.profile, force=True)
            except ValueError as e:  # a spec.json naming a profile this build no longer has
                raise C.CliError(f"{ws.spec_path}: {e}", code=2) from e
        raised = {
            k: v
            for k, v in {"max_minutes": max_minutes, "max_rounds": rounds}.items()
            if v is not None
        }
        if not force and (why := _finished_reason(ws, raised)):
            raise C.CliError(
                f"run {ws.root.name} already finished ({why}); nothing to resume.  "
                f"`3dcode status {ws.root.name}` to look at it, or --force to re-enter it "
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
    texture: Annotated[bool, typer.Option("--texture", help="texture the picked round first (object tracks)")] = False,
    judge: Annotated[str | None, typer.Option("--judge", help="pairwise judge model (default: the run's judge)")] = None,
    runs_dir: RunsDirOpt = None,
) -> None:
    """Choose the round of a finished run to hand over and package it: deliverable/ + selection.json."""
    from codeverse3d.addons import select
    from codeverse3d.cost.instrument import run_ledger
    from codeverse3d.record.record import RecordError

    if by not in ("score", "pairwise"):
        raise C.CliError(f"--by must be score or pairwise, not {by!r}", code=2)
    ws = C.open_workspace(slug, runs_dir)
    # writes deliverable/ + selection.json (a texture pass or a pairwise verdict is paid):
    # one writer per run dir, and the money joins the run's ledger
    with (C.mutating(ws, what=f"3dcode pick {ws.root.name}", action="pick"),
          run_ledger(ws.root, run=ws.root.name, create=False)):
        try:
            idx = _pick_and_package(ws, by=by, texture=texture, round_index=round_index, judge_model=judge)
            rows = select.round_rows(ws.root)
        except RecordError as e:
            raise C.CliError(str(e), code=2) from e
    if idx is None:
        raise typer.Exit(code=1)
    console.print(kv_table("rounds (* = picked)", {
        f"r{r.index:02d}{' *' if r.index == idx else ''}": f"{r.kind:<9} score {C.fmt_score(r.score)}  "
        f"gate errors {r.gate_errors}  {C.fmt_usd(r.cost_usd)}" for r in rows}))


# --------------------------------------------------------------------------- render / judge
from codeverse3d.cli.inspect_cmd import judge, render  # noqa: E402

app.command()(render)
app.command()(judge)


# --------------------------------------------------------------------------- mcp
@app.command()
def mcp(workspace: Annotated[Path, typer.Option("--workspace")]) -> None:
    """Serve the spatial tools over stdio MCP (exec `python -m codeverse3d.spatial.mcp_server`)."""
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
