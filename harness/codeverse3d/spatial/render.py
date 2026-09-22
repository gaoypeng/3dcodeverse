"""Headless renders of the canonical GLB (three.js in headless Chrome, GPU when available).

``render_glb`` is the one entry point the tracks/judges/tools use.  Results are
cached under ``<cache_dir>/renders/<sha256(glb)+params>/`` so repeated judge
calls on the same artifact do not re-render; cached PNGs are copied into the
requested ``out_dir`` so every call still yields a self-contained directory.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import RENDER_MODES, RenderSet, RenderView
from codeverse3d.conventions import OBJECT_VIEWS, ViewPreset
from codeverse3d.proc import sha256_file
from codeverse3d.spatial._render_common import build_sheet, out_directory, view_specs
from codeverse3d.spatial.node import (
    OWN_BROWSER_ENV,
    NodeError,
    browser_was_lost,
    run_node,
    runtime_js_dir,
    transient_failure,
)

BACKGROUNDS = ("studio", "white", "transparent")
CACHE_VERSION = 4  # bump when the rig changes in a way that invalidates cached PNGs


log = logging.getLogger(__name__)


class RenderError(RuntimeError):
    """Rendering failed (node/puppeteer error, bad GLB, bad arguments)."""


def _cache_key(glb: Path, params: dict[str, Any]) -> str:
    blob = json.dumps({"v": CACHE_VERSION, "glb": sha256_file(glb), **params}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


#: Every file that can change what an object render LOOKS like: the driver, the
#: page-side rig, the studio (environment/backdrop/lights) and the camera-fit math.
#: Host plumbing (lib/cli.mjs, lib/host_env.mjs, lib/host_page.mjs) is deliberately
#: NOT here — it cannot change a pixel, and hashing it would drop every cached PNG
#: on an unrelated edit.  ``tests/threejs_render/test_studio_rig.py`` walks the real
#: import graph and fails when a new page-side module is missing from this list.
RIG_FILES = (
    "render_glb.mjs",
    "lib/browser/render_rig.js",
    "lib/browser/studio.js",
    "lib/browser/studio_env.js",
    "lib/browser/renderer.js",
    "lib/browser/camera_fit.js",
    "lib/orbit.mjs",
)


def _rig_signature() -> str:
    """Hash of the JS rig files so editing the rig invalidates the cache."""
    rt = runtime_js_dir()
    h = hashlib.sha256()
    for rel in RIG_FILES:
        p = rt / rel
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def render_glb(
    glb: Path | str,
    out_dir: Path | str,
    *,
    views: Sequence[ViewPreset] | None = None,
    mode: str = "shaded",
    width: int = 768,
    height: int = 768,
    isolate: list[str] | None = None,
    explode: float = 0.0,
    sheet: bool = True,
    background: str = "studio",
    anim_time: float | None = None,
    shadow: bool = True,
    gpu: str | None = None,
    timeout_s: float | None = None,
    use_cache: bool = True,
) -> RenderSet:
    """Render ``glb`` from ``views`` (default :data:`OBJECT_VIEWS`) into ``out_dir``.

    Writes ``view_<name>.png`` per view, ``views.json`` (camera data) and, when
    ``sheet`` is true, ``sheet.png`` (labelled contact sheet).  Raises
    :class:`RenderError` on failure — never returns a partial RenderSet.
    """
    glb = Path(glb).resolve()
    out_dir = Path(out_dir).resolve()
    if not glb.is_file():
        raise RenderError(f"GLB not found: {glb}")
    if mode not in RENDER_MODES:
        raise RenderError(f"mode must be one of {RENDER_MODES}, got {mode!r}")
    if background not in BACKGROUNDS:
        raise RenderError(f"background must be one of {BACKGROUNDS}, got {background!r}")
    view_list = list(views) if views is not None else list(OBJECT_VIEWS)
    if not view_list:
        raise RenderError("no views requested")
    settings = get_settings()
    gpu = gpu or settings.render.gpu
    timeout_s = timeout_s or settings.limits.render_timeout_s
    params: dict[str, Any] = {
        "views": view_specs(view_list),
        "mode": mode,
        "width": int(width),
        "height": int(height),
        "isolate": sorted(isolate or []),
        "explode": float(explode),
        "background": background,
        "anim_time": anim_time,
        "shadow": bool(shadow),
        # requested mode ('auto' fragments from 'on'/'off' — accepted over a hit
        # serving the other backend's pixels and lying about RenderSet.renderer)
        "gpu": gpu,
        "rig": _rig_signature(),
    }
    t0 = time.time()
    cache_dir = settings.cache_dir / "renders" / _cache_key(glb, params)
    out_directory(out_dir)
    record: dict[str, Any] | None = None
    if use_cache and (cache_dir / "views.json").is_file():
        record = _restore_from_cache(cache_dir, out_dir)
    if record is None:
        try:
            record = _run_render(glb, out_dir, params, gpu=gpu, timeout_s=timeout_s)
        except RenderError as e:
            if not transient_failure(e):
                raise
            # compare_art_v4 (2026-08-28): under 12 concurrent cells the browser took 7.5 s to
            # open a page and the viewer's wait expired ("Waiting failed"); the cell lost its
            # in-loop judge for the round.  One retry with twice the time is cheap next to that.
            # A browser LOSS is retried on a browser of our own: the shared one
            # advertised in the cache is the suspect, so re-using it repeats the failure.
            own = browser_was_lost(e)
            log.warning(
                "render_glb transient failure, retrying once with %.0f s%s: %s",
                timeout_s * 2, " on an owned browser" if own else "", str(e)[:160],
            )
            time.sleep(RETRY_PAUSE_S)
            record = _run_render(glb, out_dir, params, gpu=gpu, timeout_s=timeout_s * 2, own_browser=own)
        if use_cache:
            _store_in_cache(cache_dir, out_dir, record)

    rviews = [
        RenderView(
            name=v["name"],
            path=str(out_dir / f"view_{v['name']}.png"),
            mode=mode,
            width=int(v.get("width", width)),
            height=int(v.get("height", height)),
            camera_position=tuple(v["camera_position"]) if v.get("camera_position") else None,
            look_at=tuple(v["look_at"]) if v.get("look_at") else None,
            fov=v.get("fov"),
            time_s=anim_time,
        )
        for v in record["views"]
    ]
    sheet_path = build_sheet([(v.name, v.path) for v in rviews], out_dir / "sheet.png") if sheet else None
    return RenderSet(
        views=rviews,
        contact_sheet=sheet_path,
        renderer=str(record.get("renderer", "")),
        duration_ms=int((time.time() - t0) * 1000),
        console_errors=[str(e) for e in record.get("console_errors", [])],
    )


#: render failures that are the box, not the model: a retry is worth one more timeout.
#: The vocabulary lives in `spatial/node.py` next to the browser-loss half, shared with
#: the scene drivers since 2026-09-07 (they used to retry only a browser loss).
RETRY_PAUSE_S = 3.0


def _run_render(
    glb: Path, out_dir: Path, params: dict[str, Any], *, gpu: str, timeout_s: float, own_browser: bool = False
) -> dict[str, Any]:
    args = [
        "--glb", str(glb), "--out", str(out_dir), "--views", json.dumps(params["views"]),
        "--mode", params["mode"], "--width", str(params["width"]), "--height", str(params["height"]),
        "--explode", str(params["explode"]), "--background", params["background"],
        "--gpu", gpu, "--shadow", "1" if params["shadow"] else "0", "--timeout-s", str(int(timeout_s)),
    ]
    if params["isolate"]:
        args += ["--isolate", ",".join(params["isolate"])]
    if params["anim_time"] is not None:
        args += ["--anim-time", str(params["anim_time"])]
    try:
        res = run_node(
            runtime_js_dir() / "render_glb.mjs", args, timeout_s=timeout_s + 30,
            env_extra=OWN_BROWSER_ENV if own_browser else None,
        )
    except NodeError as e:
        detail = ""
        if e.result and e.result.last_json:
            detail = json.dumps(e.result.last_json)[:2000]
        raise RenderError(f"render_glb failed: {e}\n{detail}") from e
    record = res.last_json
    if not record or not record.get("ok"):
        raise RenderError(f"render_glb produced no result record; stderr tail:\n{res.stderr_tail}")
    for v in record["views"]:
        if not (out_dir / f"view_{v['name']}.png").is_file():
            raise RenderError(f"render_glb reported view {v['name']} but the PNG is missing")
    return record


def _store_in_cache(cache_dir: Path, out_dir: Path, record: dict[str, Any]) -> None:
    # per-writer tmp + tolerant rename (the vlm_judge._render_slices shape): a pid-only
    # name let two judge threads share a tmp dir and delete each other's half-copied PNGs
    tmp = cache_dir.parent / f".{cache_dir.name}.{os.getpid()}-{threading.get_ident()}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        tmp.mkdir(parents=True)
        for v in record["views"]:
            shutil.copy2(out_dir / f"view_{v['name']}.png", tmp / f"view_{v['name']}.png")
        (tmp / "views.json").write_text(json.dumps(record, indent=1))
        with contextlib.suppress(OSError):  # a concurrent identical writer won the race; keep theirs
            tmp.replace(cache_dir)
    finally:  # a mid-copy crash (or losing the race) must not leave the .tmp behind
        shutil.rmtree(tmp, ignore_errors=True)


def _restore_from_cache(cache_dir: Path, out_dir: Path) -> dict[str, Any] | None:
    try:
        record = json.loads((cache_dir / "views.json").read_text())
        for v in record["views"]:
            shutil.copy2(cache_dir / f"view_{v['name']}.png", out_dir / f"view_{v['name']}.png")
    except (OSError, KeyError, json.JSONDecodeError):
        return None
    record["from_cache"] = True
    (out_dir / "views.json").write_text(json.dumps(record, indent=1))
    return record


__all__ = ["render_glb", "RenderError", "BACKGROUNDS"]
