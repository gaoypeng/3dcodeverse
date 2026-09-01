"""Render a Shadertoy fragment shader in a real WebGL2 context and check that it draws something.

Compiling is necessary but not sufficient: a shader can compile and still paint a constant colour. This runs the
shader in headless Chromium (the same browser the three.js runner uses), samples two frames (iTime = 0 and 1.7 s)
and reports, per frame, how many distinct quantised colours the canvas holds and its pixel standard deviation.

  render_glsl(code, workdir) -> {status, error, distinct, std, changed, shot}
      status  OK      renders a non-uniform image
              STATIC  renders, but the image is (near) uniform — usually a black screen
              FAIL    shader/link error inside the browser, or the page threw
      changed  True if frame(1.7s) differs from frame(0) — i.e. the shader animates

usage as a script:  python eval/glsl_render.py file1.glsl [file2.glsl ...]
"""
import json
import os
import sys

HTML = """<!doctype html><html><body style="margin:0">
<canvas id=c width=320 height=240></canvas>
<script>
const FS_BODY = %s;
const vs = `#version 300 es
in vec2 p; void main(){ gl_Position = vec4(p,0.,1.); }`;
const fsHead = `#version 300 es
precision highp float; precision highp int;
uniform vec3 iResolution; uniform float iTime; uniform float iTimeDelta; uniform int iFrame;
uniform vec4 iMouse; uniform vec4 iDate; uniform float iSampleRate; uniform float iFrameRate;
uniform float iChannelTime[4]; uniform vec3 iChannelResolution[4];
uniform sampler2D iChannel0; uniform sampler2D iChannel1; uniform sampler2D iChannel2; uniform sampler2D iChannel3;
out vec4 _fragColor;
`;
const fsTail = `
void main(){ mainImage(_fragColor, gl_FragCoord.xy); }`;
window.__result = {ok:false, err:null};
(function(){
  const c = document.getElementById('c');
  const gl = c.getContext('webgl2', {preserveDrawingBuffer:true});
  if (!gl) { window.__result.err = 'no webgl2'; return; }
  function sh(t, src){ const s = gl.createShader(t); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s).split('\\n')[0]); return s; }
  try {
    const body = FS_BODY.replace(/^\\s*#version[^\\n]*\\n/, '');
    const p = gl.createProgram();
    gl.attachShader(p, sh(gl.VERTEX_SHADER, vs));
    gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fsHead + body + fsTail));
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


def render_glsl(code, workdir, timeout=40):
    os.makedirs(workdir, exist_ok=True)
    html = HTML % json.dumps(code)
    p = os.path.join(workdir, "index.html")
    open(p, "w").write(html)
    rep = {"status": "FAIL", "error": None, "distinct": 0, "std": 0.0, "changed": False, "shot": None}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"])
            pg = b.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
            pg.goto("file://" + os.path.abspath(p), wait_until="load", timeout=timeout * 1000)
            res = pg.evaluate("window.__result")
            if not res or not res.get("ok"):
                rep["error"] = (res or {}).get("err") or (errs[0] if errs else "no result")
                b.close()
                return rep
            a = pg.evaluate("window.__draw(0.0)")
            c = pg.evaluate("window.__draw(1.7)")
            try:                                   # a screenshot failure must not discard a good render
                shot = os.path.join(workdir, "frame.png")
                pg.screenshot(path=shot)
                rep["shot"] = shot
            except Exception:
                rep["shot"] = None
            b.close()
        rep["distinct"] = max(a["distinct"], c["distinct"])
        rep["std"] = round(max(a["std"], c["std"]), 2)
        rep["changed"] = abs(a["mean"] - c["mean"]) > 0.5 or a["distinct"] != c["distinct"]
        rep["status"] = "OK" if (rep["distinct"] > 3 and rep["std"] > 2.0) else "STATIC"
        if rep["status"] == "STATIC":
            rep["error"] = f"uniform image (distinct={rep['distinct']}, std={rep['std']})"
    except Exception as e:
        rep["error"] = str(e)[:200]
    return rep


if __name__ == "__main__":
    import tempfile
    for f in sys.argv[1:]:
        r = render_glsl(open(f).read(), tempfile.mkdtemp())
        print(f"{r['status']:6s} distinct={r['distinct']:3d} std={r['std']:6.2f} animated={r['changed']} {os.path.basename(f)} {r['error'] or ''}")
