"""Render many Shadertoy shaders in ONE persistent WebGL2 context.

Launching a browser per shader costs 3-5 s and dominates everything (the first version of this check ran for
3h43m without finishing 250 shaders). The page below compiles a new fragment program in place, so each shader
costs ~50-150 ms. Two frames are sampled (iTime 0 and 1.7 s) and the canvas is read back with readPixels — no
screenshots, which is what silently discarded good renders in the first version.

    with BatchRenderer() as r:
        rep = r.run(shader_source)   # {status: OK|STATIC|FAIL, distinct, std, changed, error}
"""
import os

HARNESS = """<!doctype html><html><body style="margin:0"><canvas id=c width=320 height=240></canvas><script>
const c = document.getElementById('c');
const gl = c.getContext('webgl2', {preserveDrawingBuffer:true});
const VS = `#version 300 es
in vec2 p; void main(){ gl_Position = vec4(p,0.,1.); }`;
const HEAD = `#version 300 es
precision highp float; precision highp int;
uniform vec3 iResolution; uniform float iTime; uniform float iTimeDelta; uniform int iFrame;
uniform vec4 iMouse; uniform vec4 iDate; uniform float iSampleRate; uniform float iFrameRate;
uniform float iChannelTime[4]; uniform vec3 iChannelResolution[4];
uniform sampler2D iChannel0; uniform sampler2D iChannel1; uniform sampler2D iChannel2; uniform sampler2D iChannel3;
out vec4 _fragColor;
`;
const TAIL = `
void main(){ mainImage(_fragColor, gl_FragCoord.xy); }`;
let vs = null, buf = null, tex = null;
if (gl) {
  vs = gl.createShader(gl.VERTEX_SHADER); gl.shaderSource(vs, VS); gl.compileShader(vs);
  buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 3,-1, -1,3]), gl.STATIC_DRAW);
  tex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([128,128,128,255]));
}
function measure(){
  const px = new Uint8Array(c.width*c.height*4);
  gl.readPixels(0,0,c.width,c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
  const seen = new Set(); let sum=0, sum2=0, n=0;
  for (let i=0;i<px.length;i+=4*7){ const r=px[i]>>4, g=px[i+1]>>4, b=px[i+2]>>4;
    seen.add((r<<8)|(g<<4)|b); const v=(px[i]+px[i+1]+px[i+2])/3; sum+=v; sum2+=v*v; n++; }
  const mean=sum/n; return {distinct: seen.size, std: Math.sqrt(Math.max(0, sum2/n-mean*mean)), mean};
}
window.__run = function(src){
  if (!gl) return {status:'FAIL', error:'no webgl2', distinct:0, std:0, changed:false};
  let prog = null, fs = null;
  try {
    const body = src.replace(/^\\s*#version[^\\n]*\\n/, '');
    fs = gl.createShader(gl.FRAGMENT_SHADER); gl.shaderSource(fs, HEAD + body + TAIL); gl.compileShader(fs);
    if (!gl.getShaderParameter(fs, gl.COMPILE_STATUS))
      return {status:'FAIL', error:(gl.getShaderInfoLog(fs)||'').split('\\n')[0].slice(0,140), distinct:0, std:0, changed:false};
    prog = gl.createProgram(); gl.attachShader(prog, vs); gl.attachShader(prog, fs); gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS))
      return {status:'FAIL', error:(gl.getProgramInfoLog(prog)||'').split('\\n')[0].slice(0,140), distinct:0, std:0, changed:false};
    gl.useProgram(prog);
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    const loc = gl.getAttribLocation(prog, 'p');
    if (loc >= 0) { gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0); }
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, tex);
    for (let i=0;i<4;i++){ const u = gl.getUniformLocation(prog,'iChannel'+i); if (u) gl.uniform1i(u, 0); }
    const out = [];
    for (const t of [0.0, 1.7]) {
      gl.uniform3f(gl.getUniformLocation(prog,'iResolution'), c.width, c.height, 1);
      gl.uniform1f(gl.getUniformLocation(prog,'iTime'), t);
      gl.uniform1f(gl.getUniformLocation(prog,'iTimeDelta'), 1/60);
      gl.uniform1i(gl.getUniformLocation(prog,'iFrame'), Math.floor(t*60));
      gl.uniform1f(gl.getUniformLocation(prog,'iFrameRate'), 60);
      gl.clearColor(0,0,0,1); gl.clear(gl.COLOR_BUFFER_BIT);
      gl.viewport(0,0,c.width,c.height); gl.drawArrays(gl.TRIANGLES, 0, 3);
      out.push(measure());
    }
    const a = out[0], b = out[1];
    const distinct = Math.max(a.distinct, b.distinct), std = Math.max(a.std, b.std);
    return {status: (distinct > 3 && std > 2.0) ? 'OK' : 'STATIC', distinct: distinct, std: std,
            changed: Math.abs(a.mean-b.mean) > 0.5 || a.distinct !== b.distinct, error: null};
  } catch(e) {
    return {status:'FAIL', error:String((e && e.message) || e).slice(0,140), distinct:0, std:0, changed:false};
  } finally { if (prog) gl.deleteProgram(prog); if (fs) gl.deleteShader(fs); }
};
window.__ready = true;
</script></body></html>"""


class BatchRenderer:
    def __init__(self, timeout_ms=20000):
        self.timeout_ms = timeout_ms
        self._pw = self._b = self._pg = None

    def _new_page(self):
        self._pg = self._b.new_page()
        self._pg.set_default_timeout(self.timeout_ms)
        self._pg.set_content(HARNESS, wait_until="load")
        self._pg.wait_for_function("window.__ready === true", timeout=self.timeout_ms)

    def __enter__(self):
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/wekafs/ict/hx_624/cache/ms-playwright")
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._b = self._pw.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"])
        self._new_page()
        return self

    def _relaunch(self):
        """a crashed page can be replaced; a crashed browser cannot — rebuild whichever died, so that one bad
        shader cannot turn every later shader handled by this worker into a false FAIL"""
        for x in (self._pg, self._b):
            try:
                x.close()
            except Exception:
                pass
        self._b = self._pw.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"])
        self._new_page()

    def run(self, src, _retry=True):
        try:
            return self._pg.evaluate("s => window.__run(s)", src)
        except Exception as e:
            msg = str(e)[:140]
            try:
                self._new_page()
            except Exception:
                try:
                    self._relaunch()
                except Exception:
                    return {"status": "FAIL", "error": "renderer unrecoverable: " + msg,
                            "distinct": 0, "std": 0.0, "changed": False}
            if _retry:                       # one honest second chance on a fresh context
                try:
                    return self.run(src, _retry=False)
                except Exception:
                    pass
            return {"status": "FAIL", "error": msg, "distinct": 0, "std": 0.0, "changed": False}

    def __exit__(self, *a):
        for x in (self._pg, self._b):
            try:
                x.close()
            except Exception:
                pass
        try:
            self._pw.stop()
        except Exception:
            pass
