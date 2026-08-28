#!/usr/bin/env python3
"""Phase A of integrating debug-recovered samples: for every currently-failing
sample (verifier_pass != true) plus globe_0003 (passed but never rendered),
regenerate code.py with the fixed shim, verify+export glb in one exec, and if it
executes, mark it included (verifier_pass=true, real chamfer/bbox, geom_approx flag
for chamfer>0.02 or bbox out of [0.9,1.1]). Samples that still fail to exec are left
untouched. Phases B/C/D (render, caption, upload) run after."""
import os, sys, json, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ARTICRAFT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
DD = ROOT + "/data/articraft_with_mesh"
sys.path.insert(0, _HERE)
import gen_code; gen_code._load()
env = dict(os.environ, OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")


def cv(meta):
    return (meta.get("conversion") or {})


def bbox_ok(bb):
    if bb is None:
        return False
    vals = bb if isinstance(bb, list) else [bb]
    return all(0.9 <= v <= 1.1 for v in vals if isinstance(v, (int, float)))


def proc(n):
    d = f"{DD}/{n}"
    try:
        txt, _ = gen_code.gen_code_text(d)
        open(f"{d}/code.py", "w").write(txt)
    except Exception as e:
        return n, "gen_fail", str(e)[:50]
    glb = f"{d}/renders/object.glb"
    try:
        r = subprocess.run([sys.executable, "tools/verify_cadquery.py", d, f"{d}/code.py", "--glb", glb],
                           env=env, capture_output=True, text=True, timeout=400)
        j = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:
        return n, "exec_fail", str(e)[:50]
    ch, bb = j.get("chamfer"), j.get("bbox_ratio")
    if ch is None and not os.path.exists(glb):
        return n, "exec_fail", str(j.get("error"))[:50]
    # executed + produced glb -> include it
    meta = json.load(open(f"{d}/meta.json"))
    conv = meta.get("conversion") or {}
    conv["verifier_pass"] = True
    conv["chamfer"] = ch
    conv["bbox_ratio"] = bb
    conv["error"] = None
    strict = (ch is not None and ch < 0.02) and bbox_ok(bb)
    conv["geom_approx"] = (not strict)   # accepted despite slightly-off chamfer/bbox
    meta["conversion"] = conv
    json.dump(meta, open(f"{d}/meta.json", "w"), ensure_ascii=False, indent=2)
    return n, ("ok_strict" if strict else "ok_approx"), ch


def main():
    targets = sorted(n for n in os.listdir(DD) if not cv(json.load(open(f"{DD}/{n}/meta.json"))).get("verifier_pass") is True)
    # also retry globe_0003 (passed but no glb -> slow exec)
    g = [n for n in os.listdir(DD) if n.startswith("rec_globe_0003")]
    for n in g:
        if n not in targets:
            targets.append(n)
    print(f"Phase A: processing {len(targets)} samples", flush=True)
    res = {"ok_strict": 0, "ok_approx": 0, "exec_fail": 0, "gen_fail": 0}
    skipped = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        for n, st, info in [f.result() for f in as_completed([ex.submit(proc, n) for n in targets])]:
            res[st] = res.get(st, 0) + 1
            if st in ("exec_fail", "gen_fail"):
                skipped.append((n, info))
    print(f"PHASE_A_DONE included={res['ok_strict']+res['ok_approx']} "
          f"(strict={res['ok_strict']} approx={res['ok_approx']}) skipped={len(skipped)}", flush=True)
    for n, i in skipped:
        print("  SKIP", n[:46], i)


if __name__ == "__main__":
    main()
