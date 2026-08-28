#!/usr/bin/env python3
"""GPU-only consumer: render any with_mesh sample that has renders/object.glb
(produced by the fused batch_verify --glb) but no view_07.png yet. Loops until
the producer (batch_verify) is gone and nothing is left. Pure blender (GPU);
no cadquery exec, so it overlaps the CPU-bound gen+verify perfectly.

Usage: python tools/render_glb_only.py [--workers W] [--mode material|clay]
"""
import os, sys, json, glob, subprocess, time
from concurrent.futures import ThreadPoolExecutor, as_completed

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ARTICRAFT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
DD = ROOT + "/data/articraft_with_mesh"
BL = os.environ.get("BLENDER", "blender")
argv = sys.argv[1:]
WORKERS = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else 6
MODE = argv[argv.index("--mode") + 1] if "--mode" in argv else "material"

ENV = os.environ.copy()
ENV["LD_LIBRARY_PATH"] = (os.environ.get("CONDA_PREFIX","") + "/lib:") + ENV.get("LD_LIBRARY_PATH", "")


def bv_running():
    for p in glob.glob("/proc/[0-9]*"):
        try:
            c = open(p + "/cmdline", "rb").read().decode("u8", "replace")
        except Exception:
            continue
        if " -c " in c:
            continue
        if "batch_verify.py" in c or "regen_glb.py" in c:
            return True
    return False


def render(n):
    d = f"{DD}/{n}"
    glbp = f"{d}/renders/object.glb"
    try:
        r = subprocess.run([BL, "--background", "--threads", "3", "--python", f"{ROOT}/blender_glb.py",
                            "--", glbp, f"{d}/renders", "1024", "16", "8", MODE],
                           env=ENV, capture_output=True, text=True, timeout=300)
        return n, ("ok" if "GLB_RENDER_DONE" in r.stdout else "render_fail")
    except Exception as e:
        return n, "err:%s" % str(e)[:40]


def pending():
    out = []
    for n in os.listdir(DD):
        d = f"{DD}/{n}"
        if os.path.exists(f"{d}/renders/object.glb") and not os.path.exists(f"{d}/renders/view_07.png"):
            out.append(n)
    return out


def main():
    print(f"glb-render consumer | workers={WORKERS} mode={MODE}", flush=True)
    t0 = time.time(); total_ok = total_fail = 0; idle = 0
    while True:
        ps = pending()
        if not ps:
            if not bv_running():
                idle += 1
                if idle >= 2:
                    break
            time.sleep(15)
            continue
        idle = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(render, n): n for n in ps}
            for fut in as_completed(futs):
                n, st = fut.result()
                if st == "ok":
                    total_ok += 1
                else:
                    total_fail += 1
        print(f"  rendered batch: ok={total_ok} fail={total_fail} elapsed {time.time()-t0:.0f}s", flush=True)
    print(f"GLB_RENDER_ALL_DONE ok={total_ok} fail={total_fail} in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
