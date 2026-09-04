"""Execute every <gen_dir>/<task>/code.py in headless Blender (parallel), export GLB, collect reports -> <gen_dir>/exec_results.jsonl"""
import os
import argparse, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
BLENDER = os.environ.get("BLENDER_BIN", "/wekafs/ict/hx_624/tools/blender-5.0.1-linux-x64/blender")
RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_runner.py")

def run_one(task_dir, timeout):
    task = os.path.basename(task_dir)
    script = os.path.join(task_dir, "code.py"); glb = os.path.join(task_dir, "out.glb"); rep = os.path.join(task_dir, "exec.json")
    if os.path.exists(glb): os.remove(glb)
    cmd = [BLENDER, "-b", "--factory-startup", "-noaudio", "--python", RUNNER, "--", "--script", script, "--out", glb, "--report", rep]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, text=True, errors="replace",
                           env={**os.environ, "HOME": "/tmp", "XDG_CONFIG_HOME": "/tmp/.bcfg"})
        if os.path.exists(rep):
            r = json.load(open(rep))
        else:
            r = {"status": "CRASH", "error": p.stdout[-1500:], "latency_s": round(time.time() - t0, 1)}
    except subprocess.TimeoutExpired:
        r = {"status": "TIMEOUT", "error": f"timeout {timeout}s", "latency_s": timeout}
    r["task"] = task
    return r

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen_dir", required=True)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()
    a.gen_dir = os.path.abspath(a.gen_dir)   # the Blender runner chdir()s into the script dir -> all paths must be absolute
    tasks = sorted(d for d in os.listdir(a.gen_dir) if os.path.isfile(os.path.join(a.gen_dir, d, "code.py")))
    print(f"[exec] {len(tasks)} scripts, {a.workers} workers", flush=True)
    res = []
    with ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(run_one, os.path.join(a.gen_dir, t), a.timeout): t for t in tasks}
        for i, fu in enumerate(as_completed(futs)):
            r = fu.result(); res.append(r)
            if (i + 1) % 20 == 0: print(f"[exec] {i+1}/{len(tasks)}", flush=True)
    res.sort(key=lambda r: r["task"])
    with open(os.path.join(a.gen_dir, "exec_results.jsonl"), "w") as f:
        for r in res: f.write(json.dumps(r) + "\n")
    from collections import Counter
    c = Counter(r["status"] for r in res)
    print("[exec] status counts:", dict(c), f"| OK rate = {c.get('OK',0)}/{len(res)} = {100*c.get('OK',0)/max(len(res),1):.1f}%")

if __name__ == "__main__":
    main()
