"""``3dcode flywheel export | pairs | caption | index``."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from codeverse3d.addons.dataset.pairs import MIN_PREFERENCE_DELTA, build_pairs
from codeverse3d.cli import _common as C
from codeverse3d.cli._common import console, kv_table, ok, warn
from codeverse3d.config import get_settings

flywheel_app = typer.Typer(no_args_is_help=True)


@flywheel_app.command("export")
def export_cmd(
    runs_dir: Annotated[Path, typer.Argument(help="runs root")],
    out_dir: Annotated[Path, typer.Argument(help="dataset folder")],
    min_score: Annotated[float | None, typer.Option("--min-score")] = None,
    only_passed: Annotated[bool, typer.Option("--only-passed")] = False,
    pack: Annotated[bool, typer.Option("--pack", help="also pack samples-NNN.tar + byte-range locators")] = False,
    tar_prefix: Annotated[str, typer.Option("--tar-prefix", help="repo-root-relative prefix for the tar column")] = "",
    include_unbuilt: Annotated[bool, typer.Option("--include-unbuilt", help="also export runs whose exported round never built")] = False,
    captions_dir: Annotated[Path | None, typer.Option("--captions-dir", help="side-car captions written by `caption --out`")] = None,
    drop_duplicates: Annotated[bool, typer.Option("--drop-duplicates", help="leave byte-identical duplicates (raw code sha256 + prompt) out of the index, manifest and tars (recorded under the manifest's dropped); normalised ones are only marked near_duplicate_of")] = False,
) -> None:
    """Export runs → sample folders + dataset_manifest.json + metadata.jsonl/.parquet
    (+ optional plain tars, packed FROM the manifest)."""
    from codeverse3d.addons.dataset.export import export_samples

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
        from codeverse3d.addons.dataset.pack import pack_samples, verify_locators

        prep = pack_samples(out_dir, tar_prefix=tar_prefix)
        n = verify_locators(out_dir)
        ok(f"packed {prep.n_samples} samples into {prep.tars}; verified {n} locators")


@flywheel_app.command("pairs")
def pairs_cmd(
    runs_dir: Annotated[Path, typer.Argument()],
    out_jsonl: Annotated[Path, typer.Argument()],
    min_delta: Annotated[float, typer.Option("--min-delta")] = MIN_PREFERENCE_DELTA,
) -> None:
    """Preference / repair (round-to-round) / cross-backend pairs → JSONL."""
    n = build_pairs(runs_dir, out_jsonl, min_delta=min_delta)
    ok(f"{n} pairs → {out_jsonl}")


@flywheel_app.command("refine")
def refine_cmd(
    runs_dir: Annotated[Path, typer.Argument()],
    out_jsonl: Annotated[Path, typer.Argument()],
    with_code: Annotated[bool, typer.Option("--with-code", help="inline the changed files, so a "
                                            "format converter needs no access to the runs")] = False,
) -> None:
    """Refine rounds as transitions (brief → diff → score delta) → JSONL."""
    from codeverse3d.addons.dataset.refine import build_refine

    n, drops = build_refine(runs_dir, out_jsonl, with_code=with_code)
    ok(f"{n} transitions → {out_jsonl}" + (f"; not exported: {dict(drops)}" if drops else ""))


@flywheel_app.command("caption")
def caption_cmd(
    target: Annotated[str, typer.Argument(help="run slug/dir, or a runs root with --all")],
    model: Annotated[str | None, typer.Option("--model", help="default: Settings.default_captioner")] = None,
    all_runs: Annotated[bool, typer.Option("--all", help="caption every un-captioned run under target")] = False,
    force: Annotated[bool, typer.Option("--force", help="re-caption even if captions exist")] = False,
    runs_dir: Annotated[Path | None, typer.Option("--runs-dir")] = None,
    out: Annotated[Path | None, typer.Option("--out", help="write side-car <out>/<slug>.json instead of touching the run (read-only runs)")] = None,
) -> None:
    """Caption run(s): {detailed, instruction, factory} via a chat model."""
    from codeverse3d.addons.dataset.captions import CaptionError, caption_sample
    from codeverse3d.addons.dataset.export import load_captions
    from codeverse3d.cost.instrument import run_ledger
    from codeverse3d.proc import RunLocked, exclusive
    from codeverse3d.record.record import iter_runs, load_record

    model = model or get_settings().default_captioner
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
        lock = nullcontext() if out else exclusive(ws.root, what=f"3dcode flywheel caption {ws.root.name}")
        try:
            # the priced captioner call joins the run's ledger (as `3dcode judge` does)
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
    from codeverse3d.addons.dataset.index import build_index, summary

    n = build_index(runs_dir, out_sqlite)
    ok(f"indexed {n} runs → {out_sqlite}")
    if show and n:
        from rich.table import Table

        t = Table(title="summary")
        for col in ("track", "language", "generator", "n", "baseline", "picked", "Δ", "cost"):
            t.add_column(col)
        for r in summary(out_sqlite):
            t.add_row(r["track"], r["language"], r["generator"], str(r["n"]),
                      f"{(r['baseline_mean'] or 0):.3f}", f"{(r['picked_mean'] or 0):.3f}",
                      f"{(r['delta_mean'] or 0):+.3f}", f"${(r['cost_usd'] or 0):.2f}")
        console.print(t)
