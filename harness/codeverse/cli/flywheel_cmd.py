"""``3dcv flywheel export | pairs | caption | index | dedupe | gallery``."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from codeverse.cli import _common as C
from codeverse.cli._fmt import console, kv_table, ok, warn

flywheel_app = typer.Typer(no_args_is_help=True)


@flywheel_app.command("export")
def export_cmd(
    runs_dir: Annotated[Path, typer.Argument(help="runs root")],
    out_dir: Annotated[Path, typer.Argument(help="dataset folder")],
    min_score: Annotated[float | None, typer.Option("--min-score")] = None,
    only_passed: Annotated[bool, typer.Option("--only-passed")] = False,
    pack: Annotated[bool, typer.Option("--pack", help="also pack samples-NNN.tar + byte-range locators")] = False,
    tar_prefix: Annotated[str, typer.Option("--tar-prefix", help="repo-root-relative prefix for the tar column")] = "",
    include_unbuilt: Annotated[bool, typer.Option("--include-unbuilt", help="also export runs whose best round never built")] = False,
    captions_dir: Annotated[Path | None, typer.Option("--captions-dir", help="side-car captions written by `caption --out`")] = None,
    drop_duplicates: Annotated[bool, typer.Option("--drop-duplicates", help="leave byte-identical duplicates (raw code sha256 + prompt) out of the index, manifest and tars (recorded under the manifest's dropped); normalised ones are only marked near_duplicate_of")] = False,
) -> None:
    """Export runs → sample folders + dataset_manifest.json + metadata.jsonl/.parquet
    (+ optional plain tars, packed FROM the manifest)."""
    from codeverse.flywheel.export import export_samples

    if pack:
        # pack_samples rewrites the index and reads it back with pyarrow, so --pack
        # cannot degrade the way a plain export can.  Say so BEFORE the first sample is
        # written rather than after a few hundred, with a raw ModuleNotFoundError.
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            # escaped: rich reads "[flywheel]" as markup and would print the tag away
            raise C.CliError(escape("--pack needs the flywheel extra: "
                                    "pip install -e 'harness[flywheel]'")) from None

    rep = export_samples(runs_dir, out_dir, min_score=min_score, only_passed=only_passed, include_unbuilt=include_unbuilt,
                         captions_dir=captions_dir, drop_duplicates=drop_duplicates)
    tiers = " ".join(f"{t}:{rep.tiers.get(t, 0)}" for t in "ABCD")
    console.print(kv_table("export", {"runs": rep.n_runs, "exported": rep.n_exported, "indexed": rep.n_indexed,
                                      "duplicates": rep.n_duplicates, "tiers": tiers,
                                      "skipped": len(rep.skipped), "manifest": rep.manifest,
                                      "jsonl": rep.jsonl,
                                      "parquet": rep.parquet or "(skipped: no pyarrow)"}))
    for note in rep.notes:
        warn(escape(note))
    for d, why in list(rep.skipped.items())[:20]:
        warn(f"skip {Path(d).name}: {why}")
    for g in rep.duplicates[:20]:
        warn(f"duplicate of {g.canonical}: {', '.join(g.duplicates)}")
    if pack:
        from codeverse.flywheel.pack import pack_samples, verify_locators

        prep = pack_samples(out_dir, tar_prefix=tar_prefix)
        n = verify_locators(out_dir)
        ok(f"packed {prep.n_samples} samples into {prep.tars}; verified {n} locators")


@flywheel_app.command("pairs")
def pairs_cmd(
    runs_dir: Annotated[Path, typer.Argument()],
    out_jsonl: Annotated[Path, typer.Argument()],
    min_delta: Annotated[float, typer.Option("--min-delta")] = 0.05,
) -> None:
    """Preference / repair (round + in-session) / cross-backend pairs → JSONL."""
    from codeverse.flywheel.pairs import build_pairs

    n = build_pairs(runs_dir, out_jsonl, min_delta=min_delta)
    ok(f"{n} pairs → {out_jsonl}")


@flywheel_app.command("caption")
def caption_cmd(
    target: Annotated[str, typer.Argument(help="run slug/dir, or a runs root with --all")],
    model: Annotated[str, typer.Option("--model")] = "gemini:gemini-3.7-flash",
    all_runs: Annotated[bool, typer.Option("--all", help="caption every un-captioned run under target")] = False,
    force: Annotated[bool, typer.Option("--force", help="re-caption even if captions exist")] = False,
    runs_dir: Annotated[Path | None, typer.Option("--runs-dir")] = None,
    out: Annotated[Path | None, typer.Option("--out", help="write side-car <out>/<slug>.json instead of touching the run (read-only runs)")] = None,
) -> None:
    """Caption run(s): {detailed, instruction, factory} via a chat model."""
    from codeverse.cost.instrument import run_ledger
    from codeverse.flywheel.captions import CaptionError, caption_sample
    from codeverse.flywheel.export import load_captions
    from codeverse.flywheel.record import iter_runs, load_record
    from codeverse.runlock import RunLocked, exclusive

    if all_runs:
        targets = [(fr.ws, fr.record, fr.run_id.slug)
                   for fr in iter_runs(target, on_error=lambda d, e: warn(f"skip {d.name}: {e}"))]
    else:
        ws = C.open_workspace(target, runs_dir)
        targets = [(ws, load_record(ws), None)]  # no scan root → basename identity
    done = skipped = failed = 0
    for ws, rec, slug in targets:
        if not force and load_captions(ws, rec, out, slug=slug).get("detailed"):
            skipped += 1
            continue
        # PER RUN: captioning rewrites record.json (--out writes a side-car instead and
        # touches nothing), and a locked run is skipped with a warning — one live run must
        # not abort a --all batch of hundreds
        lock = nullcontext() if out else exclusive(ws.root, what=f"3dcv flywheel caption {ws.root.name}")
        try:
            # the priced captioner call joins the run's ledger (as `3dcv judge` does)
            with lock, run_ledger(ws.root, run=ws.root.name, create=False):
                caps = caption_sample(ws, rec, model, out_dir=out, slug=slug)
        except (CaptionError, RunLocked) as e:
            failed += 1
            warn(str(e))
            continue
        done += 1
        console.print(f"[bold]{slug or ws.root.name}[/bold]: {caps.instruction}")
    ok(f"captioned {done}, skipped {skipped} (already), failed {failed}")
    if failed and not done:
        raise typer.Exit(code=1)


@flywheel_app.command("index")
def index_cmd(
    runs_dir: Annotated[Path, typer.Argument()],
    out_sqlite: Annotated[Path, typer.Argument()] = Path("runs_index.sqlite"),
    show: Annotated[bool, typer.Option("--summary/--no-summary")] = True,
) -> None:
    """Build the SQLite index (runs / rounds / usage) and print a summary."""
    from codeverse.flywheel.index import build_index, summary

    n = build_index(runs_dir, out_sqlite)
    ok(f"indexed {n} runs → {out_sqlite}")
    if show and n:
        from rich.table import Table

        t = Table(title="summary")
        for col in ("track", "language", "generator", "n", "pass", "baseline", "final", "Δ", "cost"):
            t.add_column(col)
        for r in summary(out_sqlite):
            t.add_row(r["track"], r["language"], r["generator"], str(r["n"]),
                      f"{(r['pass_rate'] or 0):.2f}", f"{(r['baseline_mean'] or 0):.3f}", f"{(r['final_mean'] or 0):.3f}",
                      f"{(r['delta_mean'] or 0):+.3f}", f"${(r['cost_usd'] or 0):.2f}")
        console.print(t)


@flywheel_app.command("dedupe")
def dedupe_cmd(
    dataset_dir: Annotated[Path, typer.Argument(help="exported dataset folder")],
    mesh: Annotated[bool, typer.Option("--mesh/--no-mesh", help="also fingerprint renders/object.glb")] = True,
    threshold: Annotated[float, typer.Option("--threshold")] = 0.9,
) -> None:
    """Report near-duplicate sample groups (code fingerprint + mesh voxel Jaccard)."""
    import json

    from codeverse.flywheel.dedupe import (
        DedupeItem,
        code_fingerprint,
        mesh_fingerprint,
        near_duplicates,
    )

    items = []
    # exactly track/language/key deep — a src/meta.json in a sample's LLM-written
    # code tree must not become a phantom sample
    for meta_path in sorted(dataset_dir.glob("*/*/*/meta.json")):
        sdir = meta_path.parent
        meta = json.loads(meta_path.read_text())
        files = {p.relative_to(sdir).as_posix(): p.read_bytes() for p in (sdir / "src").rglob("*") if p.is_file()}
        fp = None
        glb = sdir / "renders" / "object.glb"
        if mesh and glb.is_file():
            try:
                fp = mesh_fingerprint(glb)
            except Exception as e:  # unreadable glb → code-only
                warn(f"{meta['key']}: mesh fingerprint failed: {e}")
        items.append(DedupeItem(id=meta["id"], code_fp=code_fingerprint(files), mesh_fp=fp))
    groups = near_duplicates(items, mesh_threshold=threshold)
    console.print(f"{len(items)} samples, {len(groups)} duplicate groups")
    for g in groups:
        console.print("  " + "  ==  ".join(g))


@flywheel_app.command("gallery")
def gallery_cmd(
    runs_dir: Annotated[Path, typer.Argument(help="runs root")],
    out_html: Annotated[Path, typer.Argument(help="output .html (self-contained)")],
    title: Annotated[str | None, typer.Option("--title")] = None,
    thumb_px: Annotated[int, typer.Option("--thumb-px", min=128, help="thumbnail long edge")] = 640,
    embed: Annotated[bool, typer.Option("--embed/--no-embed", help="inline the contact sheets as data: URIs")] = True,
) -> None:
    """Alias of `3dcv gallery build` (kept for scripts): one self-contained HTML page."""
    from codeverse.gallery.static_site import build_static

    path, n, _ = build_static([runs_dir], out_html, title=title, embed=embed, thumb_px=thumb_px)
    ok(f"gallery of {n} runs → {path} ({path.stat().st_size // 1024} KB)")
    console.print("[dim]`3dcv gallery serve` serves the same page with working links[/dim]")
