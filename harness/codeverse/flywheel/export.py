"""Export run records → dataset sample folders + ``metadata.parquet``.

``export_samples(runs_dir, out_dir)`` writes one sample folder per run (see
``sample.py`` for the layout) and then rebuilds the text index from *every*
``meta.json`` under ``out_dir`` (so repeated exports into the same folder stay
consistent):

* ``<out>/metadata.parquet`` — STORAGE_RULES §4 columns (``id, key, name,
  captions{detailed,instruction,factory}, meta_json, code, tar, byte_start,
  byte_len, n_files``) plus a few queryable extras (``track, language, score,
  passed, generator``).  ``tar``/``byte_*`` are filled by ``pack.py``.
* ``<out>/metadata.jsonl`` — same rows, one JSON object per line.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.run import RunRecord
from codeverse.flywheel import sample as S
from codeverse.flywheel.record import iter_runs
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

PARQUET_NAME = "metadata.parquet"
JSONL_NAME = "metadata.jsonl"
CAPTION_KEYS = ("detailed", "instruction", "factory")


class ExportReport(BaseModel):
    n_runs: int = 0
    n_exported: int = 0
    n_indexed: int = 0
    skipped: dict[str, str] = Field(default_factory=dict, description="run dir → reason")
    exported: list[str] = Field(default_factory=list, description="sample dirs (relative to out_dir)")
    parquet: str = ""
    jsonl: str = ""


def _captions_of(record: RunRecord) -> dict[str, str]:
    caps = record.extra.get("captions") or {}
    return {k: str(caps.get(k, "") or "") for k in CAPTION_KEYS} if caps.get("detailed") else {}


def export_one(ws: Workspace, record: RunRecord, out_dir: Path, *, overwrite: bool = True) -> Path:
    """Write the sample folder for one run; returns the sample dir."""
    key = S.sample_key(ws)
    dest = out_dir / S.sample_rel_dir(record, key)
    rnd = S.best_round_record(record)
    files, code_source = S.code_files_for_round(ws, rnd)
    if not files:
        raise S.SampleError("no code files in src/ (nothing to export)")
    if dest.exists():
        if not overwrite:
            raise S.SampleError(f"sample dir exists: {dest}")
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    entry, written = S.write_code_tree(dest, files, record.spec.language)
    if entry not in written:
        raise S.SampleError(f"entry file {S.ENTRY_BY_LANGUAGE[record.spec.language]} missing at {code_source}")
    renders = S.copy_renders(ws, rnd, dest)
    captions = _captions_of(record)
    (dest / "captions.json").write_text(json.dumps(captions, indent=2, ensure_ascii=False))
    all_files = sorted(written + renders + ["captions.json", "meta.json"])
    meta = S.build_meta(
        ws, record, key=key, entry=entry, files=all_files, renders=renders, rnd=rnd, code_source=code_source
    )
    tmp = dest / "meta.json.tmp"
    tmp.write_text(meta.model_dump_json(indent=2))
    tmp.replace(dest / "meta.json")
    return dest


def export_samples(
    runs_dir: Path | str,
    out_dir: Path | str,
    *,
    min_score: float | None = None,
    only_passed: bool = False,
    best_round: bool = True,
    overwrite: bool = True,
    include_unbuilt: bool = False,
) -> ExportReport:
    """Export every eligible run under ``runs_dir`` into ``out_dir`` and rebuild the index.

    ``best_round`` is the only supported selection (kept as a parameter for
    API stability); the best round is chosen by ``record.best_round`` or the
    highest judged score.  Runs whose best round never built are skipped unless
    ``include_unbuilt`` (failed runs are still useful for repair pairs, not as
    dataset samples).
    """
    if not best_round:
        raise ValueError("export_samples: only best_round=True is supported")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rep = ExportReport()

    def _bad(d: Path, e: Exception) -> None:
        rep.skipped[str(d)] = f"invalid record: {e}"

    for ws, rec in iter_runs(runs_dir, on_error=_bad):
        rep.n_runs += 1
        rnd = S.best_round_record(rec)
        score = rnd.score if rnd is not None else None
        if rnd is None:
            rep.skipped[str(ws.root)] = "no rounds"
            continue
        if not include_unbuilt and not (rnd.build is not None and rnd.build.ok):
            rep.skipped[str(ws.root)] = "best round did not build"
            continue
        if only_passed and not (rnd is not None and rnd.judgment is not None and rnd.judgment.passed):
            rep.skipped[str(ws.root)] = "not passed"
            continue
        if min_score is not None and (score is None or score < min_score):
            rep.skipped[str(ws.root)] = f"score {score} < min_score {min_score}"
            continue
        try:
            dest = export_one(ws, rec, out, overwrite=overwrite)
        except S.SampleError as e:
            rep.skipped[str(ws.root)] = str(e)
            continue
        except Exception as e:  # git errors etc. — report, keep going
            rep.skipped[str(ws.root)] = f"{type(e).__name__}: {e}"
            continue
        rep.n_exported += 1
        rep.exported.append(str(dest.relative_to(out)))
    rows = collect_rows(out)
    rep.n_indexed = len(rows)
    rep.parquet = str(write_parquet(rows, out / PARQUET_NAME))
    rep.jsonl = str(write_jsonl(rows, out / JSONL_NAME))
    return rep


# --------------------------------------------------------------------------- index rows
def row_for_sample(sample_dir: Path) -> dict[str, Any]:
    """One parquet row from a sample folder (reads meta.json / captions.json / entry)."""
    meta_text = (sample_dir / "meta.json").read_text()
    meta = json.loads(meta_text)
    caps_path = sample_dir / "captions.json"
    caps = json.loads(caps_path.read_text()) if caps_path.is_file() else {}
    entry = sample_dir / meta["entry"]
    code = entry.read_text(errors="replace") if entry.is_file() else ""
    n_files = sum(1 for p in sample_dir.rglob("*") if p.is_file())
    return {
        "id": meta["id"],
        "key": meta["key"],
        "name": meta.get("name", ""),
        "captions": {k: str(caps.get(k, "") or "") for k in CAPTION_KEYS},
        "meta_json": meta_text,
        "code": code,
        "tar": "",
        "byte_start": None,
        "byte_len": None,
        "n_files": n_files,
        "track": meta.get("track", ""),
        "language": meta.get("language", ""),
        "score": meta.get("score"),
        "passed": meta.get("passed"),
        "generator": meta.get("generator", ""),
    }


def collect_rows(out_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for meta in sorted(out_dir.rglob("meta.json")):
        try:
            rows.append(row_for_sample(meta.parent))
        except (OSError, ValueError, KeyError) as e:
            log.warning("skipping %s: %s", meta.parent, e)
    return rows


def parquet_schema() -> Any:
    import pyarrow as pa

    return pa.schema(
        [
            ("id", pa.string()),
            ("key", pa.string()),
            ("name", pa.string()),
            ("captions", pa.struct([(k, pa.string()) for k in CAPTION_KEYS])),
            ("meta_json", pa.string()),
            ("code", pa.string()),
            ("tar", pa.string()),
            ("byte_start", pa.int64()),
            ("byte_len", pa.int64()),
            ("n_files", pa.int32()),
            ("track", pa.string()),
            ("language", pa.string()),
            ("score", pa.float64()),
            ("passed", pa.bool_()),
            ("generator", pa.string()),
        ]
    )


def write_parquet(rows: list[dict[str, Any]], path: Path) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(rows, schema=parquet_schema())
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)
    return path


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> Path:
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return path
