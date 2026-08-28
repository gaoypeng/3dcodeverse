"""Export run records → dataset sample folders + ``metadata.parquet``.

``export_samples(runs_dir, out_dir)`` writes one sample folder per run (see
``sample.py`` for the layout), records every file it wrote — sha256 each — in
``<out>/dataset_manifest.json`` (``flywheel/manifest.py``) and builds the text
index FROM those manifest entries, never from a directory rescan (so repeated
exports into the same folder stay consistent and nothing the selection did not
choose can leak into the index):

* ``<out>/metadata.parquet`` — STORAGE_RULES §4 columns (``id, key, name,
  captions{detailed,instruction,factory}, meta_json, code, tar, byte_start,
  byte_len, n_files``) plus queryable extras (``track, language, score, passed,
  generator, quality_tier, complexity (+band), gate_errors, cost_usd, rounds, status,
  code_fingerprint, code_sha256, prompt_hash, duplicate_of, near_duplicate_of,
  has_captions``).  ``tar``/``byte_*`` are filled by ``pack.py``; ``duplicate_of``
  (raw hash) and ``near_duplicate_of`` (normalised) by the dedupe pass.
* ``<out>/metadata.jsonl`` — same rows, one JSON object per line.
* ``<out>/duplicates.json`` — the duplicate groups found by the last export.
* ``<out>/dataset_manifest.json`` — the generation manifest: the filters, one
  hashed entry per exported sample, and every dropped run with its reason
  (``duplicate_of:<id>`` for de-duplicated rows).  ``pack.py`` consumes ONLY this.

Captions come from ``record.extra["captions"]``, else ``<ws>/captions.json``,
else ``<captions_dir>/<slug>.json`` (side-car written by ``3dcv flywheel caption --out``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from pydantic import BaseModel, Field

from codeverse.contracts.common import ENTRY_FILE, Language
from codeverse.contracts.run import RunId, RunRecord
from codeverse.flywheel import sample as S
from codeverse.flywheel._git import CODE_ROOTS
from codeverse.flywheel.quality import DuplicateGroup, code_sha256, mark_duplicates
from codeverse.flywheel.record import FoundRun, iter_runs
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

PARQUET_NAME = "metadata.parquet"
JSONL_NAME = "metadata.jsonl"
DUPLICATES_NAME = "duplicates.json"
CAPTION_KEYS = ("detailed", "instruction", "factory")


class ExportReport(BaseModel):
    n_runs: int = 0
    n_exported: int = 0
    n_indexed: int = 0
    n_duplicates: int = Field(default=0, description="rows marked duplicate_of (dropped when drop_duplicates)")
    skipped: dict[str, str] = Field(default_factory=dict, description="run dir → reason")
    exported: list[str] = Field(default_factory=list, description="sample dirs (relative to out_dir)")
    duplicates: list[DuplicateGroup] = Field(default_factory=list)
    tiers: dict[str, int] = Field(default_factory=dict, description="quality tier → count (indexed rows)")
    parquet: str = ""
    jsonl: str = ""
    manifest: str = ""
    notes: list[str] = Field(default_factory=list,
                             description="non-fatal degradations, e.g. parquet skipped for a missing extra")


def load_captions(
    ws: Workspace, record: RunRecord, captions_dir: Path | None = None, *, slug: str | None = None
) -> dict[str, Any]:
    """Full captions dict (text fields + provenance) from record.extra, ``<ws>/captions.json``
    or ``<captions_dir>/<slug>.json``; ``{}`` when the run is not captioned.

    ``slug`` is the run's :class:`~codeverse.contracts.run.RunId` slug; without it the
    side-car falls back to the directory basename (correct only for flat layouts —
    every nested battery run is named ``run`` and would read a stranger's captions)."""
    caps = record.extra.get("captions") or {}
    if caps.get("detailed"):
        return dict(caps)
    candidates = [ws.root / "captions.json"]
    if captions_dir is not None:
        candidates.append(Path(captions_dir) / f"{slug or ws.root.name}.json")
    for p in candidates:
        if p.is_file():
            try:
                data = json.loads(p.read_text())
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("detailed"):
                return data
    return {}


def _caption_texts(caps: dict[str, Any]) -> dict[str, str]:
    return {k: str(caps.get(k, "") or "") for k in CAPTION_KEYS} if caps.get("detailed") else {}


class ExportedSample(NamedTuple):
    """What :func:`export_one` wrote: the sample dir plus the hashes the manifest needs."""

    dest: Path
    file_hashes: dict[str, str]
    code_sha256: str
    meta: S.SampleMeta


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export_one(
    ws: Workspace, record: RunRecord, out_dir: Path, *, overwrite: bool = True,
    captions_dir: Path | None = None, run_id: RunId | None = None
) -> ExportedSample:
    """Write the sample folder for one run; returns the dir plus a sha256 per file
    written (every byte in the folder passes through here, so it is hashed here).

    ``run_id`` carries the collision-safe identity minted at discovery time; without
    one the key falls back to the directory basename (flat layouts only)."""
    key = run_id.slug if run_id is not None else S.sample_key(ws)
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
        raise S.SampleError(f"entry file {ENTRY_FILE[record.spec.language]} missing at {code_source}")
    renders = S.copy_renders(ws, rnd, dest)
    meshes = S.copy_link_meshes(ws, dest) if record.spec.language is Language.URDF_BLENDER else []
    textured = S.copy_textured(ws, record, dest)
    captions = load_captions(ws, record, captions_dir, slug=key)
    (dest / "captions.json").write_text(json.dumps(_caption_texts(captions), indent=2, ensure_ascii=False))
    all_files = sorted(written + renders + meshes + textured + ["captions.json", "meta.json"])
    meta = S.build_meta(
        ws, record, key=key, entry=entry, files=all_files, renders=renders, rnd=rnd, code_source=code_source,
        code=files, captions=captions,
    )
    tmp = dest / "meta.json.tmp"
    tmp.write_text(meta.model_dump_json(indent=2))
    tmp.replace(dest / "meta.json")
    hashes = {rel: _sha256_file(dest / rel) for rel in all_files}
    return ExportedSample(dest=dest, file_hashes=hashes,
                          code_sha256=code_sha256(files), meta=meta)


def export_samples(
    runs_dir: Path | str,
    out_dir: Path | str,
    *,
    min_score: float | None = None,
    only_passed: bool = False,
    best_round: bool = True,
    overwrite: bool = True,
    include_unbuilt: bool = False,
    captions_dir: Path | str | None = None,
    drop_duplicates: bool = False,
) -> ExportReport:
    """Export every eligible run under ``runs_dir`` into ``out_dir`` and rebuild the index.

    ``best_round`` is the only supported selection (kept as a parameter for
    API stability); the best round is chosen by ``record.best_round`` or the
    highest judged score.  Runs whose best round never built are skipped unless
    ``include_unbuilt`` (failed runs are still useful for repair pairs, not as
    dataset samples).  Byte-identical duplicates (same raw ``code_sha256`` + prompt)
    are marked ``duplicate_of`` and, with ``drop_duplicates``, left out of the index
    AND of the manifest entries, recorded under ``manifest.dropped`` as
    ``duplicate_of:<id>`` (folders stay on disk).  Normalised duplicates are only
    MARKED (``near_duplicate_of``) — never dropped: whitespace inside a string
    literal is not a duplicate.
    """
    if not best_round:
        raise ValueError("export_samples: only best_round=True is supported")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rep = ExportReport()

    def _bad(d: Path, e: Exception) -> None:
        rep.skipped[str(d)] = f"invalid record: {e}"

    # Fail FAST on identity collisions: two run dirs whose samples would land in the
    # same folder must abort before anything is written — the rmtree-before-write in
    # export_one is how 79 nested-battery records used to collapse into ONE sample.
    # A dest left by an EARLIER pass stays overwritable; only same-pass duplicates raise.
    found: list[FoundRun] = list(iter_runs(runs_dir, on_error=_bad))
    dest_owner: dict[str, Path] = {}
    for ws, rec, rid in found:
        rel = str(S.sample_rel_dir(rec, rid.slug))
        prev = dest_owner.get(rel)
        if prev is not None:
            raise S.SampleError(
                f"duplicate sample id {rel!r}: {prev} and {ws.root} would export onto each other")
        dest_owner[rel] = ws.root

    entries: list[ManifestEntry] = []
    dropped: list[DroppedRun] = []

    def _skip(ws_root: Path, rid: RunId, reason: str) -> None:
        rep.skipped[str(ws_root)] = reason
        dropped.append(DroppedRun(run=rid, reason=reason))

    for ws, rec, rid in found:
        rep.n_runs += 1
        rnd = S.best_round_record(rec)
        j = S.effective_judgment(rnd) if rnd is not None else None
        score = j.overall if j is not None else None
        if rnd is None:
            _skip(ws.root, rid, "no rounds")
            continue
        if not include_unbuilt and not (rnd.build is not None and rnd.build.ok):
            _skip(ws.root, rid, "best round did not build")
            continue
        if only_passed and not (j is not None and j.passed):
            _skip(ws.root, rid, "not passed")
            continue
        if min_score is not None and (score is None or score < min_score):
            _skip(ws.root, rid, f"score {score} < min_score {min_score}")
            continue
        try:
            exported = export_one(ws, rec, out, overwrite=overwrite,
                                  captions_dir=Path(captions_dir) if captions_dir else None, run_id=rid)
        except S.SampleError as e:
            _skip(ws.root, rid, str(e))
            continue
        except Exception as e:  # git errors etc. — report, keep going
            _skip(ws.root, rid, f"{type(e).__name__}: {e}")
            continue
        rep.n_exported += 1
        rel_dir = exported.dest.relative_to(out).as_posix()
        rep.exported.append(rel_dir)
        entries.append(ManifestEntry(
            run=rid, sample_id=exported.meta.id, sample_rel_dir=rel_dir,
            files=exported.file_hashes, code_fingerprint=exported.meta.code_fingerprint,
            code_sha256=exported.code_sha256))
    # rows come from the manifest's own selection, never a rescan — a rescan is how
    # dropped duplicates used to ship and how a planted src/meta.json became a row
    rows = [row_for_sample(out / e.sample_rel_dir) for e in entries]
    rep.duplicates = mark_duplicates(rows)
    rep.n_duplicates = sum(len(g.duplicates) for g in rep.duplicates)
    (out / DUPLICATES_NAME).write_text(json.dumps([g.model_dump() for g in rep.duplicates], indent=2))
    if drop_duplicates:
        dup_of = {str(r["id"]): str(r["duplicate_of"]) for r in rows if r["duplicate_of"]}
        rows = [r for r in rows if not r["duplicate_of"]]
        kept: list[ManifestEntry] = []
        for e in entries:
            canonical = dup_of.get(e.sample_id)
            if canonical:
                dropped.append(DroppedRun(run=e.run, reason=f"duplicate_of:{canonical}"))
            else:
                kept.append(e)
        entries = kept
    rep.manifest = str(write_manifest(DatasetGenerationManifest(
        filters=ManifestFilters(min_score=min_score, only_passed=only_passed,
                                include_unbuilt=include_unbuilt, drop_duplicates=drop_duplicates,
                                best_round=best_round, captions_dir=str(captions_dir or "")),
        entries=entries, dropped=dropped), out))
    rep.n_indexed = len(rows)
    for r in rows:
        rep.tiers[r["quality_tier"]] = rep.tiers.get(r["quality_tier"], 0) + 1
    # JSONL first, and unconditionally: it carries the same rows with no third-party
    # dependency, so a core-only install still gets a complete, indexed dataset.
    rep.jsonl = str(write_jsonl(rows, out / JSONL_NAME))
    try:
        rep.parquet = str(write_parquet(rows, out / PARQUET_NAME))
    except ImportError:
        # docs/INSTALL.md §4 scopes pyarrow to the `flywheel` extra and promises the
        # harness only raises at the moment the named feature is used.  Crashing here
        # left a partial dataset on disk (samples written, no index at all) and the
        # rows were already computed — degrade loudly instead.
        rep.notes.append(f"{PARQUET_NAME} skipped: pyarrow is not installed "
                         f"(pip install -e 'harness[flywheel]'); {JSONL_NAME} has the same rows")
        log.warning("%s", rep.notes[-1])
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
        "quality_tier": str(meta.get("quality_tier") or "D"),
        "complexity": meta.get("complexity"),
        "complexity_band": str(meta.get("complexity_band") or ""),
        "gate_errors": int(meta.get("gate_errors") or 0),
        "cost_usd": float(meta.get("cost_usd") or 0.0),
        "rounds": int(meta.get("rounds") or 0),
        "status": str(meta.get("status") or ""),
        "code_fingerprint": str(meta.get("code_fingerprint") or ""),
        "code_sha256": code_sha256({rel: (sample_dir / rel).read_bytes()
                                    for rel in meta.get("files", []) if rel.split("/")[0] in CODE_ROOTS}),
        "prompt_hash": str(meta.get("prompt_hash") or ""),
        "duplicate_of": "",
        "near_duplicate_of": "",
        "has_captions": bool(caps.get("detailed")),
    }


def collect_rows(out_dir: Path) -> list[dict[str, Any]]:
    """Recovery/debug rescan of a dataset folder — export/pack build their rows from
    the manifest, never from this.  Exactly three levels deep (track/language/key,
    the shape ``sample_rel_dir`` has), so an LLM-written ``src/meta.json`` inside a
    sample's code tree can never inject a phantom row."""
    rows = []
    for meta in sorted(out_dir.glob("*/*/*/meta.json")):
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
            ("quality_tier", pa.string()),
            ("complexity", pa.float64()),
            ("complexity_band", pa.string()),
            ("gate_errors", pa.int32()),
            ("cost_usd", pa.float64()),
            ("rounds", pa.int32()),
            ("status", pa.string()),
            ("code_fingerprint", pa.string()),
            ("code_sha256", pa.string()),
            ("prompt_hash", pa.string()),
            ("duplicate_of", pa.string()),
            ("near_duplicate_of", pa.string()),
            ("has_captions", pa.bool_()),
        ]
    )


def write_parquet(rows: list[dict[str, Any]], path: Path, *, commit: bool = True) -> Path:
    """One row per sample, in :func:`parquet_schema` order.  ``commit=False`` leaves the
    file at ``<path>.tmp`` and returns it, so a caller can publish it with other files.

    ``from_pylist(schema=...)`` DROPS any key the schema does not name, silently: that is
    how ``complexity`` / ``complexity_band`` — filled by :func:`row_for_sample`, kept in
    metadata.jsonl, and advertised in this module's docstring — went missing from the
    parquet, so every query of the dataset by complexity band returned nothing.  The
    schema and the row keys are asserted equal here so a future column cannot vanish
    the same way.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = parquet_schema()
    if rows:
        missing = set(rows[0]) - set(schema.names)
        if missing:
            raise ValueError(f"parquet_schema() is missing column(s) written by row_for_sample: {sorted(missing)}")
    table = pa.Table.from_pylist(rows, schema=schema)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd")
    if not commit:
        return tmp
    tmp.replace(path)
    return path


def write_jsonl(rows: list[dict[str, Any]], path: Path, *, commit: bool = True) -> Path:
    """``commit=False``: leave ``<path>.tmp`` and return it (see :func:`write_parquet`)."""
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    if not commit:
        return tmp
    tmp.replace(path)
    return path


# ===================================================================== manifest
# (merged from codeverse/flywheel/manifest.py, 2026-08-28)
MANIFEST_NAME = "dataset_manifest.json"


class ManifestError(RuntimeError):
    """The manifest is missing or unreadable."""


class ManifestFilters(BaseModel):
    """The selection knobs the export ran with (provenance for the dataset)."""

    min_score: float | None = None
    only_passed: bool = False
    include_unbuilt: bool = False
    drop_duplicates: bool = False
    best_round: bool = True
    captions_dir: str = ""


class ManifestEntry(BaseModel):
    """One exported sample folder, hashed file by file at write time."""

    run: RunId
    sample_id: str
    sample_rel_dir: str = Field(description="sample folder relative to the dataset root (posix)")
    files: dict[str, str] = Field(
        description="sample-relative file path → sha256 of the bytes the export wrote")
    code_fingerprint: str = Field(
        default="", description="sha256 of the NORMALISED src tree (dedupe.code_fingerprint)")
    code_sha256: str = Field(
        default="", description="sha256 of the RAW src tree bytes (dedupe.code_sha256 — the exact hash)")


class DroppedRun(BaseModel):
    """A run the selection refused — nothing is dropped silently."""

    run: RunId
    reason: str = Field(description='filter reason, export error, or "duplicate_of:<sample id>"')


class DatasetGenerationManifest(BaseModel):
    generated_at: datetime | None = Field(
        default=None, description="stamped at write time when not passed in")
    filters: ManifestFilters = Field(default_factory=ManifestFilters)
    entries: list[ManifestEntry] = Field(default_factory=list)
    dropped: list[DroppedRun] = Field(default_factory=list)


def write_manifest(manifest: DatasetGenerationManifest, out_dir: Path | str) -> Path:
    """Atomic write (tmp + replace) of ``<out_dir>/dataset_manifest.json``."""
    if manifest.generated_at is None:
        manifest = manifest.model_copy(update={"generated_at": datetime.now(UTC)})
    path = Path(out_dir) / MANIFEST_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(manifest.model_dump_json(indent=2))
    tmp.replace(path)
    return path


def load_manifest(out_dir: Path | str) -> DatasetGenerationManifest:
    """Read ``<out_dir>/dataset_manifest.json``; missing or invalid is a hard error."""
    path = Path(out_dir) / MANIFEST_NAME
    if not path.is_file():
        raise ManifestError(
            f"{path} not found — run export first (3dcv flywheel export writes it): "
            f"pack no longer rescans directories")
    try:
        return DatasetGenerationManifest.model_validate_json(path.read_text())
    except (ValueError, json.JSONDecodeError) as e:
        raise ManifestError(f"{path} is not a valid dataset manifest: {e}") from e
