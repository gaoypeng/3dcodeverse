#!/usr/bin/env python3
"""Regenerate every PASSING with_mesh code.py (current gen_code) and EXEC-validate
all of them: exec code.py -> result must be a cq.Assembly/Workplane/Shape. Catches
NameError / type / dedup-precedence regressions across the WHOLE set (not a sample).
Then chamfer-verify a random subset for geometry fidelity."""
import os, sys, time, json, statistics, subprocess, random
from concurrent.futures import ThreadPoolExecutor, as_completed

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ARTICRAFT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
DD = ROOT + "/data/articraft_with_mesh"
sys.path.insert(0, _HERE)
import gen_code; gen_code._load()

names = [n for n in os.listdir(DD)
         if (json.load(open(f"{DD}/{n}/meta.json")).get("conversion") or {}).get("verifier_pass") is True]

# 1) regenerate all (last-wins dedup)
t0 = time.time()
for n in names:
    try:
        txt, _ = gen_code.gen_code_text(f"{DD}/{n}")
        open(f"{DD}/{n}/code.py", "w").write(txt)
    except Exception as e:
        print("GENFAIL", n, str(e)[:60], flush=True)
ls = [sum(1 for _ in open(f"{DD}/{n}/code.py")) for n in names]
print(f"regen {len(names)} {time.time()-t0:.0f}s | median {statistics.median(ls):.0f} "
      f"mean {statistics.mean(ls):.0f} max {max(ls)} | >1500 {sum(x>1500 for x in ls)} "
      f">1000 {sum(x>1000 for x in ls)}", flush=True)

CHECK = ROOT + "/tools/_execchk.py"
open(CHECK, "w").write(
    'import sys,types,warnings\n'
    'warnings.filterwarnings("ignore")\n'
    'd=sys.argv[1]; src=open(d+"/code.py").read()\n'
    'm=types.ModuleType("v"); m.__dict__["__builtins__"]=__builtins__; m.__dict__["__file__"]=d+"/code.py"\n'
    'sys.modules["v"]=m\n'
    'try:\n'
    '    exec(compile(src,"c","exec"),m.__dict__)\n'
    '    r=m.__dict__.get("result")\n'
    '    import cadquery as cq\n'
    '    if r is None: print("NO_RESULT")\n'
    '    elif isinstance(r,(cq.Assembly,cq.Workplane,cq.Shape)): print("OK")\n'
    '    else: print("BADTYPE:"+type(r).__name__)\n'
    'except Exception as e:\n'
    '    print("EXECERR:"+type(e).__name__+":"+str(e)[:70])\n'
)
env = dict(os.environ, OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")


def chk(n):
    r = subprocess.run([sys.executable, CHECK, f"{DD}/{n}"], env=env, capture_output=True, text=True, timeout=300)
    return n, (r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "EMPTY:" + r.stderr[-60:])


t1 = time.time(); ok = 0; fails = []
with ThreadPoolExecutor(max_workers=16) as ex:
    futs = [ex.submit(chk, n) for n in names]
    for k, f in enumerate(as_completed(futs), 1):
        n, st = f.result()
        if st == "OK":
            ok += 1
        else:
            fails.append((n, st))
        if k % 1000 == 0:
            print(f"  exec {k}/{len(names)} ok={ok} fail={len(fails)} {time.time()-t1:.0f}s", flush=True)
print(f"EXEC_VALIDATE OK {ok}/{len(names)} fail {len(fails)} in {time.time()-t1:.0f}s", flush=True)
from collections import Counter
c = Counter(":".join(st.split(":")[:2]) for _, st in fails)
for k, v in c.most_common(20):
    print(f"  {v}x {k}", flush=True)
for n, st in fails[:30]:
    print("  FAIL", n[:44], st[:60], flush=True)
