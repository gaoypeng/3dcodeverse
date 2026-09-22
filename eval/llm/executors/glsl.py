"""GLSL (Shadertoy-style) executor: compile with glslang, then optionally render in headless Chromium.

Compile: the model's ``mainImage`` body is wrapped in a Shadertoy prelude (GLSL ES 3.00 uniforms incl.
``iFrameRate`` — the old eval prelude lacked it and failed shaders that used it) and checked with
``glslang -S frag``.

Render (``render=True``, default): a WebGL2 page draws two frames (iTime = 0 and 1.7 s) and measures
distinct quantised colours + pixel std.  ``render`` ∈ {OK, STATIC, FAIL}; a shader that compiles but paints
a constant colour is STATIC.  The suite-level ``status`` stays "compile OK" for comparability with the
historical numbers; ``render`` is reported alongside as the stricter signal.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from .. import config

PRELUDE = """#version 300 es
precision highp float; precision highp int;
uniform vec3 iResolution; uniform float iTime; uniform float iTimeDelta; uniform int iFrame; uniform float iFrameRate;
uniform vec4 iMouse; uniform vec4 iDate; uniform float iSampleRate;
uniform sampler2D iChannel0; uniform sampler2D iChannel1; uniform sampler2D iChannel2; uniform sampler2D iChannel3;
uniform vec3 iChannelResolution[4]; uniform float iChannelTime[4];
out vec4 _outColor;
"""
MAIN = "\nvoid main(){ vec4 c = vec4(0.0); mainImage(c, gl_FragCoord.xy); _outColor = c; }\n"

_UNIFORM_RE = re.compile(r"\s*uniform\s+.*\b(iResolution|iTime|iTimeDelta|iFrame|iFrameRate|iMouse|iDate|iSampleRate|iChannel[0-3]|iChannelResolution|iChannelTime)\b")


def normalise(code: str) -> str:
    """Drop #version / precision / Shadertoy-uniform / `out vec4` lines that the prelude already provides."""
    out = []
    for ln in code.splitlines():
        if re.match(r"\s*#\s*version\b", ln) or re.match(r"\s*precision\s+(low|medium|high)p\s+", ln):
            continue
        if _UNIFORM_RE.match(ln) or re.match(r"\s*out\s+vec4\s+\w+\s*;", ln):
            continue
        out.append(ln)
    return "\n".join(out)


HTML = """<!doctype html><html><body style="margin:0"><canvas id=c width=320 height=240></canvas><script>
const FS_BODY = %s;
const vs = `#version 300 es\nin vec2 p; void main(){ gl_Position = vec4(p,0.,1.); }`;
const fsHead = %s;
const fsTail = `\nvoid main(){ mainImage(_outColor, gl_FragCoord.xy); }`;
window.__result = {ok:false, err:null};
(function(){
  const c = document.getElementById('c');
  const gl = c.getContext('webgl2', {preserveDrawingBuffer:true});
  if (!gl) { window.__result.err = 'no webgl2'; return; }
  function sh(t, src){ const s = gl.createShader(t); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s).split('\\n')[0]); return s; }
  try {
    const p = gl.createProgram();
    gl.attachShader(p, sh(gl.VERTEX_SHADER, vs));
    gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fsHead + FS_BODY + fsTail));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p).split('\\n')[0]);
    gl.useProgram(p);
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 3,-1, -1,3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(p, 'p'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const tex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([128,128,128,255]));
    for (let i=0;i<4;i++){ const u = gl.getUniformLocation(p, 'iChannel'+i); if (u) gl.uniform1i(u, 0); }
    window.__draw = function(t){
      gl.uniform3f(gl.getUniformLocation(p,'iResolution'), c.width, c.height, 1);
      gl.uniform1f(gl.getUniformLocation(p,'iTime'), t);
      gl.uniform1f(gl.getUniformLocation(p,'iTimeDelta'), 1/60);
      gl.uniform1i(gl.getUniformLocation(p,'iFrame'), Math.floor(t*60));
      gl.uniform1f(gl.getUniformLocation(p,'iFrameRate'), 60);
      gl.viewport(0,0,c.width,c.height); gl.drawArrays(gl.TRIANGLES, 0, 3);
      const px = new Uint8Array(c.width*c.height*4);
      gl.readPixels(0,0,c.width,c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
      const seen = new Set(); let sum=0, sum2=0, n=0;
      for (let i=0;i<px.length;i+=4*7){ const r=px[i]>>4, g=px[i+1]>>4, b=px[i+2]>>4;
        seen.add((r<<8)|(g<<4)|b); const v=(px[i]+px[i+1]+px[i+2])/3; sum+=v; sum2+=v*v; n++; }
      const mean=sum/n; return {distinct: seen.size, std: Math.sqrt(Math.max(0, sum2/n - mean*mean)), mean};
    };
    window.__result.ok = true;
  } catch(e) { window.__result.err = String(e.message || e); }
})();
</script></body></html>"""


def compile_check(src: str, workdir: Path, timeout: int) -> dict:
    exe = config.find_glslang()
    if exe is None:
        return {"status": "CRASH", "error": "glslang not found (set C3D_GLSLANG)"}
    fp = workdir / "shader.frag"
    fp.write_text(PRELUDE + src + MAIN)
    try:
        p = subprocess.run([str(exe), "-S", "frag", str(fp)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, text=True, errors="replace")
        return {"status": "OK" if p.returncode == 0 else "FAIL", "error": None if p.returncode == 0 else p.stdout[-600:]}
    except subprocess.TimeoutExpired:
        return {"status": "TIMEOUT", "error": "glslang timeout"}


def render_check(src: str, workdir: Path, timeout: int) -> dict:
    """WebGL2 render in headless Chromium → {render: OK|STATIC|FAIL, distinct, std, changed, shot, render_error}."""
    rep = {"render": "FAIL", "render_error": None, "distinct": 0, "std": 0.0, "changed": False, "shot": None}
    page_path = workdir / "index.html"
    page_path.write_text(HTML % (json.dumps(src), json.dumps(PRELUDE.replace("_outColor", "_outColor"))))
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"])
            pg = b.new_page()
            errs: list[str] = []
            pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
            pg.goto("file://" + str(page_path), wait_until="load", timeout=timeout * 1000)
            res = pg.evaluate("window.__result")
            if not res or not res.get("ok"):
                rep["render_error"] = (res or {}).get("err") or (errs[0] if errs else "no result")
                b.close()
                return rep
            a = pg.evaluate("window.__draw(0.0)")
            c = pg.evaluate("window.__draw(1.7)")
            try:
                shot = workdir / "frame.png"
                pg.screenshot(path=str(shot))
                rep["shot"] = str(shot)
            except Exception:  # noqa: BLE001
                pass
            b.close()
        rep["distinct"] = max(a["distinct"], c["distinct"])
        rep["std"] = round(max(a["std"], c["std"]), 2)
        rep["changed"] = abs(a["mean"] - c["mean"]) > 0.5 or a["distinct"] != c["distinct"]
        rep["render"] = "OK" if (rep["distinct"] > 3 and rep["std"] > 2.0) else "STATIC"
        if rep["render"] == "STATIC":
            rep["render_error"] = f"uniform image (distinct={rep['distinct']}, std={rep['std']})"
    except Exception as e:  # noqa: BLE001
        rep["render_error"] = str(e)[:200]
    return rep


def run(code: str, workdir: str, timeout: int = 60, render: bool = True) -> dict:
    wd = Path(workdir).resolve()
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "code.frag").write_text(code)
    t0 = time.time()
    src = normalise(code)
    if "mainImage" not in src:
        rep = {"status": "FAIL", "error": "no mainImage()"}
    else:
        rep = compile_check(src, wd, timeout)
        if rep["status"] == "OK" and render:
            rep.update(render_check(src, wd, timeout))
    rep["latency_s"] = round(time.time() - t0, 2)
    rep["mesh"] = None
    return rep
