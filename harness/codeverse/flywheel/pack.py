"""Pack exported sample folders into plain ``samples-NNN.tar`` files and fill
the ``tar / byte_start / byte_len / n_files`` locator columns (STORAGE_RULES §3-4).

Each sample's files are written consecutively so ``byte_start..+byte_len`` is a
valid sub-tar; ``verify_locators`` round-trips every row.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.flywheel.export import (
    JSONL_NAME,
    PARQUET_NAME,
    collect_rows,
    write_jsonl,
    write_parquet,
)

MAX_TAR_BYTES = int(2.5 * 1024**3)


class PackError(RuntimeError):
    pass


class PackReport(BaseModel):
    n_samples: int = 0
    tars: list[str] = Field(default_factory=list)
    parquet: str = ""


def _sample_dir_for(out_dir: Path, meta: dict[str, Any]) -> Path:
    return out_dir / meta["track"] / meta["language"] / meta["key"]


def pack_samples(out_dir: Path | str, *, tar_prefix: str = "", max_tar_bytes: int = MAX_TAR_BYTES) -> PackReport:
    """Write ``samples-NNN.tar`` under ``out_dir`` and rewrite the index with locators.

    ``tar_prefix`` is prepended to the tar file name in the ``tar`` column so it
    can be repo-root-relative (e.g. ``"3dcodeverse/static_object/"``).
    """
    out = Path(out_dir)
    rows = collect_rows(out)
    if not rows:
        raise PackError(f"no samples under {out}")
    rep = PackReport()
    tar_idx = 0
    tar: tarfile.TarFile | None = None
    tar_path: Path | None = None

    def _open_new() -> None:
        nonlocal tar, tar_path, tar_idx
        if tar is not None:
            tar.close()
        tar_path = out / f"samples-{tar_idx:03d}.tar"
        tar = tarfile.open(tar_path, mode="w", format=tarfile.PAX_FORMAT)
        rep.tars.append(tar_path.name)
        tar_idx += 1

    _open_new()
    assert tar is not None and tar_path is not None
    for row in rows:
        meta = json.loads(row["meta_json"])
        sdir = _sample_dir_for(out, meta)
        files = sorted(p for p in sdir.rglob("*") if p.is_file())
        size = sum(p.stat().st_size for p in files) + 1024 * (len(files) + 1)
        if tar.offset > 0 and tar.offset + size > max_tar_bytes:
            _open_new()
        start = tar.offset
        for p in files:
            tar.add(p, arcname=f"{meta['key']}/{p.relative_to(sdir).as_posix()}", recursive=False)
        row["tar"] = f"{tar_prefix}{tar_path.name}"
        row["byte_start"] = start
        row["byte_len"] = tar.offset - start
        row["n_files"] = len(files)
        rep.n_samples += 1
    tar.close()
    rep.parquet = str(write_parquet(rows, out / PARQUET_NAME))
    write_jsonl(rows, out / JSONL_NAME)
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
        members = tarfile.open(fileobj=io.BytesIO(blob), mode="r:").getmembers()
        files = [m for m in members if m.isfile()]
        if len(files) != row["n_files"]:
            raise PackError(f"row {row['id']}: {len(files)} files in range, expected {row['n_files']}")
        if not all(m.name.startswith(row["key"] + "/") for m in files):
            raise PackError(f"row {row['id']}: foreign member in byte range")
        n += 1
    return n
