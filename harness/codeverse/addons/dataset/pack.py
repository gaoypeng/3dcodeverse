"""Pack the manifest's sample folders into plain ``samples-NNN.tar`` files and fill
the ``tar / byte_start / byte_len / n_files`` locator columns (STORAGE_RULES §3-4).

``pack_samples`` consumes ONLY ``<out>/dataset_manifest.json`` (written by
``export_samples``) — no directory rescan, so a duplicate dropped at export can
never ship and nothing outside the manifest reaches an archive.  Every file is
sha256-verified against the manifest as it is added (fail fast, naming the file);
the archives AND the two index files are written as ``.tmp`` and moved into place
in one final loop, so a failure anywhere — including in ``write_parquet`` — leaves
the previous archives and index untouched.  Tars left over from a re-pack that
produced fewer shards are removed after that loop.

Each sample's files are written consecutively so ``byte_start..+byte_len`` is a
valid sub-tar; ``verify_locators`` round-trips every row.
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.addons.dataset.export import (
    JSONL_NAME,
    PARQUET_NAME,
    ManifestEntry,
    ManifestError,
    load_manifest,
    row_for_sample,
    write_jsonl,
    write_parquet,
)
from codeverse.flywheel.quality import mark_duplicates

MAX_TAR_BYTES = int(2.5 * 1024**3)
#: per-member allowance for tar headers/padding in the rollover size estimate
TAR_HEADER_SLACK = 1024


class PackError(RuntimeError):
    pass


class PackReport(BaseModel):
    n_samples: int = 0
    tars: list[str] = Field(default_factory=list)
    parquet: str = ""


def _entry_sizes(sdir: Path, entry: ManifestEntry, rels: list[str]) -> int:
    size = 0
    for rel in rels:
        try:
            size += (sdir / rel).stat().st_size
        except OSError as e:
            raise PackError(f"{entry.sample_id}: missing file {rel} ({e}) — re-run export") from None
    return size + TAR_HEADER_SLACK * (len(rels) + 1)


def pack_samples(out_dir: Path | str, *, tar_prefix: str = "", max_tar_bytes: int = MAX_TAR_BYTES) -> PackReport:
    """Write ``samples-NNN.tar`` under ``out_dir`` from the dataset manifest and
    rewrite the index (rows rebuilt from the manifest's sample dirs, locators filled).

    ``tar_prefix`` is prepended to the tar file name in the ``tar`` column so it
    can be repo-root-relative (e.g. ``"3dcodeverse/static_object/"``).
    """
    out = Path(out_dir)
    try:
        manifest = load_manifest(out)
    except ManifestError as e:
        raise PackError(str(e)) from None
    if not manifest.entries:
        raise PackError(f"the manifest under {out} lists no samples (all filtered or dropped)")
    rows: list[dict[str, Any]] = []
    for entry in manifest.entries:
        sdir = out / entry.sample_rel_dir
        try:
            rows.append(row_for_sample(sdir))
        except (OSError, ValueError, KeyError) as e:
            raise PackError(f"{entry.sample_id}: unreadable sample dir {sdir} ({e}) — re-run export") from e
    mark_duplicates(rows)
    rep = PackReport()
    tar_idx = 0
    tar: tarfile.TarFile | None = None
    tmp_to_final: list[tuple[Path, Path]] = []
    final_name = ""

    def _open_new() -> None:
        nonlocal tar, tar_idx, final_name
        if tar is not None:
            tar.close()
        final = out / f"samples-{tar_idx:03d}.tar"
        tmp = out / f"samples-{tar_idx:03d}.tar.tmp"
        tar = tarfile.open(tmp, mode="w", format=tarfile.PAX_FORMAT)  # noqa: SIM115 - closed in _open_new/finally
        tmp_to_final.append((tmp, final))
        final_name = final.name
        rep.tars.append(final.name)
        tar_idx += 1

    done = False
    try:
        _open_new()
        assert tar is not None
        for entry, row in zip(manifest.entries, rows, strict=True):
            sdir = out / entry.sample_rel_dir
            rels = sorted(entry.files)
            if tar.offset > 0 and tar.offset + _entry_sizes(sdir, entry, rels) > max_tar_bytes:
                _open_new()
            start = tar.offset
            for rel in rels:
                p = sdir / rel
                try:
                    data = p.read_bytes()
                except OSError as e:
                    raise PackError(f"{entry.sample_id}: missing file {rel} ({e}) — re-run export") from None
                digest = hashlib.sha256(data).hexdigest()
                if digest != entry.files[rel]:
                    raise PackError(
                        f"{entry.sample_id}: {rel} changed since export (sha256 {digest} != "
                        f"manifest {entry.files[rel]}) — re-run export")
                info = tarfile.TarInfo(name=f"{row['key']}/{rel}")
                info.size = len(data)
                info.mtime = int(p.stat().st_mtime)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))
            row["tar"] = f"{tar_prefix}{final_name}"
            row["byte_start"] = start
            row["byte_len"] = tar.offset - start
            row["n_files"] = len(rels)
            rep.n_samples += 1
        tar.close()
        tar = None
        # the index is published WITH the tars: a failure here used to leave every new
        # tar in place and the old index pointing into it (byte offsets of a re-pack)
        tmp_to_final.append((write_parquet(rows, out / PARQUET_NAME, commit=False), out / PARQUET_NAME))
        tmp_to_final.append((write_jsonl(rows, out / JSONL_NAME, commit=False), out / JSONL_NAME))
        done = True
    finally:
        if not done:  # leave the previous archives and index exactly as they were
            if tar is not None:
                tar.close()
            for tmp, _final in tmp_to_final:
                tmp.unlink(missing_ok=True)
    for tmp, final in tmp_to_final:
        os.replace(tmp, final)
    for stale in out.glob("samples-*.tar"):  # a re-pack with fewer shards must leave no dead tar
        n = stale.name[len("samples-"):-len(".tar")]
        if n.isdigit() and int(n) >= tar_idx:
            stale.unlink()
    rep.parquet = str(out / PARQUET_NAME)
    return rep


def verify_locators(out_dir: Path | str) -> int:
    """Round-trip every row's byte range through ``tarfile``; returns rows checked."""
    out = Path(out_dir)
    import pyarrow.parquet as pq

    table = pq.read_table(out / PARQUET_NAME).to_pylist()
    n = 0
    for row in table:
        if not row["tar"]:
            raise PackError(f"row {row['id']} has no tar locator")
        tpath = out / Path(row["tar"]).name
        with tpath.open("rb") as fh:
            fh.seek(row["byte_start"])
            blob = fh.read(row["byte_len"])
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as sub:
            members = sub.getmembers()
        files = [m for m in members if m.isfile()]
        if len(files) != row["n_files"]:
            raise PackError(f"row {row['id']}: {len(files)} files in range, expected {row['n_files']}")
        if not all(m.name.startswith(row["key"] + "/") for m in files):
            raise PackError(f"row {row['id']}: foreign member in byte range")
        n += 1
    return n
