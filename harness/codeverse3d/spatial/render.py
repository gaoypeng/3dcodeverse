"""Headless renders of the canonical GLB (three.js in headless Chrome, GPU when available).

``render_glb`` is the entry point; results are cached under ``<cache_dir>/renders/`` and copied into ``out_dir``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import shutil
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import RENDER_MODES, RenderSet, RenderView
from codeverse3d.conventions import OBJECT_VIEWS, ViewPreset
from codeverse3d.proc import sha256_file, unique_tmp
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
CACHE_VERSION = 4  # bump when the rig changes the pixels


log = logging.getLogger(__name__)


class RenderError(RuntimeError):
    """Rendering failed (node/puppeteer error, bad GLB, bad arguments)."""


def _cache_key(glb: Path, params: dict[str, Any]) -> str:
    blob = json.dumps({"v": CACHE_VERSION, "glb": sha256_file(glb), **params}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


#: Every file that can change what an object render looks like (host plumbing excluded); a test checks the import graph.
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
    """Hash of the JS rig files; editing the rig invalidates the cache."""
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
    """Render ``glb`` from ``views`` (default :data:`OBJECT_VIEWS`): ``view_<name>.png``, ``views.json`` and ``sheet.png``.

    Raises :class:`RenderError` on failure; never returns a partial RenderSet.
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
        # the requested mode, so a cache hit never serves the other backend's pixels
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
            # one retry with twice the time; a browser loss is retried on an owned browser
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
    # per-writer tmp + tolerant rename: concurrent judge threads must not share a tmp dir
    tmp = unique_tmp(cache_dir)
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        tmp.mkdir(parents=True)
        for v in record["views"]:
            shutil.copy2(out_dir / f"view_{v['name']}.png", tmp / f"view_{v['name']}.png")
        (tmp / "views.json").write_text(json.dumps(record, indent=1))
        with contextlib.suppress(OSError):  # a concurrent writer won; keep theirs
            tmp.replace(cache_dir)
    finally:  # never leave the .tmp behind
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
