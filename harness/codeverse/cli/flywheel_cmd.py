"""``c3v flywheel export | pairs | caption | index | dedupe``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

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
) -> None:
    """Export runs → sample folders + metadata.parquet (+ optional plain tars)."""
    from codeverse.flywheel.export import export_samples

    rep = export_samples(runs_dir, out_dir, min_score=min_score, only_passed=only_passed, include_unbuilt=include_unbuilt)
    console.print(kv_table("export", {"runs": rep.n_runs, "exported": rep.n_exported, "indexed": rep.n_indexed,
                                      "skipped": len(rep.skipped), "parquet": rep.parquet}))
    for d, why in list(rep.skipped.items())[:20]:
        warn(f"skip {Path(d).name}: {why}")
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
    """Preference / repair / cross-backend pairs → JSONL."""
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
) -> None:
    """Caption run(s): {detailed, instruction, factory} via a chat model."""
    from codeverse.flywheel.captions import CaptionError, caption_sample
    from codeverse.flywheel.record import iter_runs, load_record

    if all_runs:
        targets = [(ws, rec) for ws, rec in iter_runs(target, on_error=lambda d, e: warn(f"skip {d.name}: {e}"))]
    else:
        ws = C.open_workspace(target, runs_dir)
        targets = [(ws, load_record(ws))]
    done = skipped = failed = 0
    for ws, rec in targets:
        if not force and (rec.extra.get("captions") or {}).get("detailed"):
            skipped += 1
            continue
        try:
            caps = caption_sample(ws, rec, model)
        except CaptionError as e:
            failed += 1
            warn(str(e))
            continue
        done += 1
        console.print(f"[bold]{ws.root.name}[/bold]: {caps.instruction}")
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
    for meta_path in sorted(dataset_dir.rglob("meta.json")):
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
