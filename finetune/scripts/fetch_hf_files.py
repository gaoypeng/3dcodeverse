"""Download only files matching a regex from an HF dataset repo (handles tree pagination, no file locks).
usage: python fetch_hf_files.py REPO OUTDIR REGEX [--subdir SUB] [--workers 32]"""
import argparse, os, re, sys, requests
from concurrent.futures import ThreadPoolExecutor
from huggingface_hub import HfApi
ap = argparse.ArgumentParser(); ap.add_argument("repo"); ap.add_argument("out"); ap.add_argument("regex")
ap.add_argument("--subdir", default=None); ap.add_argument("--workers", type=int, default=32)
a = ap.parse_args()
tok = os.environ["HF_TOKEN"]; api = HfApi(token=tok); pat = re.compile(a.regex)
files = [f.path for f in api.list_repo_tree(a.repo, repo_type="dataset", path_in_repo=a.subdir, recursive=True) if getattr(f, "size", None) is not None and pat.search(f.path)]
print(f"[fetch] {len(files)} files match", flush=True)
sess = requests.Session(); sess.headers["Authorization"] = f"Bearer {tok}"
def get(p):
    dst = os.path.join(a.out, p)
    if os.path.exists(dst) and os.path.getsize(dst) > 0: return 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    for attempt in range(3):
        try:
            r = sess.get(f"https://huggingface.co/datasets/{a.repo}/resolve/main/{p}", timeout=60)
            if r.status_code == 200:
                open(dst, "wb").write(r.content); return 1
        except Exception as e:
            pass
    print("FAIL", p, flush=True); return -1
n = 0
with ThreadPoolExecutor(a.workers) as ex:
    for i, r in enumerate(ex.map(get, files)):
        n += (r == 1)
        if (i + 1) % 500 == 0: print(f"[fetch] {i+1}/{len(files)}", flush=True)
print(f"[fetch] done: {n} downloaded, {len(files)} total", flush=True)
