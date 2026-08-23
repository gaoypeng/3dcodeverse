"""Headless renders of the canonical GLB (three.js in headless Chrome, GPU when available).

``render_glb`` is the one entry point the tracks/judges/tools use.  Results are
cached under ``<cache_dir>/renders/<sha256(glb)+params>/`` so repeated judge
calls on the same artifact do not re-render; cached PNGs are copied into the
requested ``out_dir`` so every call still yields a self-contained directory.

``render_scene`` delegates to ``codeverse.spatial.render_scene`` (package C2).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codeverse.config import get_settings
from codeverse.contracts.artifacts import RenderSet, RenderView
from codeverse.contracts.plan import CameraPlan
from codeverse.conventions import OBJECT_VIEWS, ViewPreset
from codeverse.spatial.node import NodeError, run_node, runtime_js_dir
from codeverse.spatial.render_scene import render_scene as _render_scene_impl
from codeverse.spatial.sheet import contact_sheet
from codeverse.workspace import Workspace

MODES = ("shaded", "wire", "normals", "silhouette", "clay")
BACKGROUNDS = ("studio", "white", "transparent")
CACHE_VERSION = 3  # bump when the rig changes in a way that invalidates cached PNGs


class RenderError(RuntimeError):
    """Rendering failed (node/puppeteer error, bad GLB, bad arguments)."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _views_payload(views: Sequence[ViewPreset]) -> list[dict[str, Any]]:
    return [{"name": v.name, "azimuth": float(v.azimuth_deg), "elevation": float(v.elevation_deg)} for v in views]


def _cache_key(glb: Path, params: dict[str, Any]) -> str:
    blob = json.dumps({"v": CACHE_VERSION, "glb": _sha256_file(glb), **params}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _rig_signature() -> str:
    """Hash of the JS rig files so editing the rig invalidates the cache."""
    rt = runtime_js_dir()
    h = hashlib.sha256()
    for rel in ("render_glb.mjs", "lib/browser/render_rig.js", "lib/browser/studio.js", "lib/browser/camera_fit.js"):
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
    if mode not in MODES:
        raise RenderError(f"mode must be one of {MODES}, got {mode!r}")
    if background not in BACKGROUNDS:
        raise RenderError(f"background must be one of {BACKGROUNDS}, got {background!r}")
    view_list = list(views) if views is not None else list(OBJECT_VIEWS)
    if not view_list:
        raise RenderError("no views requested")
    settings = get_settings()
    gpu = gpu or settings.render.gpu
    timeout_s = timeout_s or settings.limits.render_timeout_s
    params: dict[str, Any] = {
        "views": _views_payload(view_list),
        "mode": mode,
        "width": int(width),
        "height": int(height),
        "isolate": sorted(isolate or []),
        "explode": float(explode),
        "background": background,
        "anim_time": anim_time,
        "shadow": bool(shadow),
        "rig": _rig_signature(),
    }
    t0 = time.time()
    cache_dir = settings.cache_dir / "renders" / _cache_key(glb, params)
    out_dir.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] | None = None
    if use_cache and (cache_dir / "views.json").is_file():
        record = _restore_from_cache(cache_dir, out_dir)
    if record is None:
        record = _run_render(glb, out_dir, params, gpu=gpu, timeout_s=timeout_s)
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
    sheet_path = None
    if sheet:
        sheet_path = str(
            contact_sheet(
                [(v.name, v.path) for v in rviews],
                out_dir / "sheet.png",
                cols=settings.render.sheet_cols,
                tile=settings.render.sheet_tile,
            )
        )
    return RenderSet(
        views=rviews,
        contact_sheet=sheet_path,
        renderer=str(record.get("renderer", "")),
        duration_ms=int((time.time() - t0) * 1000),
        console_errors=[str(e) for e in record.get("console_errors", [])],
    )


def _run_render(glb: Path, out_dir: Path, params: dict[str, Any], *, gpu: str, timeout_s: float) -> dict[str, Any]:
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
        res = run_node(runtime_js_dir() / "render_glb.mjs", args, timeout_s=timeout_s + 30)
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
    tmp = cache_dir.parent / f".{cache_dir.name}.{os.getpid()}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    for v in record["views"]:
        shutil.copy2(out_dir / f"view_{v['name']}.png", tmp / f"view_{v['name']}.png")
    (tmp / "views.json").write_text(json.dumps(record, indent=1))
    if cache_dir.exists():  # another process won the race; keep theirs
        shutil.rmtree(tmp)
        return
    tmp.replace(cache_dir)


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


def render_turntable(
    glb: Path | str,
    out: Path | str,
    *,
    n: int = 24,
    elevation_deg: float = 18.0,
    mode: str = "shaded",
    width: int = 512,
    height: int = 512,
    fps: int = 12,
) -> Path:
    """Render ``n`` azimuth steps and assemble ``out`` (.mp4 via ffmpeg, or .gif via PIL)."""
    from codeverse.spatial.turntable import assemble_turntable

    out = Path(out)
    frames_dir = out.parent / f".{out.stem}_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    views = [ViewPreset(f"tt{i:03d}", 360.0 * i / n, elevation_deg) for i in range(n)]
    rs = render_glb(glb, frames_dir, views=views, mode=mode, width=width, height=height, sheet=False)
    return assemble_turntable([Path(v.path) for v in rs.views], out, fps=fps)


def render_scene(
    ws: Workspace,
    out_dir: Path | str,
    *,
    cameras: list[CameraPlan] | None = None,
    orbit: bool = True,
    times: Sequence[float] = (0.0, 1.5),
    width: int = 1024,
    height: int = 576,
    sheet: bool = True,
) -> RenderSet:
    """Scene renders: delegates to ``codeverse.spatial.render_scene`` (narrower kwarg surface)."""
    return _render_scene_impl(ws, Path(out_dir), cameras=cameras, orbit=orbit, times=times, width=width, height=height, sheet=sheet)


__all__ = ["render_glb", "render_turntable", "render_scene", "RenderError", "MODES", "BACKGROUNDS"]
