"""Render a ``scene_threejs`` workspace through the node host (headless GPU Chrome).

``render_scene(ws, out_dir, ...)`` drives ``runtime_js/render_scene.mjs``:
authored cameras + an overview orbit rig (``conventions.SCENE_VIEWS``) at one
or more animation times, deterministic instruments (console/shader errors,
fps, census, camera checks) and a labelled contact sheet.  Returns a
``RenderSet``; the full instrument payload is left at ``out_dir/metrics.json``.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codeverse.config import get_settings
from codeverse.contracts.artifacts import RenderSet, RenderView
from codeverse.contracts.plan import CameraPlan
from codeverse.conventions import SCENE_VIEWS, ViewPreset
from codeverse.workspace import Workspace


class SceneRenderError(RuntimeError):
    """The node driver could not run (missing node/deps, timeout, crash)."""


@dataclass
class NodeResult:
    """Outcome of one node driver run; ``summary`` is the last stdout JSON line."""

    returncode: int
    stdout: str
    stderr: str
    summary: dict[str, Any]
    duration_ms: int


def runtime_js_dir() -> Path:
    return get_settings().runtime_js_dir()


def run_scene_script(script: str, args: Sequence[str], *, timeout_s: float, cwd: Path | None = None) -> NodeResult:
    """Run ``runtime_js/<script>`` with node; kill the whole process group on timeout.

    Goes through ``codeverse.spatial.node.run_node`` (package C1) when it is
    importable, else a local equivalent.  The driver prints a JSON summary as
    its LAST stdout line; exit code 2/3 means the driver itself failed →
    ``SceneRenderError``.
    """
    rt = runtime_js_dir()
    path = rt / script
    if not path.is_file():
        raise SceneRenderError(f"missing node driver {path}")
    env_extra: dict[str, str] = {}
    gpu = get_settings().render.gpu
    if gpu and "C3V_RENDER_GPU" not in os.environ:
        env_extra["C3V_RENDER_GPU"] = gpu
    try:
        from codeverse.spatial.node import NodeError, run_node
    except ImportError:  # pragma: no cover - C1 not present
        rc, out, err, dur = _run_node_local(path, args, timeout_s=timeout_s, cwd=cwd or rt, env_extra=env_extra)
    else:
        try:
            r = run_node(path, list(map(str, args)), cwd=cwd or rt, timeout_s=timeout_s, env_extra=env_extra, check=False)
        except NodeError as e:
            raise SceneRenderError(f"{script} could not run: {e}") from e
        if r.timed_out:
            raise SceneRenderError(f"{script} timed out after {timeout_s:.0f}s\n{r.stderr_tail}")
        rc, out, err, dur = r.rc, r.stdout, r.stderr, r.duration_ms
    summary = _last_json_line(out)
    if rc in (2, 3) or (rc != 0 and not summary):
        raise SceneRenderError(
            f"{script} failed (exit {rc}): {summary.get('error') if summary else ''}\n"
            f"stderr tail: {(err or '')[-2000:]}\nstdout tail: {(out or '')[-1000:]}"
        )
    return NodeResult(rc, out, err, summary, dur)


def _run_node_local(path: Path, args: Sequence[str], *, timeout_s: float, cwd: Path, env_extra: dict[str, str]) -> tuple[int, str, str, int]:
    node = get_settings().binaries.node or "node"
    env = dict(os.environ)
    env.setdefault("NODE_PATH", str(path.parent / "node_modules"))
    env.update(env_extra)
    t0 = time.time()
    proc = subprocess.Popen(
        [node, str(path), *map(str, args)], cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        raise SceneRenderError(f"{path.name} timed out after {timeout_s:.0f}s\n{(err or '')[-2000:]}") from None
    return proc.returncode, out, err, int((time.time() - t0) * 1000)


def _last_json_line(stdout: str) -> dict[str, Any]:
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


def _camera_json(cams: Sequence[CameraPlan]) -> str:
    return json.dumps([
        {"name": c.name, "position": list(c.position), "lookAt": list(c.look_at), "fov": c.fov} for c in cams
    ])


def _views_json(views: Sequence[ViewPreset]) -> str:
    return json.dumps([{"name": v.name, "azimuth": v.azimuth_deg, "elevation": v.elevation_deg} for v in views])


def _contact_sheet(images: list[tuple[str, Path]], out: Path, cols: int, tile: int) -> Path | None:
    """Use ``codeverse.spatial.sheet.contact_sheet`` when present, else a local PIL grid."""
    if not images:
        return None
    try:
        from codeverse.spatial.sheet import contact_sheet  # package C1

        return Path(contact_sheet(images, out, cols=cols, tile=tile))
    except ImportError:
        return _local_contact_sheet(images, out, cols=cols, tile=tile)


def _local_contact_sheet(images: list[tuple[str, Path]], out: Path, *, cols: int = 4, tile: int = 384) -> Path:
    from PIL import Image, ImageDraw

    cols = max(1, min(cols, len(images)))
    rows = (len(images) + cols - 1) // cols
    th = int(tile * 9 / 16) + 18
    sheet = Image.new("RGB", (cols * tile, rows * th), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    for i, (label, p) in enumerate(images):
        x, y = (i % cols) * tile, (i // cols) * th
        try:
            im = Image.open(p).convert("RGB")
            im.thumbnail((tile, th - 18))
            sheet.paste(im, (x + (tile - im.width) // 2, y + 18))
        except OSError:
            draw.rectangle([x, y + 18, x + tile, y + th], fill=(60, 0, 0))
        draw.rectangle([x, y, x + tile, y + 18], fill=(0, 0, 0))
        draw.text((x + 4, y + 3), label[: tile // 7], fill=(255, 255, 255))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def render_scene(
    ws: Workspace,
    out_dir: Path,
    *,
    cameras: list[CameraPlan] | None = None,
    orbit: bool = True,
    times: Sequence[float] = (0.0, 1.5),
    width: int = 1024,
    height: int = 576,
    sheet: bool = True,
    orbit_views: Sequence[ViewPreset] = SCENE_VIEWS,
    fps_seconds: float = 2.0,
    counterfactual: bool = False,
    timeout_s: float | None = None,
) -> RenderSet:
    """Render authored (+ orbit) views at ``times``; returns a RenderSet.

    ``cameras=None`` uses the cameras authored in ``createScene()``.  A scene
    that fails to boot yields an empty RenderSet whose ``console_errors`` say
    why (the build gate normally catches that earlier); a driver failure raises
    ``SceneRenderError``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    args: list[str] = [
        "--ws", str(ws.root), "--out", str(out_dir),
        "--cameras", _camera_json(cameras) if cameras else "authored",
        "--orbit-views", _views_json(orbit_views) if orbit else "none",
        "--times", ",".join(f"{t:g}" for t in times),
        "--width", str(width), "--height", str(height),
        "--fps-seconds", f"{fps_seconds:g}",
    ]
    if counterfactual:
        args.append("--counterfactual")
    tmo = timeout_s or settings.limits.render_timeout_s
    args += ["--timeout-ms", str(int(tmo * 1000))]
    res = run_scene_script("render_scene.mjs", args, timeout_s=tmo + 30)
    metrics = _read_json(out_dir / "metrics.json")
    views: list[RenderView] = []
    for v in metrics.get("views", []):
        views.append(RenderView(
            name=v["name"], path=str(out_dir / v["path"]), mode="shaded", width=width, height=height,
            camera_position=tuple(v["position"]), look_at=tuple(v["lookAt"]), fov=v.get("fov"),
            time_s=v.get("time_s"),
        ))
    sheet_path = None
    if sheet and views:
        labelled = [(f"{v.name} t={v.time_s:g}", Path(v.path)) for v in views if "_nocustom" not in v.name]
        sheet_path = _contact_sheet(labelled, out_dir / "sheet.png", settings.render.sheet_cols, settings.render.sheet_tile)
    fps = (metrics.get("fps") or {}).get("fps")
    errors = list(metrics.get("console_errors", []))
    for e in metrics.get("shader_errors", []):
        errors.append(f"shader[{e.get('stage')}]: {e.get('message')} — {e.get('source_line', '')}".strip())
    if not metrics and res.summary.get("error"):
        errors.append(str(res.summary["error"]))
    return RenderSet(
        views=views, contact_sheet=str(sheet_path) if sheet_path else None,
        renderer=str(metrics.get("renderer") or res.summary.get("renderer") or ""),
        duration_ms=res.duration_ms, console_errors=errors,
        fps=float(fps) if fps is not None else None,
    )


def _read_json(p: Path) -> dict[str, Any]:
    if not p.is_file():
        return {}
    return json.loads(p.read_text())


def read_metrics(out_dir: Path) -> dict[str, Any]:
    """Full instrument payload written by the driver (census, camera_checks, ...)."""
    return _read_json(Path(out_dir) / "metrics.json")
