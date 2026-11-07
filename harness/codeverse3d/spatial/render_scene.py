"""Render a ``scene_threejs`` workspace through the node host (headless GPU Chrome).

``render_scene`` drives ``runtime_js/render_scene.mjs``: authored cameras + a fitted orbit rig at one or more
times, with deterministic instruments left in ``out_dir/metrics.json`` and a contact sheet of the judge subset.
Each view carries ``judge: true|false``, stamped once from ``select_judge_views``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import RenderSet, RenderView
from codeverse3d.contracts.plan import BBox, CameraPlan
from codeverse3d.conventions import SCENE_TIMES, SCENE_VIEWS, ViewPreset
from codeverse3d.proc import read_json_or_none, write_json_atomic
from codeverse3d.spatial._render_common import build_sheet, out_directory, view_specs
from codeverse3d.spatial.node import (
    OWN_BROWSER_ENV,
    NodeError,
    NodeResult,
    browser_was_lost,
    run_node,
    runtime_js_dir,
    transient_failure,
)
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

#: views the judge sees (contact sheet + individual images are built from these)
JUDGE_MAX_VIEWS = 10
#: overview-rig tiles the judge gets; the eye-level rig views are diagnostics only
MAX_JUDGE_OVERVIEWS = 4


#: ``probe_scene.mjs`` watchdog phases that are the scene's own work; every other phase, or a process-clock kill, is the box's.
SCENE_PHASES = frozenset({"scene", "census", "update"})


class SceneRenderError(RuntimeError):
    """The node driver could not run (missing node/deps, timeout, crash).

    ``timed_out``: the driver ran out of budget after the retry; ``phase`` is the watchdog's phase (may be empty)."""

    def __init__(self, message: str, *, timed_out: bool = False, phase: str = ""):
        super().__init__(message)
        self.timed_out = timed_out
        self.phase = phase

    @property
    def scene_timed_out(self) -> bool:
        """The driver's watchdog fired while the scene's code was running (a verdict on the code, not the box)."""
        return self.timed_out and self.phase in SCENE_PHASES


def _driver_failed(r: NodeResult) -> bool:
    """The driver could not run (exit 2/3, or non-zero with no summary); exit 1 is a failed scene."""
    return r.rc in (2, 3) or (r.rc != 0 and not r.summary)


def run_scene_script(script: str, args: Sequence[str], *, timeout_s: float, cwd: Path | None = None) -> NodeResult:
    """Run ``runtime_js/<script>``; the driver prints a JSON summary as its last stdout line.

    Timeout or exit 2/3 raises ``SceneRenderError``.  A lost browser, a watchdog timeout, transient failures
    and lost output are retried once, the first two on an owned browser.
    """
    rt = runtime_js_dir()
    path = rt / script
    if not path.is_file():
        raise SceneRenderError(f"missing node driver {path}")
    env_extra: dict[str, str] = {}
    gpu = get_settings().render.gpu
    if gpu:
        env_extra["C3D_RENDER_GPU"] = gpu
    argv = [str(a) for a in args]

    def attempt(env: dict[str, str]) -> NodeResult:
        try:
            return run_node(path, argv, cwd=cwd or rt, timeout_s=timeout_s, env_extra=env, check=False)
        except NodeError as e:
            if e.result is not None and e.result.timed_out:
                # the process clock: node's own watchdog never ran (the box, no phase)
                raise SceneRenderError(f"{script} timed out after {timeout_s:.0f}s\n{e.result.stderr_tail}",
                                       timed_out=True) from e
            raise SceneRenderError(f"{script} could not run: {e}") from e

    r = attempt(env_extra)
    err = str(r.summary.get("error", "")) if r.summary else r.stderr_tail
    lost_browser = _driver_failed(r) and browser_was_lost(err)
    # watchdog timeout: retry on an owned browser so a wedged shared one cannot time out both attempts
    timed_out = not lost_browser and _driver_failed(r) and bool(r.summary.get("timed_out"))
    # the rest of ``node.TRANSIENT_MARKERS``
    transient = not lost_browser and not timed_out and _driver_failed(r) and transient_failure(err)
    # every driver ends with a JSON summary line, so none means the output was lost: transient
    lost_output = not lost_browser and not r.summary
    if lost_browser or lost_output or transient or timed_out:
        log.warning(
            "%s %s; retrying once%s",
            script,
            f"lost its browser ({err[:200]})" if lost_browser
            else f"exited {r.rc} with no summary line (output lost)" if lost_output
            else f"ran out of its budget in phase {r.summary.get('phase') or '?'}" if timed_out
            else f"failed transiently ({err[:200]})",
            "" if transient else " on an owned browser",
        )
        r = attempt(env_extra if transient else {**env_extra, **OWN_BROWSER_ENV})
    summary = r.summary
    if _driver_failed(r):
        raise SceneRenderError(
            f"{script} failed (exit {r.rc}): {summary.get('error') if summary else ''}\n"
            f"stderr tail: {r.stderr_tail}\nstdout tail: {r.stdout[-1000:]}",
            timed_out=bool(summary.get("timed_out")), phase=str(summary.get("phase") or ""),
        )
    return r


def probe_env_args() -> list[str]:
    """Scene-driver flags from the settle / camera-repair settings; every driver invocation appends them."""
    s = get_settings()
    args: list[str] = []
    if not s.settle:
        args.append("--no-settle")
    if s.camera_repair:
        args.append("--camera-repair")
    return args


def post_chain_args() -> list[str]:
    """``--no-post`` when the post chain is off.  It applies to scene pictures only; the probe runs raw, so this is not in :func:`probe_env_args`."""
    return [] if get_settings().post else ["--no-post"]


def _camera_json(cams: Sequence[CameraPlan]) -> str:
    return json.dumps([
        {"name": c.name, "position": list(c.position), "lookAt": list(c.look_at), "fov": c.fov} for c in cams
    ])


def _views_json(views: Sequence[ViewPreset]) -> str:
    return json.dumps(view_specs(views))


def _bounds_json(bounds: BBox) -> str:
    return json.dumps({"min": list(bounds.min), "max": list(bounds.max)})


def plan_bounds(ws: Workspace) -> BBox | None:
    """Scene bounds from the workspace's ``plan.json`` (None when absent/invalid)."""
    raw = (read_json_or_none(ws.plan_path) or {}).get("bounds")
    try:
        return BBox.model_validate(raw) if raw else None
    except ValueError:
        return None


def render_scene(
    ws: Workspace,
    out_dir: Path,
    *,
    cameras: list[CameraPlan] | None = None,
    orbit: bool = True,
    times: Sequence[float] = SCENE_TIMES,
    width: int = 1024,
    height: int = 576,
    sheet: bool = True,
    orbit_views: Sequence[ViewPreset] = SCENE_VIEWS,
    fps_seconds: float = 2.0,
    timeout_s: float | None = None,
    bounds: BBox | None = None,
) -> RenderSet:
    """Render authored (+ orbit) views at ``times``; returns a RenderSet.

    ``cameras=None`` uses the cameras authored in ``createScene()``; ``bounds`` defaults to the plan's.  A scene that
    fails to boot yields an empty RenderSet with ``console_errors``; a driver failure raises ``SceneRenderError``.
    """
    out_dir = out_directory(out_dir, clean=("metrics.json", "views.json"))
    settings = get_settings()
    args: list[str] = [
        "--ws", str(ws.root), "--out", str(out_dir),
        "--cameras", _camera_json(cameras) if cameras else "authored",
        "--orbit-views", _views_json(orbit_views) if orbit else "none",
        "--times", ",".join(f"{t:g}" for t in times),
        "--width", str(width), "--height", str(height),
        "--fps-seconds", f"{fps_seconds:g}",
    ]
    bounds = bounds or plan_bounds(ws)
    if bounds is not None:
        args += ["--bounds", _bounds_json(bounds)]
    tmo = timeout_s or settings.limits.render_timeout_s
    args += ["--timeout-ms", str(int(tmo * 1000))]
    args += probe_env_args()
    args += post_chain_args()
    driver_error = ""
    try:
        res = run_scene_script("render_scene.mjs", args, timeout_s=tmo + 30)
    except SceneRenderError as e:
        # instruments already written: a scene-side failure for the gate; none written: a driver problem
        if not (out_dir / "metrics.json").is_file():
            raise
        driver_error = str(e).splitlines()[0][:500]
        res = NodeResult(rc=2, stdout="", stderr="", last_json={"error": driver_error}, duration_ms=0)
    metrics = read_json_or_none(out_dir / "metrics.json") or {}
    for w in metrics.get("host_warnings") or []:
        log.warning("render_scene host warning: %s", w)
    extra = [driver_error] if driver_error else []
    if not metrics and res.summary.get("error"):
        extra.append(str(res.summary["error"]))
    return render_set_from_metrics(out_dir, metrics, width=width, height=height, orbit_views=orbit_views, sheet=sheet,
                                   duration_ms=res.duration_ms, renderer=str(res.summary.get("renderer") or ""),
                                   extra_errors=extra)


def render_set_from_metrics(out_dir: Path, metrics: dict[str, Any], *, width: int, height: int,
                            orbit_views: Sequence[ViewPreset] = SCENE_VIEWS, sheet: bool = True, duration_ms: int = 0,
                            renderer: str = "", extra_errors: Sequence[str] = ()) -> RenderSet:
    """A scene driver's ``metrics.json`` payload → the RenderSet: motion, views, errors, the stamped judge subset, the contact sheet."""
    _store_motion(out_dir, metrics)
    views: list[RenderView] = []
    for v in metrics.get("views", []):
        views.append(RenderView(
            name=v["name"], path=str(out_dir / v["path"]), mode="shaded", width=width, height=height,
            camera_position=tuple(v["position"]), look_at=tuple(v["lookAt"]), fov=v.get("fov"),
            time_s=v.get("time_s"),
        ))
    fps = (metrics.get("fps") or {}).get("fps")
    errors = list(metrics.get("console_errors", []))
    for e in metrics.get("shader_errors", []):
        errors.append(f"shader[{e.get('stage')}]: {e.get('message')} — {e.get('source_line', '')}".strip())
    for e in extra_errors:
        if e and e not in errors:
            errors.append(e)
    rs = RenderSet(
        views=views, renderer=str(metrics.get("renderer") or renderer or ""),
        duration_ms=duration_ms, console_errors=errors, fps=float(fps) if fps is not None else None,
        out_dir=str(out_dir),
    )
    chosen = select_judge_views(rs, max_n=JUDGE_MAX_VIEWS, orbit_names=[v.name for v in orbit_views])
    keep = {id(v) for v in chosen.views}
    for v in rs.views:
        v.judge = id(v) in keep
    _mark_judge_views(out_dir / "views.json", rs.views)
    if sheet and views:
        labelled = [(f"{v.name} t={v.time_s:g}", Path(v.path)) for v in rs.views if v.judge]
        rs.contact_sheet = build_sheet(labelled, out_dir / "sheet.png")
    return rs


def _store_motion(out_dir: Path, metrics: dict[str, Any]) -> None:
    """Measure inter-frame motion into the payload and ``metrics.json``; never fatal (no ``motion`` key = not measured)."""
    from codeverse3d.spatial.frame_motion import motion_rows

    if not metrics.get("views"):
        return
    try:
        rows = motion_rows(metrics, out_dir)
    except Exception as e:  # noqa: BLE001 — instrumentation must not break a render
        log.warning("motion measurement failed: %s", e)
        return
    if not rows:
        return
    metrics["motion"] = [r.as_dict() for r in rows]
    try:
        write_json_atomic(out_dir / "metrics.json", metrics)
    except OSError as e:
        log.warning("could not write motion into %s: %s", out_dir / "metrics.json", e)


def select_judge_views(rs: RenderSet, max_n: int = JUDGE_MAX_VIEWS, *, orbit_names: Sequence[str] | None = None) -> RenderSet:
    """Copy of ``rs`` with the views a judge should see (≤ ``max_n``): authored cameras at the first time, the first
    overviews at that time, the first two authored cameras at the last time."""
    orbit = set(orbit_names if orbit_names is not None else [v.name for v in SCENE_VIEWS])
    views = list(rs.views)
    if not views or max_n <= 0:
        return rs.model_copy(update={"views": views[:max(max_n, 0)]})
    times = sorted({v.time_s if v.time_s is not None else 0.0 for v in views})
    t0, t_last = times[0], times[-1]
    authored = [v for v in views if v.name not in orbit]
    overview = [v for v in views if v.name in orbit and v.name.startswith("overview")]
    at = lambda vs, t: [v for v in vs if (v.time_s if v.time_s is not None else 0.0) == t]  # noqa: E731
    first_two = [v.name for v in at(authored, t0)[:2]]
    ordered: list[RenderView] = [*at(authored, t0), *at(overview, t0)[:MAX_JUDGE_OVERVIEWS]]
    if t_last != t0:
        ordered += [v for v in at(authored, t_last) if v.name in first_two]
    # the eye-level rig is left out: cameras nobody authored mislead the judge
    if not ordered:
        ordered = list(views)
    chosen = ordered[:max_n]
    keep = {id(v) for v in chosen}
    return rs.model_copy(update={"views": [v for v in views if id(v) in keep]})  # disk order


def _mark_judge_views(views_json: Path, views: Sequence[RenderView]) -> None:
    """Write each view's ``judge`` flag into views.json."""
    if not views_json.is_file():
        return
    try:
        entries = json.loads(views_json.read_text())
    except (OSError, ValueError):
        return
    flags = {(v.name, v.time_s): bool(v.judge) for v in views}
    for e in entries:
        e["judge"] = flags.get((e.get("name"), e.get("time_s")), False)
    views_json.write_text(json.dumps(entries, indent=1))


def metrics_path_for(rs: RenderSet) -> Path | None:
    """``metrics.json`` of a RenderSet: in ``out_dir``, else beside the sheet / views."""
    if rs.out_dir:
        p = Path(rs.out_dir) / "metrics.json"
        if p.is_file():
            return p
    for cand in (rs.contact_sheet, *(v.path for v in rs.views)):
        if cand:
            p = Path(cand).parent / "metrics.json"
            if p.is_file():
                return p
    return None
