"""
pack_tar_parquet.py  (v2 — spec-conformant, matches ilabai/3dcodeverse Storage Rules)

Emits the official chunked tar+parquet layout:

  deepcad/v1/        metadata.parquet (<20MB) + samples-000.tar (<=2.5GB) [+ 001...]
  deepcad/v1_1/      ...
  deepcad/v1_N/      ...

Rules honored:
- FLAT chunk folders; split a too-big subset into sibling folders v1, v1_1, v1_2...
  when a chunk's metadata approaches the size cap.
- metadata.parquet columns EXACTLY: id, key, name, captions{detailed,instruction,factory},
  meta_json, code, tar, byte_start, byte_len, n_files  (NO curation_json).
- Subset extras (Text2CAD text, IoU fidelity, dedup/splits) are folded INTO meta_json
  (§7 "subsets add their own keys"), so they're visible in the standard meta_json column.
- `id` = "deepcad/v1/<key>" (logical subset, no chunk suffix).
- `tar` = repo-root-relative path "deepcad/<folder>/samples-NNN.tar".
- captions left empty (maintainer captions).
- plain uncompressed .tar; a sample's files are consecutive; byte_start/len is a sub-tar.

Reads the canonical dir tree produced by to_3dcodeverse.py (meta.json + curation.json
+ code.py + renders/).
"""
import io
import os
import json
import tarfile
import argparse
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

PARQUET_SCHEMA = pa.schema([
    ("id", pa.string()), ("key", pa.string()), ("name", pa.string()),
    ("captions", pa.struct([("detailed", pa.string()),
                            ("instruction", pa.string()),
                            ("factory", pa.string())])),
    ("meta_json", pa.string()), ("code", pa.string()), ("tar", pa.string()),
    ("byte_start", pa.int64()), ("byte_len", pa.int64()), ("n_files", pa.int32()),
])
EMPTY_CAP = {"detailed": "", "instruction": "", "factory": ""}
RENDER_ORDER = ["view_00.png", "view_01.png", "view_02.png", "view_03.png",
                "object.glb", "model.step"]


def add_bytes(tar, arcname, data):
    ti = tarfile.TarInfo(arcname)
    ti.size = len(data)
    tar.addfile(ti, io.BytesIO(data))


def build_meta_json(d, key, subset):
    """meta.json with subset extras (text2cad/fidelity/splits) folded in."""
    meta = json.loads((d / "meta.json").read_text())
    meta["id"] = f"deepcad/{subset}/{key}"        # logical subset, no chunk suffix
    meta["source_url"] = "http://www.cs.columbia.edu/cg/deepcad/data.tar"
    cur = json.loads((d / "curation.json").read_text()) if (d / "curation.json").exists() else {}
    meta["text2cad"] = cur.get("text_text2cad", {})           # <- Text2CAD now here (visible)
    meta["text_license"] = cur.get("text_license")
    meta["fidelity"] = cur.get("fidelity", {})                # iou / chamfer / tier / quality
    meta["splits"] = cur.get("splits", {})                    # split_original/dedup/is_canonical/dup_of
    meta["provenance"] = cur.get("provenance")
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, help=".../corpus_3dcv/deepcad")
    ap.add_argument("--out", required=True)
    ap.add_argument("--subset", default="v1")
    ap.add_argument("--meta_raw_mb", type=int, default=38,
                    help="raw code+meta bytes per chunk before split (~2.5x -> parquet MB)")
    ap.add_argument("--tar_gb", type=float, default=2.0, help="max bytes per tar within a chunk")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    corpus = Path(args.corpus)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    RAW_LIMIT = args.meta_raw_mb * 1024 * 1024
    TAR_LIMIT = int(args.tar_gb * 1024 * 1024 * 1024)
    subset = args.subset

    samples = []
    for bucket in sorted(corpus.iterdir()):
        if bucket.is_dir():
            for d in sorted(bucket.iterdir()):
                if d.is_dir():
                    samples.append(d)
    if args.limit:
        samples = samples[: args.limit]
    print(f"Packing {len(samples)} samples -> chunks (meta cap {args.meta_raw_mb}MB raw, "
          f"tar cap {args.tar_gb}GB)")

    chunk_idx = 0          # 0 -> v1, 1 -> v1_1, ...
    rows = []
    chunk_raw = 0
    tar_idx = 0
    tar = None
    chunk_dir = None
    n_chunks_written = 0

    def folder_name(ci):
        return subset if ci == 0 else f"{subset}_{ci}"

    def open_chunk(ci):
        nonlocal tar, tar_idx, chunk_dir
        chunk_dir = out / folder_name(ci)
        chunk_dir.mkdir(parents=True, exist_ok=True)
        tar_idx = 0
        tar = tarfile.open(chunk_dir / f"samples-{tar_idx:03d}.tar", "w")

    def close_chunk(ci, chunk_rows):
        nonlocal tar, n_chunks_written
        if tar is not None:
            tar.close()
        pq.write_table(pa.Table.from_pylist(chunk_rows, schema=PARQUET_SCHEMA),
                       chunk_dir / "metadata.parquet", compression="zstd")
        n_chunks_written += 1
        sz = (chunk_dir / "metadata.parquet").stat().st_size / 1e6
        print(f"  {folder_name(ci)}: {len(chunk_rows)} rows, metadata {sz:.1f} MB")

    open_chunk(chunk_idx)
    for d in samples:
        key = d.name
        meta = build_meta_json(d, key, subset)
        meta_json = json.dumps(meta, ensure_ascii=False)
        code = (d / "code.py").read_text()
        raw = len(meta_json) + len(code)

        # start a new CHUNK when metadata would get too big
        if rows and chunk_raw + raw > RAW_LIMIT:
            close_chunk(chunk_idx, rows)
            rows = []
            chunk_raw = 0
            chunk_idx += 1
            open_chunk(chunk_idx)

        # start a new TAR within the chunk when it gets too big
        if tar.offset > 0 and tar.offset >= TAR_LIMIT:
            tar.close()
            tar_idx += 1
            tar = tarfile.open(chunk_dir / f"samples-{tar_idx:03d}.tar", "w")

        start = tar.offset
        add_bytes(tar, f"{key}/captions.json", json.dumps(EMPTY_CAP).encode()); n = 1
        add_bytes(tar, f"{key}/code.py", code.encode()); n += 1
        add_bytes(tar, f"{key}/meta.json", meta_json.encode()); n += 1
        for rname in RENDER_ORDER:
            rp = d / "renders" / rname
            if rp.exists():
                tar.add(rp, arcname=f"{key}/renders/{rname}"); n += 1
        byte_len = tar.offset - start

        rows.append({
            "id": f"deepcad/{subset}/{key}",
            "key": key,
            "name": meta.get("name", f"deepcad_{key}"),
            "captions": dict(EMPTY_CAP),
            "meta_json": meta_json,
            "code": code,
            "tar": f"deepcad/{folder_name(chunk_idx)}/samples-{tar_idx:03d}.tar",
            "byte_start": start,
            "byte_len": byte_len,
            "n_files": n,
        })
        chunk_raw += raw

    if rows:
        close_chunk(chunk_idx, rows)
    print(f"Done: {n_chunks_written} chunks.")


if __name__ == "__main__":
    main()
