"""Build all three pair types for a 3DCodeVerse subdirectory: text->code, image->code, image+text->code.

The corpus so far only carries text->code, and its row count is inflated ~3x because every caption variant is a
separate row -- an augmentation three independent runs showed does not help. Here each SAMPLE appears once per
pair type with one caption, and the renders come from the sample's own slice of the tar (metadata carries
`tar`/`byte_start`/`byte_len`, so a ranged read fetches one sample without downloading a 2 GB shard).

  python scripts/build_pairs_3dcodebench.py --subdir 3dcodebench/factories_geo --out DIR [--views 4] [--workers 8]
      [--limit N] [--local_tars DIR]

Writes <out>/<name>_{text,img,imgtext}/train.parquet + dataset_info.json + qc.json.
"""
import argparse
import collections
import io
import json
import os
import tarfile
import time

import pandas as pd
import pyarrow.parquet as pq
import requests

META = "/wekafs/ict/hx_624/data/3dcodeverse_meta"
REPO = "ilabai/3dcodeverse"
SYS = {
    "blender": "You are an expert in procedural 3D modeling with Blender Python (bpy). Write a complete, standalone Blender 5 Python script that builds the requested object from scratch (clear the default scene first). Output ONLY the code in one ```python block.",
    "glsl": "You are an expert GLSL shader programmer. Write a complete Shadertoy-style fragment shader. Output ONLY the code in one ```glsl block.",
}
FENCE_RE = __import__('re').compile(r'^```[a-zA-Z]*\n|\n?```$')
FENCE = {"blender": "python", "glsl": "glsl"}
ASK_TEXT = {"blender": "Write the Blender Python code that builds this object: ",
            "glsl": "Write the shader described here: "}
ASK_IMG = {"blender": "These are reference renders of one object. Write the Blender Python code that builds it.",
           "glsl": "These are frames rendered by one shader. Write the shader that produces them."}
ASK_BOTH = {"blender": "These are reference renders of one object. Write the Blender Python code that builds it.\n\nThe object: ",
            "glsl": "These are frames rendered by one shader. Write the shader that produces them.\n\nThe shader: "}
IMPERATIVE = ("write", "create", "implement", "make", "build", "generate", "design", "produce")


def caption_of(caps):
    """the best human description, reduced to a noun phrase because the ASK_* strings already supply the verb"""
    for k in ("user_instruction", "instruction", "detailed", "brief", "factory"):
        v = (caps.get(k) or "").strip()
        if not v or len(v) < 15:
            continue
        low = v.lower()
        if low.startswith(IMPERATIVE):
            for sep in (" that builds ", " that renders ", " that reproduces ", " of ", " for "):
                if sep in low:
                    return v[low.index(sep) + len(sep):].strip()[:1200]
            continue
        return v[:1200]
    return None


def fetch_sample(row, token, local_tars=None):
    """-> list of render bytes for one sample, via a local tar if we have it, else a ranged read"""
    tar_rel, start, length = row["tar"], int(row["byte_start"]), int(row["byte_len"])
    if local_tars:
        p = os.path.join(local_tars, os.path.basename(tar_rel))
        if os.path.exists(p):
            with open(p, "rb") as f:
                f.seek(start)
                blob = f.read(length)
            return read_renders(blob)
    url = f"https://huggingface.co/datasets/{REPO}/resolve/main/{tar_rel}"
    hdr = {"Authorization": f"Bearer {token}", "Range": f"bytes={start}-{start + length - 1}"}
    # under a dozen concurrent readers the Hub throttles and times out; returning [] on the first failure looks
    # exactly like "this sample has no renders", which silently cost 70% of the image pairs on the first run
    for attempt in range(4):
        try:
            r = requests.get(url, headers=hdr, timeout=300)
            if r.status_code in (200, 206):
                return read_renders(r.content)
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = type(e).__name__
        time.sleep(2 ** attempt)
    raise FetchError(last)


class FetchError(Exception):
    """the sample could not be read at all — distinct from a sample that genuinely carries no renders"""


def read_renders(blob):
    """A byte slice out of the middle of a tar has no end-of-archive marker, so tarfile treats it as truncated —
    and it raises in getmembers(), not in open(), which is why guarding only the open() let one bad sample kill a
    whole run. Append the two zero blocks tar expects, and scan defensively."""
    blob = blob + b"\0" * 1024
    try:
        tf = tarfile.open(fileobj=io.BytesIO(blob))
    except Exception:
        return []
    out = []
    try:
        members = sorted(tf.getmembers(), key=lambda m: m.name)
    except Exception:
        return []
    for m in members:
        if m.isfile() and "/renders/" in m.name and m.name.endswith(".png"):
            try:
                f = tf.extractfile(m)
                if f:
                    out.append((os.path.basename(m.name), f.read()))
            except Exception:
                pass
    return out



def renders_by_scanning(subdir, sample_ids, views, token, local_tars=None, cache_root="/wekafs/ict/hx_624/data/pair_tars"):
    """Index every sample's renders by walking each tar once.

    The byte offsets in metadata.parquet cannot be trusted: for 3dcodebench/instances_geo only 577 of 1,953 point
    at a real tar header (checked by reading 512 bytes at each offset and looking for the ustar magic), because
    the archives were repacked after the metadata was written. Sequential scanning ignores the offsets entirely,
    and for a whole-subdirectory conversion it is also cheaper -- one pass per tar instead of one request per
    sample.
    """
    import glob as _glob
    from huggingface_hub import hf_hub_download
    want = {sid.split("/")[-1] for sid in sample_ids}
    found = {}
    tars = sorted({r for r in _tars_of(subdir)})
    for i, tar_rel in enumerate(tars, 1):
        if local_tars:
            path = os.path.join(local_tars, os.path.basename(tar_rel))
            if not os.path.exists(path):
                path = None
        else:
            path = None
        if path is None:
            path = hf_hub_download(REPO, tar_rel, repo_type="dataset", token=token, cache_dir=cache_root)
        n_before = len(found)
        try:
            with tarfile.open(path) as tf:
                for m in tf:
                    if not (m.isfile() and "/renders/" in m.name and m.name.endswith(".png")):
                        continue
                    key = m.name.split("/")[0]
                    if key not in want:
                        continue
                    lst = found.setdefault(key, [])
                    if len(lst) >= views:
                        continue
                    f = tf.extractfile(m)
                    if f:
                        lst.append((os.path.basename(m.name), f.read()))
        except Exception as e:
            print(f"[pairs] tar {os.path.basename(tar_rel)} unreadable: {type(e).__name__}", flush=True)
        print(f"[pairs] scanned {i}/{len(tars)} {os.path.basename(tar_rel)}: "
              f"+{len(found)-n_before} samples ({len(found):,}/{len(want):,})", flush=True)
    return found


def _has_verify_cols(path):
    import pyarrow.parquet as _pq
    try:
        return {"compiles", "repair"} <= set(_pq.ParquetFile(path).schema_arrow.names)
    except Exception:
        return False


def _tars_of(subdir):
    from huggingface_hub import HfApi
    api = HfApi(token=os.environ.get("HF_TOKEN", ""))
    return [f for f in api.list_repo_files(REPO, repo_type="dataset")
            if f.startswith(f"{subdir}/") and f.endswith(".tar")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subdir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--views", type=int, default=4)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--local_tars", default=None)
    ap.add_argument("--img_root", default="/wekafs/ict/hx_624/data/pair_images")
    ap.add_argument("--dialect", default="blender")
    # the compile verification lives in the *_llamafactory parquets; carrying it into the pair datasets is the
    # difference between "here is some GLSL" and "here is GLSL that a compiler accepted"
    ap.add_argument("--verified_from", default=None, help="dir with <name>_llamafactory/train.parquet holding compiles/repair")
    a = ap.parse_args()
    token = os.environ.get("HF_TOKEN", "")
    rows = pq.read_table(f"{META}/{a.subdir}/metadata.parquet").to_pylist()
    if a.limit:
        rows = rows[: a.limit]
    print(f"[pairs] {a.subdir}: {len(rows):,} source samples", flush=True)

    from concurrent.futures import ThreadPoolExecutor
    name = a.subdir.replace("/", "__")
    img_dir = os.path.join(a.img_root, a.subdir)
    os.makedirs(img_dir, exist_ok=True)
    qc = collections.Counter()

    verified = {}
    if a.verified_from:
        # the verification lives in two places depending on the shard: the 68 that could be flattened carry it in
        # <sub>_llamafactory_flat, the 44 that could not are in the separately verified tree
        base = os.path.basename(a.subdir)
        cands = []
        for root in a.verified_from.split(","):
            cands += [os.path.join(root, f"{base}_llamafactory_flat", "train.parquet"),
                      os.path.join(root, f"{base}_llamafactory", "train.parquet")]
        vp = next((c for c in cands if os.path.exists(c) and _has_verify_cols(c)), None)
        if vp:
            vdf = pd.read_parquet(vp, columns=["id", "compiles", "repair", "conversations"])
            for t in vdf.itertuples():
                # one row per caption variant; the code is identical across them, so first wins
                verified.setdefault(str(t.id), (bool(t.compiles), t.repair, t.conversations[1]["value"]))
            print(f"[pairs] verification joined for {len(verified):,} ids from {vp}", flush=True)
        else:
            print(f"[pairs] WARNING no parquet with compiles/repair for {base} — pairs carry no compiles column", flush=True)
    ids = [str(r["id"]) for r in rows]
    render_index = renders_by_scanning(a.subdir, ids, a.views, token, a.local_tars)

    def one(r):
        code = r.get("code") or ""
        if len(code.strip()) < 40:
            qc["short_code"] += 1
            return None
        caps = r.get("captions")
        caps = dict(caps) if isinstance(caps, dict) else (json.loads(caps) if isinstance(caps, str) else {})
        cap = caption_of(caps)
        renders = render_index.get(str(r["id"]).split("/")[-1], [])[: a.views]
        paths = []
        for fname, blob in renders:
            d = os.path.join(img_dir, str(r["id"]).replace("/", "_"))
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, fname)
            if not os.path.exists(p):
                open(p, "wb").write(blob)
            paths.append(p)
        if not paths:
            qc["no_renders"] += 1
        if not cap:
            qc["no_caption"] += 1
        v = verified.get(str(r["id"]))
        if v:
            # prefer the verified (possibly repaired) program over the raw metadata code
            code = FENCE_RE.sub("", v[2].strip()) or code
        return {"id": str(r["id"]), "code": code, "caption": cap, "images": paths,
                "compiles": (v[0] if v else None), "repair": (v[1] if v else None)}

    with ThreadPoolExecutor(a.workers) as ex:
        got = [x for x in ex.map(one, rows) if x]
    print(f"[pairs] usable samples: {len(got):,} | qc={dict(qc)}", flush=True)
    if qc["fetch_failed"]:
        print(f"[pairs] WARNING {qc['fetch_failed']:,} samples could not be fetched after 4 attempts — their image "
              f"pairs are MISSING, not absent by nature. Re-run to fill them in.", flush=True)

    d, fence = a.dialect, FENCE.get(a.dialect, "python")
    out = {"text": [], "img": [], "imgtext": []}
    for g in got:
        gpt = {"from": "gpt", "value": f"```{fence}\n{g['code'].strip()}\n```"}
        base = {"id": g["id"], "system": SYS[d]}
        if g["compiles"] is not None:
            base["compiles"] = g["compiles"]; base["repair"] = g["repair"]
        if g["caption"]:
            out["text"].append(dict(base, conversations=[{"from": "human", "value": ASK_TEXT[d] + g["caption"]}, gpt]))
        if g["images"]:
            ph = "<image>" * len(g["images"])
            out["img"].append(dict(base, images=list(g["images"]),
                                   conversations=[{"from": "human", "value": ph + "\n" + ASK_IMG[d]}, gpt]))
            if g["caption"]:
                out["imgtext"].append(dict(base, images=list(g["images"]),
                                           conversations=[{"from": "human", "value": ph + "\n" + ASK_BOTH[d] + g["caption"]}, gpt]))
    info = {}
    for kind, rowsk in out.items():
        if not rowsk:
            print(f"[pairs] {kind}: 0 rows — not written", flush=True)
            continue
        folder = os.path.join(a.out, f"{os.path.basename(a.subdir)}_llamafactory_{kind}")
        os.makedirs(folder, exist_ok=True)
        pd.DataFrame(rowsk).to_parquet(os.path.join(folder, "train.parquet"), index=False)
        cols = {"messages": "conversations", "system": "system"}
        if kind != "text":
            cols["images"] = "images"
        entry = {"file_name": "train.parquet", "formatting": "sharegpt", "columns": cols}
        json.dump({f"3dcv_{name}_{kind}": entry}, open(os.path.join(folder, "dataset_info.json"), "w"), indent=2)
        json.dump({"subdir": a.subdir, "pair_type": kind, "rows": len(rowsk),
                   "source_samples": len(rows), "usable": len(got), **{k: v for k, v in qc.items()}},
                  open(os.path.join(folder, "qc.json"), "w"), indent=2)
        info[kind] = len(rowsk)
        print(f"[pairs] {kind:8s} {len(rowsk):>7,} rows -> {folder}", flush=True)
    # the invariant LLaMA-Factory enforces on multimodal rows
    bad = sum(1 for k in ("img", "imgtext") for r in out[k]
              if r["conversations"][0]["value"].count("<image>") != len(r["images"]))
    print(f"[pairs] placeholder/image mismatches: {bad} (must be 0)", flush=True)


if __name__ == "__main__":
    main()
