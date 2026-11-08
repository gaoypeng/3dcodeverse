"""``c3d-flywheel`` — the command line over recorded v0.0.x runs: ``show`` / ``status`` / ``pick``,
``cost``, ``gallery serve|build``, ``export`` / ``pairs`` / ``refine``.  Heavy modules import lazily
so ``--help`` stays fast."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from c3d_flywheel import __version__
from c3d_flywheel.cli import _common as C
from c3d_flywheel.cli._common import RunsDirOpt, console, kv_table, ok, warn
from c3d_flywheel.cli.cost_cmd import cost_app
from c3d_flywheel.cli.flywheel_cmd import export_cmd, pairs_cmd, refine_cmd
from c3d_flywheel.cli.layout_cmd import show, status

# ===================================================================== gallery
gallery_app = typer.Typer(no_args_is_help=True)

RootsArg = Annotated[list[Path] | None, typer.Argument(
    help="run roots (default: ./runs and every ./eval/bench/out/*/runs that exists)")]
RuntimeJsOpt = Annotated[Path | None, typer.Option(
    "--runtime-js", help="the plugin's runtime_js/ (vendored three.js for the GLB viewer; default: next to the "
                         "installed codeverse3d package)")]


def resolve_roots(roots: list[Path] | None) -> list[Path]:
    """Explicit roots (validated) or the defaults; a clear error when there are none."""
    from c3d_flywheel.gallery.index import default_roots

    if roots:
        missing = [r for r in roots if not Path(r).is_dir()]
        if missing:
            raise C.CliError("not a directory: " + ", ".join(str(m) for m in missing))
        return [Path(r) for r in roots]
    found = default_roots(Path.cwd())
    if not found:
        raise C.CliError(f"no run roots found (looked for runs/ and eval/bench/out/*/runs under {Path.cwd()}); "
                         f"pass one explicitly: `c3d-flywheel gallery serve path/to/runs`")
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
    runtime_js: RuntimeJsOpt = None,
) -> None:
    """Serve the gallery (and the run directories) on localhost."""
    from c3d_flywheel.gallery.server import GalleryError, serve

    root_paths = resolve_roots(roots)

    def announce(app, url: str) -> None:
        ix = app.index
        console.print(kv_table("gallery", {
            "url": url, "runs": len(ix.entries()), "roots": len(ix.sections),
            "index built in": f"{ix.build_ms} ms", "reload": reload,
            "sections": ", ".join(f"{s.label}({len(s.entries)})" for s in ix.sections)}))
        console.print("[dim]Ctrl-C to stop[/dim]")

    try:
        serve(root_paths, host=host, port=port, reload=reload, open_browser=open_browser, title=title,
              runtime_js=runtime_js, on_start=announce)
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
    from c3d_flywheel.gallery.page import build_static

    path, n, index = build_static(resolve_roots(roots), out, title=title, embed=embed, thumb_px=thumb_px)
    broken = sum(1 for e in index.entries() if e.state != "ok")
    if broken:
        warn(f"{broken} run(s) have no usable record.json (shown as broken cards)")
    ok(f"gallery of {n} runs → {path} ({path.stat().st_size // 1024} KB)")
    if not embed:
        console.print("[dim]file:// links; `--embed` inlines the images, `c3d-flywheel gallery serve` makes them clickable[/dim]")


# ===================================================================== pick
def pick(
    slug: str,
    round_index: Annotated[int | None, typer.Option("--round", min=0, help="hand over this round instead")] = None,
    runs_dir: RunsDirOpt = None,
) -> None:
    """Choose the round of a finished run to hand over and package it: deliverable/ + selection.json."""
    from c3d_flywheel import select
    from c3d_flywheel.record.record import RecordError

    ws = C.open_workspace(slug, runs_dir)
    try:
        idx = round_index if round_index is not None else select.pick(ws.root)
        if idx is None:
            warn("no round was judged, so none was picked: name one with `c3d-flywheel pick <slug> --round N`")
            raise typer.Exit(code=1)
        select.package(ws.root, idx, method="round" if round_index is not None else "score")
        rows = select.round_rows(ws.root)
    except RecordError as e:
        raise C.CliError(str(e), code=2) from e
    except ValueError as e:
        raise C.CliError(str(e), code=2) from e
    ok(f"picked round {idx} ({'named' if round_index is not None else 'by score'}) → {ws.deliverable}")
    console.print(C.rounds_table(rows, idx))


# ===================================================================== the app
app = typer.Typer(
    name="c3d-flywheel",
    help="c3d-flywheel: read the runs 3dcodeverse v0.0.x recorded — show, pick, cost, gallery, dataset export.",
    pretty_exceptions_enable=False,
)
app.command()(show)
app.command()(status)
app.command()(pick)
app.add_typer(cost_app, name="cost", help="Cost audit: per stage/role/model, waste, $ per run.")
app.add_typer(gallery_app, name="gallery",
              help="Look at runs locally: `serve` on localhost, `build` one shareable HTML file.")
app.command("export")(export_cmd)
app.command("pairs")(pairs_cmd)
app.command("refine")(refine_cmd)


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context, version: Annotated[bool, typer.Option("--version", is_eager=True)] = False
) -> None:
    if version:
        console.print(f"c3d-flywheel {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


if __name__ == "__main__":  # pragma: no cover
    app()
