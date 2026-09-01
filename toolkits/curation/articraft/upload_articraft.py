#!/usr/bin/env python3
"""Upload the finished folders to HF ilabai/3dcodeverse under articraft/.
  data/articraft_urdf_geo_only -> articraft/urdf_geo_only
  data/articraft_urdf_tex      -> articraft/urdf_tex
  data/articraft_with_mesh     -> articraft/with_mesh   (verifier_pass samples only)
Chunked by sample name (multi-commit) so no single huge commit.
Resumable: huggingface_hub skips files already present.
Usage: python tools/upload_articraft.py [geo|tex|mesh|urdf|all]"""
import os, sys, math, time, json
# hf_transfer OFF: it has no 429 handling and HANGS when HF rate-limits us
# (uploading tens of thousands of small LFS render files blows the request quota).
# Plain huggingface_hub honors Retry-After and backs off on 429.
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
from huggingface_hub import HfApi, CommitOperationAdd

REPO = "ilabai/3dcodeverse"
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ARTICRAFT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
TOK = open(os.path.expanduser("~/.hf_token")).read().strip()
api = HfApi(token=TOK)
CHUNKS = 6
which = sys.argv[1] if len(sys.argv) > 1 else "all"


def upload_dir(local, prefix):
    names = sorted(os.listdir(local))
    per = math.ceil(len(names) / CHUNKS)
    print(f"=== {prefix}: {len(names)} samples in {CHUNKS} chunks of ~{per} ===", flush=True)
    for ci in range(0, len(names), per):
        batch = names[ci:ci + per]
        patterns = [f"{n}/**" for n in batch]
        idx = ci // per + 1
        t0 = time.time()
        for attempt in range(4):
            try:
                api.upload_folder(
                    repo_id=REPO, repo_type="dataset", folder_path=local,
                    path_in_repo=prefix, allow_patterns=patterns,
                    commit_message=f"add {prefix} chunk {idx}/{CHUNKS} ({len(batch)} samples)",
                )
                print(f"  chunk {idx}/{CHUNKS} OK ({len(batch)} samples) {time.time()-t0:.0f}s", flush=True)
                break
            except Exception as e:
                print(f"  chunk {idx} attempt {attempt} FAIL: {str(e)[:120]}", flush=True)
                time.sleep(10 * (attempt + 1))
        else:
            print(f"  chunk {idx} GAVE UP", flush=True)


def _passed(d):
    try:
        return (json.load(open(f"{d}/meta.json")).get("conversion") or {}).get("verifier_pass") is True
    except Exception:
        return False


def upload_filtered(local, prefix, names, chunks, pause=20):
    per = math.ceil(len(names) / chunks)
    print(f"=== {prefix}: {len(names)} samples in {chunks} chunks of ~{per} ===", flush=True)
    for ci in range(0, len(names), per):
        batch = names[ci:ci + per]
        patterns = [f"{n}/**" for n in batch]
        idx = ci // per + 1
        t0 = time.time()
        for attempt in range(8):
            try:
                api.upload_folder(
                    repo_id=REPO, repo_type="dataset", folder_path=local,
                    path_in_repo=prefix, allow_patterns=patterns,
                    commit_message=f"add {prefix} chunk {idx}/{chunks} ({len(batch)} samples)",
                )
                print(f"  chunk {idx}/{chunks} OK ({len(batch)} samples) {time.time()-t0:.0f}s", flush=True)
                break
            except Exception as e:
                msg = str(e)
                # 429 / rate-limit -> long backoff so we ride out the window
                wait = min(90 * (attempt + 1), 420) if any(s in msg for s in ("429", "rate limit", "Too Many")) else min(20 * (attempt + 1), 120)
                print(f"  chunk {idx} attempt {attempt} FAIL ({msg[:90]}) -> wait {wait}s", flush=True)
                time.sleep(wait)
        else:
            print(f"  chunk {idx} GAVE UP", flush=True)
        time.sleep(pause)  # gentle gap between chunks to stay under the request quota


def upload_lowcc(local, prefix, names, chunks, num_threads=5, pause=15):
    """Gentle upload via create_commit with a LOW LFS thread count (default 5) so
    we never burst past HF's request rate limit. Resumable: LFS objects already on
    the hub are skipped (hash check); a fully-present chunk is detected + skipped."""
    per = math.ceil(len(names) / chunks)
    print(f"=== {prefix}: {len(names)} samples in {chunks} chunks of ~{per} | num_threads={num_threads} ===", flush=True)
    for ci in range(0, len(names), per):
        batch = names[ci:ci + per]
        idx = ci // per + 1
        ops = []
        for s in batch:
            sd = f"{local}/{s}"
            for root, _, files in os.walk(sd):
                for f in files:
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, local)   # "<sample>/..."
                    ops.append(CommitOperationAdd(path_in_repo=f"{prefix}/{rel}", path_or_fileobj=full))
        t0 = time.time()
        for attempt in range(8):
            try:
                api.create_commit(repo_id=REPO, repo_type="dataset", operations=ops,
                                  commit_message=f"add {prefix} chunk {idx}/{chunks} ({len(batch)} samples)",
                                  num_threads=num_threads)
                print(f"  chunk {idx}/{chunks} OK ({len(batch)} samples, {len(ops)} files) {time.time()-t0:.0f}s", flush=True)
                break
            except Exception as e:
                msg = str(e)
                if "No files" in msg or "no files" in msg or "nothing to commit" in msg.lower():
                    print(f"  chunk {idx}/{chunks} already present, skip", flush=True)
                    break
                wait = min(90 * (attempt + 1), 420) if any(s in msg for s in ("429", "rate limit", "Too Many")) else min(20 * (attempt + 1), 120)
                print(f"  chunk {idx} attempt {attempt} FAIL ({msg[:90]}) -> wait {wait}s", flush=True)
                time.sleep(wait)
        else:
            print(f"  chunk {idx} GAVE UP", flush=True)
        time.sleep(pause)


if which in ("all", "urdf", "geo"):
    upload_dir(f"{ROOT}/data/articraft_urdf_geo_only", "articraft/urdf_geo_only")
if which in ("all", "urdf", "tex"):
    upload_dir(f"{ROOT}/data/articraft_urdf_tex", "articraft/urdf_tex")
if which in ("all", "mesh"):
    DD = f"{ROOT}/data/articraft_with_mesh"
    names = sorted(n for n in os.listdir(DD)
                   if _passed(f"{DD}/{n}") and os.path.exists(f"{DD}/{n}/renders/view_07.png"))
    NT = int(os.environ.get("UPLOAD_THREADS", "5"))
    upload_lowcc(DD, "articraft/cadquery_single_tex", names, 16, num_threads=NT)
if which == "meshnew":  # only the debug-recovered samples (carry a geom_approx flag)
    DD = f"{ROOT}/data/articraft_with_mesh"
    def _new(d):
        c = json.load(open(f"{d}/meta.json")).get("conversion") or {}
        return c.get("verifier_pass") is True and "geom_approx" in c
    names = sorted(n for n in os.listdir(DD)
                   if _new(f"{DD}/{n}") and os.path.exists(f"{DD}/{n}/renders/view_07.png"))
    print(f"meshnew: {len(names)} debug-recovered samples", flush=True)
    upload_lowcc(DD, "articraft/cadquery_single_tex", names, 2, num_threads=5)
print("UPLOAD_DONE", flush=True)
