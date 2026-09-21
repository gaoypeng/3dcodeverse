"""three.js executor: load a single-file HTML page in headless Chromium (Playwright), screenshot, decide.

status OK   = page loaded, no uncaught JS error / failed module import / failed request, a <canvas> exists and
              the composited screenshot is not (nearly) uniform.
       EMPTY = no canvas or blank canvas.   FAIL = JS/import error.   TIMEOUT = page load exceeded the limit.

Pages usually import three.js from a CDN via an importmap, so the browser needs network access.  Set
``CV3D_THREE_LOCAL=/path/to/three/build`` (a directory holding ``three.module.js`` and ``jsm/``) to rewrite
``https://cdn.jsdelivr.net/npm/three@X/build/three.module.js`` / ``…/examples/jsm/`` (and the unpkg /
esm.sh equivalents) to that local copy and run offline.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

_CDN_RE = re.compile(r"https://(?:cdn\.jsdelivr\.net/npm|unpkg\.com|esm\.sh)/three@[^/\"']+/(build/three\.module\.js|examples/jsm/)")


def localise(html: str) -> str:
    local = os.environ.get("CV3D_THREE_LOCAL")
    if not local:
        return html
    root = Path(local).resolve()

    def sub(m: re.Match) -> str:
        tail = m.group(1)
        return f"file://{root}/three.module.js" if tail.startswith("build/") else f"file://{root}/jsm/"

    return _CDN_RE.sub(sub, html)


def run(code: str, workdir: str, timeout: int = 60, settle_ms: int = 4000) -> dict:
    wd = Path(workdir).resolve()
    wd.mkdir(parents=True, exist_ok=True)
    page_path = wd / "index.html"
    page_path.write_text(localise(code))
    rep: dict = {"status": "FAIL", "error": None, "console_errors": [], "canvas_nonblank": None, "latency_s": 0.0,
                 "shot": None, "mesh": None}
    t0 = time.time()
    try:
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                                                             "--ignore-gpu-blocklist", "--no-sandbox", "--disable-dev-shm-usage",
                                                             "--allow-file-access-from-files"])
            page = browser.new_page(viewport={"width": 640, "height": 480})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:300]))
            page.on("console", lambda m: errs.append("console.error: " + m.text[:300]) if m.type == "error" else None)
            page.on("requestfailed", lambda r: errs.append("requestfailed: " + r.url[:200]))
            try:
                page.goto("file://" + str(page_path), wait_until="load", timeout=timeout * 1000)
            except PWTimeout:
                rep.update(status="TIMEOUT", error=f"page load > {timeout}s")
                browser.close()
                rep["latency_s"] = round(time.time() - t0, 1)
                return rep
            page.wait_for_timeout(settle_ms)
            n_canvas = page.evaluate("document.querySelectorAll('canvas').length")
            shot = wd / "shot.png"
            page.screenshot(path=str(shot))
            rep["shot"] = str(shot)
            browser.close()
        rep["console_errors"] = errs[:10]
        fatal = [e for e in errs if e.startswith("pageerror") or "Failed to resolve module" in e or "Uncaught" in e
                 or e.startswith("requestfailed")]
        from PIL import Image
        import numpy as np
        im = np.array(Image.open(shot).convert("RGB"))
        q = im[::6, ::6].reshape(-1, 3) // 16
        distinct = len(set(map(tuple, q)))
        rep["shot_distinct"], rep["shot_std"] = distinct, float(im.std())
        nonblank = distinct > 6 and float(im.std()) > 4.0
        rep["canvas_nonblank"] = bool(n_canvas) and nonblank
        if fatal:
            rep.update(status="FAIL", error=fatal[0])
        elif not n_canvas:
            rep.update(status="EMPTY", error="no <canvas>")
        elif not nonblank:
            rep.update(status="EMPTY", error=f"blank canvas (distinct={distinct}, std={im.std():.1f})")
        else:
            rep["status"] = "OK"
    except Exception as e:  # noqa: BLE001
        rep["error"] = (type(e).__name__ + ": " + str(e))[:400]
        rep["status"] = "CRASH"
    rep["latency_s"] = round(time.time() - t0, 1)
    return rep
