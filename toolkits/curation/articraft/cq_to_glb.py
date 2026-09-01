#!/usr/bin/env python3
"""Exec a self-contained with_mesh code.py and export its `result` (cq.Assembly,
with material colors) to <sample>/renders/object.glb. Usage: cq_to_glb.py <sample_dir>"""
import sys, os, types, warnings
warnings.filterwarnings("ignore")

d = sys.argv[1]
src = open(f"{d}/code.py").read()
mod = types.ModuleType("cqglb_mod")
mod.__dict__["__builtins__"] = __builtins__
mod.__dict__["__file__"] = f"{d}/code.py"   # model body may ref AssetContext.from_script(__file__)
sys.modules["cqglb_mod"] = mod
exec(compile(src, f"{d}/code.py", "exec"), mod.__dict__)
result = mod.__dict__.get("result")
if result is None:
    print("NO_RESULT"); sys.exit(1)
os.makedirs(f"{d}/renders", exist_ok=True)
out = f"{d}/renders/object.glb"
try:
    result.save(out)            # cq.Assembly -> GLB (keeps per-part colors)
except Exception:
    import cadquery as cq
    cq.exporters.export(result.toCompound(), out)
print("GLB_OK", os.path.getsize(out))
