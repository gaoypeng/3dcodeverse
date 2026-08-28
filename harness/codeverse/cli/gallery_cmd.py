"""``3dcv gallery serve | build`` — look at runs locally.

``serve`` is the one you want day to day: it indexes every run root, serves the
page on loopback and serves the run directories themselves, so every link on
every card actually opens (sheet, renders, record.json, src/, GLB, GIFs).
``build`` writes the same page as a single file for sharing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from codeverse.cli import _common as C
from codeverse.cli._common import console, kv_table, ok, warn

gallery_app = typer.Typer(no_args_is_help=True)

RootsArg = Annotated[list[Path] | None, typer.Argument(
    help="run roots (default: ./runs plus every ./bench/out/*/runs that exists)")]


def resolve_roots(roots: list[Path] | None) -> list[Path]:
    """Explicit roots (validated) or the defaults; a clear error when there are none."""
    from codeverse.gallery.index import default_roots

    if roots:
        missing = [r for r in roots if not Path(r).is_dir()]
        if missing:
            raise C.CliError("not a directory: " + ", ".join(str(m) for m in missing))
        return [Path(r) for r in roots]
    found = default_roots(Path.cwd())
    if not found:
        from codeverse.config import get_settings

        fallback = Path(get_settings().runs_dir)
        if fallback.is_dir():
            return [fallback]
        raise C.CliError(f"no run roots found under {Path.cwd()} (looked for runs/ and bench/out/*/runs); "
                         f"pass one explicitly: `3dcv gallery serve path/to/runs`")
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
    title: Annotated[str, typer.Option("--title")] = "3dcv gallery",
) -> None:
    """Serve the gallery (and the run directories) on localhost."""
    from codeverse.gallery.server import GalleryError, serve

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
    from codeverse.gallery.page import build_static

    path, n, index = build_static(resolve_roots(roots), out, title=title, embed=embed, thumb_px=thumb_px)
    broken = sum(1 for e in index.entries() if e.state != "ok")
    if broken:
        warn(f"{broken} run(s) have no usable record.json (shown as broken cards)")
    ok(f"gallery of {n} runs → {path} ({path.stat().st_size // 1024} KB)")
    if not embed:
        console.print("[dim]file:// links; `--embed` inlines the images, `3dcv gallery serve` makes them clickable[/dim]")
