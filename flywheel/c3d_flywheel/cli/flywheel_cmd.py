"""``c3d-flywheel export | pairs | refine``: recorded runs → dataset rows."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from c3d_flywheel.cli import _common as C
from c3d_flywheel.cli._common import console, kv_table, ok, warn
from c3d_flywheel.dataset.pairs import MIN_PREFERENCE_DELTA, build_pairs


def export_cmd(
    runs_dir: Annotated[Path, typer.Argument(help="runs root")],
    out_dir: Annotated[Path, typer.Argument(help="dataset folder")],
    min_score: Annotated[float | None, typer.Option("--min-score")] = None,
    only_passed: Annotated[bool, typer.Option("--only-passed")] = False,
    pack: Annotated[bool, typer.Option("--pack", help="also pack samples-NNN.tar + byte-range locators")] = False,
    tar_prefix: Annotated[str, typer.Option("--tar-prefix", help="repo-root-relative prefix for the tar column")] = "",
    include_unbuilt: Annotated[bool, typer.Option("--include-unbuilt", help="also export runs whose exported round never built")] = False,
    captions_dir: Annotated[Path | None, typer.Option("--captions-dir", help="side-car captions (<dir>/<slug>.json) written by the v0.0.x captioner")] = None,
    drop_duplicates: Annotated[bool, typer.Option("--drop-duplicates", help="leave byte-identical duplicates (raw code sha256 + prompt) out of the index, manifest and tars (recorded under the manifest's dropped); normalised ones are only marked near_duplicate_of")] = False,
) -> None:
    """Export runs → sample folders + dataset_manifest.json + metadata.jsonl/.parquet (+ tars)."""
    from c3d_flywheel.dataset.export import export_samples

    if pack:
        # --pack reads the index back with pyarrow: fail before the first sample is written
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            # escaped: rich reads "[parquet]" as markup
            raise C.CliError(escape("--pack needs the parquet extra: pip install 'c3d-flywheel[parquet]'")) from None

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
        from c3d_flywheel.dataset.pack import pack_samples, verify_locators

        prep = pack_samples(out_dir, tar_prefix=tar_prefix)
        n = verify_locators(out_dir)
        ok(f"packed {prep.n_samples} samples into {prep.tars}; verified {n} locators")


def pairs_cmd(
    runs_dir: Annotated[Path, typer.Argument()],
    out_jsonl: Annotated[Path, typer.Argument()],
    min_delta: Annotated[float, typer.Option("--min-delta")] = MIN_PREFERENCE_DELTA,
) -> None:
    """Preference / repair (round-to-round) / cross-backend pairs → JSONL."""
    n = build_pairs(runs_dir, out_jsonl, min_delta=min_delta)
    ok(f"{n} pairs → {out_jsonl}")


def refine_cmd(
    runs_dir: Annotated[Path, typer.Argument()],
    out_jsonl: Annotated[Path, typer.Argument()],
    with_code: Annotated[bool, typer.Option("--with-code", help="inline the changed files, so a "
                                            "format converter needs no access to the runs")] = False,
) -> None:
    """Refine rounds as transitions (brief → diff → score delta) → JSONL."""
    from c3d_flywheel.dataset.refine import build_refine

    n, drops = build_refine(runs_dir, out_jsonl, with_code=with_code)
    ok(f"{n} transitions → {out_jsonl}" + (f"; not exported: {dict(drops)}" if drops else ""))
