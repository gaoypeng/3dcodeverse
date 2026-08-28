#!/usr/bin/env python3
"""Batch: generate self-contained CadQuery code.py for every with_mesh sample,
verify chamfer vs HF ground truth, update meta.json. Resumable & parallel.

For each sample:
 1. If articraft_source_code.py missing -> cp code.py to it (preserve SDK source).
 2. gen_code -> write self-contained code.py (only import cadquery + inlined shim).
 3. verify_cadquery.py (subprocess) -> chamfer / bbox / pass.
 4. update meta.json: language/entry/environment/aux_source/conversion/status.

Usage: python tools/batch_verify.py [N|all] [--workers W] [--force] [--shard i/k]
  default: process only samples not yet verifier_pass (resumable).
"""
import os, sys, json, subprocess, shutil, time, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ARTICRAFT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
DD = ROOT + "/data/articraft_with_mesh"
sys.path.insert(0, _HERE)
import gen_code

# cap per-subprocess thread fan-out so N workers don't each grab all cores
_ENV = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
            MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")

argv = sys.argv[1:]
N = 0
WORKERS = 10
FORCE = False
SHARD = None
_i = 0
while _i < len(argv):
    a = argv[_i]
    if a == "--workers":
        WORKERS = int(argv[_i + 1]); _i += 2; continue
    if a == "--shard":
        _x, _k = argv[_i + 1].split("/"); SHARD = (int(_x), int(_k)); _i += 2; continue
    if a == "--force":
        FORCE = True; _i += 1; continue
    if a.isdigit():
        N = int(a)
    _i += 1


def done(name):
    try:
        m = json.load(open(f"{DD}/{name}/meta.json"))
        return (m.get("conversion") or {}).get("verifier_pass") is True
    except Exception:
        return False


def process(name):
    d = f"{DD}/{name}"
    src = f"{d}/articraft_source_code.py"
    try:
        if not os.path.exists(src):
            shutil.copy(f"{d}/code.py", src)   # preserve original SDK source
        txt, fams = gen_code.gen_code_text(d)
        with open(f"{d}/code.py", "w") as f:
            f.write(txt)
    except Exception as e:
        return name, {"pass": False, "error": "gen:%s" % e, "families": []}
    try:
        r = subprocess.run([sys.executable, f"{ROOT}/tools/verify_cadquery.py", d, f"{d}/code.py",
                            "--glb", f"{d}/renders/object.glb"],
                           capture_output=True, text=True, timeout=420, env=_ENV)
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:
        res = {"pass": False, "chamfer": None, "bbox_ratio": None, "error": "verify:%s" % e}
    try:
        mp = f"{d}/meta.json"
        meta = json.load(open(mp))
        meta["language"] = "CadQuery (Python)"
        meta["entry"] = "code.py"
        meta["environment"] = "CadQuery (pip install cadquery)"
        meta["aux_source"] = "articraft_source_code.py (Articraft SDK source)"
        meta["conversion"] = {
            "chamfer": res.get("chamfer"), "bbox_ratio": res.get("bbox_ratio"),
            "volume_ratio": res.get("volume_ratio"), "verifier_pass": bool(res.get("pass")),
            "families": fams, "error": res.get("error"),
        }
        meta["status"] = "curated" if res.get("pass") else "revise"
        json.dump(meta, open(mp, "w"), indent=2, ensure_ascii=False)
    except Exception as e:
        res["meta_error"] = str(e)
    res["families"] = fams
    return name, res


def main():
    names = sorted(os.listdir(DD))
    if SHARD:
        i, k = SHARD
        names = [n for n in names if int(hashlib.md5(n.encode()).hexdigest(), 16) % k == i]
    if not FORCE:
        names = [n for n in names if not done(n)]
    if N:
        names = names[:N]
    total = len(names)
    gen_code._load()   # pre-load shim sources ONCE (avoid thread race on lazy init)
    print(f"processing {total} samples, workers={WORKERS}", flush=True)
    t0 = time.time()
    npass = nfail = 0
    chs = []
    failcauses = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(process, n): n for n in names}
        for k, fut in enumerate(as_completed(futs), 1):
            name, res = fut.result()
            if res.get("pass"):
                npass += 1
                if res.get("chamfer") is not None:
                    chs.append(res["chamfer"])
            else:
                nfail += 1
                err = (res.get("error") or "chamfer/bbox_fail")[:45]
                failcauses[err] = failcauses.get(err, 0) + 1
            if k % 25 == 0 or k == total:
                el = time.time() - t0
                avg = sum(chs) / len(chs) if chs else 0
                print(f"[{k}/{total}] pass={npass} fail={nfail} avgChamfer={avg:.4f} "
                      f"{el:.0f}s ETA{el/k*(total-k):.0f}s", flush=True)
    el = time.time() - t0
    print(f"\nDONE pass={npass} fail={nfail} ({100*npass/max(total,1):.1f}%) in {el:.0f}s", flush=True)
    print("top fail causes:", sorted(failcauses.items(), key=lambda x: -x[1])[:15], flush=True)


if __name__ == "__main__":
    main()
