"""并行渲染所有 URDF 样本(透明背景8视角): geo_only→白模(clay), tex→彩色(material)。
每物体独立 Blender 进程。用法: python driver_urdf_render.py [workers] [limit]"""
import os, subprocess, glob, sys, time
from concurrent.futures import ThreadPoolExecutor

BL="./tools/blender-5.0.1-linux-x64/blender"
N=int(sys.argv[1]) if len(sys.argv)>1 else 8
SCOPE=sys.argv[2] if len(sys.argv)>2 else "all"        # all | geo | tex
FORCE=len(sys.argv)>3 and sys.argv[3]=="force"          # 重渲已完成的
ENV=os.environ.copy(); ENV["LD_LIBRARY_PATH"]=(os.environ.get("CONDA_PREFIX","") + "/lib:")+ENV.get("LD_LIBRARY_PATH","")

tasks=[]
if SCOPE in ("all","geo"):
    for n in sorted(os.listdir("data/articraft_urdf_geo_only")):
        d=f"data/articraft_urdf_geo_only/{n}"
        if not FORCE and os.path.exists(f"{d}/renders/view_07.png"): continue
        tasks.append((f"{d}/code.urdf", f"{d}/renders", "clay"))
if SCOPE in ("all","tex"):
    for n in sorted(os.listdir("data/articraft_urdf_tex")):
        d=f"data/articraft_urdf_tex/{n}"
        if not FORCE and os.path.exists(f"{d}/renders/view_07.png"): continue
        tasks.append((f"{d}/code.urdf", f"{d}/renders", "material"))

def render(t):
    urdf,out,mode=t
    try:
        r=subprocess.run([BL,"--background","--threads","4","--python","blender_urdf.py","--",urdf,out,"1024","16","8",mode],
            env=ENV,capture_output=True,text=True,timeout=300)
        return "URDF_RENDER_DONE" in r.stdout
    except Exception:
        return False

t0=time.time(); done=fail=0
print(f"workers={N} tasks={len(tasks)} (geo_only白模 + tex彩色)",flush=True)
with ThreadPoolExecutor(max_workers=N) as ex:
    for ok in ex.map(render,tasks):
        done+=1; fail+=(0 if ok else 1)
        if done%100==0 or done==len(tasks):
            el=time.time()-t0
            print(f"[{done}/{len(tasks)}] {el:.0f}s {el/done:.1f}s/ea fail{fail} ETA{el/done*(len(tasks)-done):.0f}s",flush=True)
print(f"RENDER_ALL_DONE fail{fail} 用时{time.time()-t0:.0f}s",flush=True)
