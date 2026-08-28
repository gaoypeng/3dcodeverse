#!/usr/bin/env python3
"""Light pass: for every PASSING with_mesh sample, regenerate the tree-shaken
code.py and export object.glb (exec ONCE) — NO chamfer (tree-shaking can't
change the geometry of used symbols, and a successful glb export already
validates the B-rep, so re-chamfering is redundant). Keeps the existing
verifier_pass verdict. The GPU render consumer renders the glbs in parallel.

Usage: python tools/regen_glb.py [--workers W] [--force]
"""
import os, sys, json, subprocess, time
from concurrent.futures import ThreadPoolExecutor, as_completed

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ARTICRAFT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
DD = ROOT + "/data/articraft_with_mesh"
sys.path.insert(0, _HERE)
import gen_code

argv = sys.argv[1:]
WORKERS = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else 16
FORCE = "--force" in argv
_ENV = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
            MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")


def passed(n):
    try:
        return (json.load(open(f"{DD}/{n}/meta.json")).get("conversion") or {}).get("verifier_pass") is True
    except Exception:
        return False


def proc(n):
    d = f"{DD}/{n}"
    try:
        txt, _ = gen_code.gen_code_text(d)
        with open(f"{d}/code.py", "w") as f:
            f.write(txt)
    except Exception as e:
        return n, "gen_fail:%s" % str(e)[:50]
    try:
        r = subprocess.run([sys.executable, f"{ROOT}/tools/cq_to_glb.py", d],
                           env=_ENV, capture_output=True, text=True, timeout=300)
        return n, ("ok" if "GLB_OK" in r.stdout else "glb_fail")
    except Exception as e:
        return n, "err:%s" % str(e)[:40]


def main():
    gen_code._load()
    names = [n for n in sorted(os.listdir(DD)) if passed(n)]
    if not FORCE:
        names = [n for n in names if not os.path.exists(f"{DD}/{n}/renders/object.glb")]
    total = len(names)
    print(f"regen+glb {total} passing samples | workers={WORKERS} (no chamfer)", flush=True)
    t0 = time.time(); ok = fail = 0; fails = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(proc, n): n for n in names}
        for k, fut in enumerate(as_completed(futs), 1):
            n, st = fut.result()
            if st == "ok":
                ok += 1
            else:
                fail += 1; fails.append((n, st))
            if k % 100 == 0 or k == total:
                el = time.time() - t0
                print(f"[{k}/{total}] ok={ok} fail={fail} {el:.0f}s {el/k:.2f}s/ea ETA{el/k*(total-k):.0f}s", flush=True)
    print(f"REGEN_GLB_DONE ok={ok} fail={fail} in {time.time()-t0:.0f}s", flush=True)
    for n, s in fails[:20]:
        print("  FAIL", n[:45], s)


if __name__ == "__main__":
    main()
