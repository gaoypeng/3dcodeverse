"""The dataset generation manifest: what export selected, dropped, and wrote.

``export_samples`` writes ``<out>/dataset_manifest.json`` from its OWN selection
loop — every sample folder it wrote, with a sha256 per file — and ``pack_samples``
consumes ONLY this manifest.  No directory rescan can resurrect a duplicate that
``--drop-duplicates`` removed, and a planted ``src/meta.json`` inside a sample's
LLM-written code tree can never become a phantom row.  Nothing is dropped
silently: every run the selection refused (filters, export errors, de-dup) is
recorded under ``dropped`` with its reason.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from codeverse.contracts.run import RunId

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
