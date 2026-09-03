"""Executors for non-Blender dialects. Each run_* returns dict(status, error, mesh_path or None, latency_s)."""
import os, subprocess, sys, time, json, tempfile, re
OPENSCAD = os.environ.get("OPENSCAD_BIN", "/wekafs/ict/hx_624/tools/openscad/squashfs-root/AppRun")
GLSLANG = os.environ.get("GLSLANG_BIN", "/wekafs/ict/hx_624/anaconda3/envs/llmft/bin/glslangValidator")
PY = os.environ.get("EVAL_PYTHON", "/wekafs/ict/hx_624/anaconda3/envs/llmft/bin/python")
CQ_RUNNER = r'''
import sys, json, runpy, traceback, os
os.environ.setdefault("OMP_NUM_THREADS","1")
script, out_stl = sys.argv[1], sys.argv[2]
rep = {"status":"FAIL","error":None}
try:
    import cadquery as cq
    sys.argv = [script, out_stl]
    _orig_export = cq.exporters.export
    captured = []
    def _capture(o, *a, **k): captured.append(o)
    # neutralise export calls (the scripts export STEP/STL to arbitrary paths) but remember the exported object
    for modname in ("cadquery.occ_impl.exporters", "cadquery.exporters"):
        try:
            import importlib; _m = importlib.import_module(modname); _m.export = _capture
        except Exception: pass
    try: cq.exporters.export = _capture
    except Exception: pass
    g = runpy.run_path(script, run_name="__main__", init_globals={"show_object": _capture, "debug": _capture})
    obj = None
    for c in reversed(captured):
        if isinstance(c, (cq.Workplane, cq.Shape, cq.Assembly)): obj = c; break
    if obj is None:
        for name in ("result", "final", "model", "part", "solid", "shape", "body", "assembly", "assy", "obj", "res"):
            if isinstance(g.get(name), (cq.Workplane, cq.Shape, cq.Assembly)): obj = g[name]; break
    if obj is None:
        cands = [v for v in g.values() if isinstance(v, (cq.Workplane, cq.Shape, cq.Assembly))]
        obj = cands[-1] if cands else None
    if obj is None: raise RuntimeError("no CadQuery result object found (expected variable `result`)")
    if isinstance(obj, cq.Assembly): obj = obj.toCompound()
    if isinstance(obj, cq.Workplane):
        vals = obj.vals()
        if not vals: raise RuntimeError("empty workplane")
        obj = vals[0] if len(vals)==1 else cq.Compound.makeCompound([v for v in vals if isinstance(v, cq.Shape)])
    if not isinstance(obj, cq.Shape): raise RuntimeError(f"unsupported object {type(obj)}")
    if obj.Volume() <= 1e-9 and not obj.Faces(): raise RuntimeError("empty shape")
    _orig_export(obj, out_stl, tolerance=0.01, angularTolerance=0.1)
    rep["status"]="OK"
except BaseException as e:
    rep["error"]=(type(e).__name__+": "+str(e))[:500]; rep["traceback"]=traceback.format_exc()[-1500:]
print("CQ_REPORT "+json.dumps(rep))
'''
def run_cadquery(code, workdir, timeout=90):
    workdir = os.path.abspath(workdir); os.makedirs(workdir, exist_ok=True); sp = os.path.join(workdir, "script.py"); stl = os.path.join(workdir, "out.stl"); rp = os.path.join(workdir, "runner.py")
    open(sp, "w").write(code); open(rp, "w").write(CQ_RUNNER)
    t0 = time.time()
    try:
        p = subprocess.run([PY, rp, sp, stl], cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, text=True, errors="replace")
        m = re.search(r"CQ_REPORT (\{.*\})", p.stdout)
        rep = json.loads(m.group(1)) if m else {"status": "CRASH", "error": p.stdout[-800:]}
    except subprocess.TimeoutExpired:
        rep = {"status": "TIMEOUT", "error": f"timeout {timeout}s"}
    rep["latency_s"] = round(time.time() - t0, 1); rep["mesh"] = stl if rep["status"] == "OK" and os.path.exists(stl) else None
    if rep["status"] == "OK" and rep["mesh"] is None: rep["status"] = "EMPTY"
    return rep
def run_openscad(code, workdir, timeout=120):
    workdir = os.path.abspath(workdir); os.makedirs(workdir, exist_ok=True); sc = os.path.join(workdir, "model.scad"); stl = os.path.join(workdir, "out.stl")
    open(sc, "w").write(code); t0 = time.time()
    try:
        p = subprocess.run([OPENSCAD, "-o", stl, "--export-format", "binstl", sc], cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, text=True, errors="replace",
                           env={**os.environ, "HOME": workdir, "QT_QPA_PLATFORM": "offscreen"})
        ok = p.returncode == 0 and os.path.exists(stl) and os.path.getsize(stl) > 100
        rep = {"status": "OK" if ok else ("EMPTY" if p.returncode == 0 else "FAIL"), "error": None if ok else p.stdout[-800:]}
    except subprocess.TimeoutExpired:
        rep = {"status": "TIMEOUT", "error": f"timeout {timeout}s"}
    rep["latency_s"] = round(time.time() - t0, 1); rep["mesh"] = stl if rep["status"] == "OK" else None
    return rep
GLSL_PRELUDE = """#version 300 es
precision highp float; precision highp int;
uniform vec3 iResolution; uniform float iTime; uniform float iTimeDelta; uniform int iFrame; uniform vec4 iMouse; uniform vec4 iDate; uniform float iSampleRate;
uniform sampler2D iChannel0; uniform sampler2D iChannel1; uniform sampler2D iChannel2; uniform sampler2D iChannel3;
uniform vec3 iChannelResolution[4]; uniform float iChannelTime[4];
out vec4 _outColor;
"""
GLSL_MAIN = "\nvoid main(){ vec4 c = vec4(0.0); mainImage(c, gl_FragCoord.xy); _outColor = c; }\n"
def run_glsl(code, workdir, timeout=30):
    workdir = os.path.abspath(workdir); os.makedirs(workdir, exist_ok=True); fp = os.path.join(workdir, "shader.frag")
    # normalise: drop #version / precision lines and re-declarations of Shadertoy uniforms / the out variable (the prelude provides them)
    import re as _re
    lines = []
    for ln in code.splitlines():
        if _re.match(r"\s*#\s*version\b", ln) or _re.match(r"\s*precision\s+(low|medium|high)p\s+", ln): continue
        if _re.match(r"\s*uniform\s+.*\b(iResolution|iTime|iTimeDelta|iFrame|iMouse|iDate|iSampleRate|iChannel[0-3]|iChannelResolution|iChannelTime)\b", ln): continue
        if _re.match(r"\s*out\s+vec4\s+\w+\s*;", ln): continue
        lines.append(ln)
    src = "\n".join(lines)
    if "mainImage" not in src: return {"status": "FAIL", "error": "no mainImage()", "latency_s": 0, "mesh": None}
    open(fp, "w").write(GLSL_PRELUDE + src + GLSL_MAIN); t0 = time.time()
    try:
        p = subprocess.run([GLSLANG, "-S", "frag", fp], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, text=True, errors="replace")
        rep = {"status": "OK" if p.returncode == 0 else "FAIL", "error": None if p.returncode == 0 else p.stdout[-600:]}
    except subprocess.TimeoutExpired:
        rep = {"status": "TIMEOUT", "error": "timeout"}
    rep["latency_s"] = round(time.time() - t0, 2); rep["mesh"] = None
    return rep
RUNNERS = {"cadquery": run_cadquery, "openscad": run_openscad, "glsl": run_glsl}
