"""Export run records → dataset sample folders + ``metadata.parquet``."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.proc import read_json_or_none, sha256_file, write_json_atomic, write_text_atomic
from pydantic import BaseModel, Field

from c3d_flywheel.dataset import sample as S
from c3d_flywheel.dataset.quality import DuplicateGroup, code_sha256, mark_duplicates
from c3d_flywheel.legacy import RunId, RunRecord
from c3d_flywheel.record.git_history import CODE_ROOTS
from c3d_flywheel.record.record import FoundRun, iter_runs
from c3d_flywheel.runlayout import RunWorkspace

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
    ws: RunWorkspace, record: RunRecord, captions_dir: Path | None = None, *, slug: str | None = None
) -> dict[str, Any]:
    caps = record.extra.get("captions") or {}
    if caps.get("detailed"):
        return dict(caps)
    candidates = [ws.root / "captions.json"]
    if captions_dir is not None:
        candidates.append(Path(captions_dir) / f"{slug or ws.root.name}.json")
    for p in candidates:
        data = read_json_or_none(p)
        if data is not None and data.get("detailed"):
            return data
    return {}


def _caption_texts(caps: dict[str, Any]) -> dict[str, str]:
    return {k: str(caps.get(k, "") or "") for k in CAPTION_KEYS} if caps.get("detailed") else {}


class ExportedSample(NamedTuple):

    dest: Path
    file_hashes: dict[str, str]
    code_sha256: str
    meta: S.SampleMeta


def export_one(
    ws: RunWorkspace, record: RunRecord, out_dir: Path, *,
    captions_dir: Path | None = None, run_id: RunId | None = None
) -> ExportedSample:
    key = run_id.slug if run_id is not None else S.sample_key(ws)
    dest = out_dir / S.sample_rel_dir(record, key)
    rnd = S.exported_round(ws, record)
    files, code_source = S.code_files_for_round(ws, rnd)
    if not files:
        raise S.SampleError("no code files in src/ (nothing to export)")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    entry, written = S.write_code_tree(dest, files, record.spec.language)
    if entry not in written:
        raise S.SampleError(f"entry file {ENTRY_FILE[record.spec.language]} missing at {code_source}")
    renders = S.copy_renders(ws, rnd, dest)
    meshes = S.copy_link_meshes(ws, rnd, dest) if record.spec.language is Language.URDF_BLENDER else []
    captions = load_captions(ws, record, captions_dir, slug=key)
    write_json_atomic(dest / "captions.json", _caption_texts(captions))
    all_files = sorted(written + renders + meshes + ["captions.json", "meta.json"])
    meta = S.build_meta(
        ws, record, key=key, entry=entry, files=all_files, renders=renders, rnd=rnd, code_source=code_source,
        code=files, captions=captions,
    )
    write_text_atomic(dest / "meta.json", meta.model_dump_json(indent=2))
    hashes = {rel: sha256_file(dest / rel) for rel in all_files}
    return ExportedSample(dest=dest, file_hashes=hashes,
                          code_sha256=code_sha256(files), meta=meta)


def export_samples(
    runs_dir: Path | str,
    out_dir: Path | str,
    *,
    min_score: float | None = None,
    only_passed: bool = False,
    include_unbuilt: bool = False,
    captions_dir: Path | str | None = None,
    drop_duplicates: bool = False,
) -> ExportReport:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rep = ExportReport()

    def _bad(d: Path, e: Exception) -> None:
        rep.skipped[str(d)] = f"invalid record: {e}"

    # Abort on same-pass identity collisions before writing anything (export_one rmtrees its dest);
    # a dest left by an earlier pass stays overwritable.
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
        rnd = S.exported_round(ws, rec)
        j = S.effective_judgment(rnd) if rnd is not None else None
        score = j.overall if j is not None else None
        if rnd is None:
            _skip(ws.root, rid, "no rounds")
            continue
        if not include_unbuilt and not (rnd.build is not None and rnd.build.ok):
            _skip(ws.root, rid, "the exported round did not build")
            continue
        if only_passed and not (j is not None and j.passed):
            _skip(ws.root, rid, "the judge did not pass the exported round")
            continue
        if min_score is not None and (score is None or score < min_score):
            _skip(ws.root, rid, f"score {score} < min_score {min_score}")
            continue
        try:
            exported = export_one(ws, rec, out,
                                  captions_dir=Path(captions_dir) if captions_dir else None, run_id=rid)
        except S.SampleError as e:
            _skip(ws.root, rid, str(e))
            continue
        except Exception as e:
            _skip(ws.root, rid, f"{type(e).__name__}: {e}")
            continue
        rep.n_exported += 1
        rel_dir = exported.dest.relative_to(out).as_posix()
        rep.exported.append(rel_dir)
        entries.append(ManifestEntry(
            run=rid, sample_id=exported.meta.id, sample_rel_dir=rel_dir,
            files=exported.file_hashes, code_fingerprint=exported.meta.code_fingerprint,
            code_sha256=exported.code_sha256))
    # rows come from the manifest's own selection, never a rescan.
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
                                captions_dir=str(captions_dir or "")),
        entries=entries, dropped=dropped), out))
    rep.n_indexed = len(rows)
    for r in rows:
        rep.tiers[r["quality_tier"]] = rep.tiers.get(r["quality_tier"], 0) + 1
    rep.jsonl = str(write_jsonl(rows, out / JSONL_NAME))
    try:
        rep.parquet = str(write_parquet(rows, out / PARQUET_NAME))
    except ImportError:
        # pyarrow is an optional extra: degrade loudly instead of leaving a partial dataset.
        rep.notes.append(f"{PARQUET_NAME} skipped: pyarrow is not installed "
                         f"(pip install -e 'harness[flywheel]'); {JSONL_NAME} has the same rows")
        log.warning("%s", rep.notes[-1])
    return rep


# --------------------------------------------------------------------------- index rows
def row_for_sample(sample_dir: Path) -> dict[str, Any]:
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
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    if not commit:
        return tmp
    tmp.replace(path)
    return path


# ===================================================================== manifest
MANIFEST_NAME = "dataset_manifest.json"


class ManifestError(RuntimeError):
    """The manifest is missing or unreadable."""


class ManifestFilters(BaseModel):

    min_score: float | None = None
    only_passed: bool = False
    include_unbuilt: bool = False
    drop_duplicates: bool = False
    picked_round: bool = Field(default=True, description="each sample is the round addons/select picks")
    captions_dir: str = ""


class ManifestEntry(BaseModel):

    run: RunId
    sample_id: str
    sample_rel_dir: str = Field(description="sample folder relative to the dataset root (posix)")
    files: dict[str, str] = Field(
        description="sample-relative file path → sha256 of the bytes the export wrote")
    code_fingerprint: str = Field(
        default="", description="sha256 of the NORMALISED src tree (quality.code_fingerprint)")
    code_sha256: str = Field(
        default="", description="sha256 of the RAW src tree bytes (quality.code_sha256 — the exact hash)")


class DroppedRun(BaseModel):

    run: RunId
    reason: str = Field(description='filter reason, export error, or "duplicate_of:<sample id>"')


class DatasetGenerationManifest(BaseModel):
    generated_at: datetime | None = Field(
        default=None, description="stamped at write time when not passed in")
    filters: ManifestFilters = Field(default_factory=ManifestFilters)
    entries: list[ManifestEntry] = Field(default_factory=list)
    dropped: list[DroppedRun] = Field(default_factory=list)


def write_manifest(manifest: DatasetGenerationManifest, out_dir: Path | str) -> Path:
    if manifest.generated_at is None:
        manifest = manifest.model_copy(update={"generated_at": datetime.now(UTC)})
    return write_text_atomic(Path(out_dir) / MANIFEST_NAME, manifest.model_dump_json(indent=2))


def load_manifest(out_dir: Path | str) -> DatasetGenerationManifest:
    path = Path(out_dir) / MANIFEST_NAME
    if not path.is_file():
        raise ManifestError(
            f"{path} not found — run export first (c3d-flywheel export writes it): "
            f"pack no longer rescans directories")
    try:
        return DatasetGenerationManifest.model_validate_json(path.read_text())
    except (ValueError, json.JSONDecodeError) as e:
        raise ManifestError(f"{path} is not a valid dataset manifest: {e}") from e
