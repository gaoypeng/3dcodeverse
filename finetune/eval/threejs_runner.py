"""Headless-Chromium executor for single-file three.js HTML (Playwright). Returns dict(status, error, console_errors, canvas_nonblank, latency_s, shot).
status OK = page loaded, no uncaught JS error / failed module import, a <canvas> exists and its rendered pixels are not (nearly) uniform."""
import os, sys, json, time, base64, io
def run_threejs(html, workdir, timeout=40, settle_ms=4000):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/wekafs/ict/hx_624/cache/ms-playwright")
    workdir = os.path.abspath(workdir); os.makedirs(workdir, exist_ok=True)
    hp = os.path.join(workdir, "index.html"); open(hp, "w").write(html)
    rep = {"status": "FAIL", "error": None, "console_errors": [], "canvas_nonblank": None, "latency_s": 0, "shot": None}
    t0 = time.time()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(viewport={"width": 640, "height": 480})
            errs = []
            page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:300]))
            page.on("console", lambda m: errs.append("console.error: " + m.text[:300]) if m.type == "error" else None)
            page.on("requestfailed", lambda r: errs.append("requestfailed: " + r.url[:200]))
            page.goto("file://" + hp, wait_until="load", timeout=timeout * 1000)
            page.wait_for_timeout(settle_ms)
            # canvas check: count distinct pixels over a downsampled grid
            info = page.evaluate("""() => {
                const cs = document.querySelectorAll('canvas'); if (!cs.length) return {n:0};
                const c = cs[0]; const w=c.width, h=c.height; if (!w||!h) return {n:cs.length, w, h, distinct:0};
                let gl = c.getContext('webgl2') || c.getContext('webgl');
                let pix = null;
                if (gl) { pix = new Uint8Array(w*h*4); try { gl.readPixels(0,0,w,h,gl.RGBA,gl.UNSIGNED_BYTE,pix); } catch(e) { pix=null; } }
                if (!pix) { try { const t=document.createElement('canvas'); t.width=w; t.height=h; const ctx=t.getContext('2d'); ctx.drawImage(c,0,0); pix=ctx.getImageData(0,0,w,h).data; } catch(e) { return {n:cs.length, w, h, distinct:-1, err:String(e)}; } }
                const seen = new Set(); const step = Math.max(1, Math.floor((w*h)/4000));
                for (let i=0;i<w*h;i+=step){ const o=i*4; seen.add((pix[o]>>3)+','+(pix[o+1]>>3)+','+(pix[o+2]>>3)); if (seen.size>64) break; }
                return {n:cs.length, w, h, distinct:seen.size};
            }""")
            shot = os.path.join(workdir, "shot.png"); page.screenshot(path=shot); rep["shot"] = shot
            browser.close()
        rep["console_errors"] = errs[:10]
        fatal = [e for e in errs if e.startswith("pageerror") or "Failed to resolve module" in e or "Uncaught" in e or e.startswith("requestfailed")]
        # blank check from the composited screenshot (the WebGL drawing buffer is cleared after compositing, so in-page readPixels is unreliable)
        try:
            from PIL import Image; import numpy as np
            im = np.array(Image.open(shot).convert("RGB")); q = (im[::6, ::6].reshape(-1, 3) // 16)
            distinct = len(set(map(tuple, q))); rep["shot_distinct"] = distinct; rep["shot_std"] = float(im.std())
            nonblank = distinct > 6 and float(im.std()) > 4.0
        except Exception as e:
            nonblank = info.get("distinct", 0) > 3
        rep["canvas_nonblank"] = bool(info.get("n", 0)) and nonblank
        if fatal: rep["status"] = "FAIL"; rep["error"] = fatal[0]
        elif not info.get("n"): rep["status"] = "EMPTY"; rep["error"] = "no <canvas>"
        elif not rep["canvas_nonblank"]: rep["status"] = "EMPTY"; rep["error"] = f"blank canvas (shot distinct={rep.get('shot_distinct')})"
        else: rep["status"] = "OK"
    except Exception as e:
        rep["error"] = (type(e).__name__ + ": " + str(e))[:400]
        if "Timeout" in rep["error"]: rep["status"] = "TIMEOUT"
    rep["latency_s"] = round(time.time() - t0, 1)
    return rep
if __name__ == "__main__":
    print(json.dumps(run_threejs(open(sys.argv[1]).read(), sys.argv[2]), indent=1))
