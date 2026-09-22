"""Render a ``scene_threejs`` workspace through the node host (headless GPU Chrome).

``render_scene(ws, out_dir, ...)`` drives ``runtime_js/render_scene.mjs``:
authored cameras + an overview orbit rig (``conventions.SCENE_VIEWS``, fitted
to the scene CONTENT, guarded by the plan bounds) at one or more animation
times, deterministic instruments (console/shader errors, fps, census, camera
checks incl. luminance + content coverage) and a labelled contact sheet built
from the JUDGE subset of views.  Returns a ``RenderSet`` with every view; the
full instrument payload is left at ``out_dir/metrics.json`` (``metrics_path_for``
finds it again from a RenderSet).  Each ``RenderView`` (and each entry of the
driver's ``views.json``) carries ``judge: true|false`` — stamped once from
``select_judge_views`` at render time.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codeverse3d.config import env_flag, get_settings
from codeverse3d.contracts.artifacts import RenderSet, RenderView
from codeverse3d.contracts.plan import BBox, CameraPlan
from codeverse3d.conventions import SCENE_VIEWS, ViewPreset
from codeverse3d.proc import read_json_or_none
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
#: how many overview-rig tiles the judge gets (layout / grounding X-ray; the eye-level
#: rig views are diagnostics only and never reach the judge)
MAX_JUDGE_OVERVIEWS = 3


class SceneRenderError(RuntimeError):
    """The node driver could not run (missing node/deps, timeout, crash)."""


def _driver_failed(r: NodeResult) -> bool:
    """The DRIVER could not run (exit 2/3, or a non-zero exit with no summary line).
    Exit 1 is a scene that failed, which is a verdict, not a driver failure."""
    return r.rc in (2, 3) or (r.rc != 0 and not r.summary)


def run_scene_script(script: str, args: Sequence[str], *, timeout_s: float, cwd: Path | None = None) -> NodeResult:
    """Run ``runtime_js/<script>`` via ``codeverse3d.spatial.node.run_node`` (which
    delegates to ``codeverse3d.proc.run_subprocess``: own process group, group kill
    on timeout).  The driver prints a JSON summary as its LAST stdout line; a
    timeout or exit code 2/3 means the driver itself failed → ``SceneRenderError``.

    A driver that lost its BROWSER (``node.BROWSER_LOST_MARKERS``) is retried once on
    a browser of its own.  That failure is the box, not the workspace, and without the
    retry it costs the round every render and therefore its judge — the object path
    has retried it since 2026-08-28 (``spatial/render.py``), the scene path did not.
    """
    rt = runtime_js_dir()
    path = rt / script
    if not path.is_file():
        raise SceneRenderError(f"missing node driver {path}")
    env_extra: dict[str, str] = {}
    gpu = get_settings().render.gpu
    if gpu and "C3D_RENDER_GPU" not in os.environ:
        env_extra["C3D_RENDER_GPU"] = gpu
    argv = [str(a) for a in args]

    def attempt(env: dict[str, str]) -> NodeResult:
        try:
            return run_node(path, argv, cwd=cwd or rt, timeout_s=timeout_s, env_extra=env, check=False)
        except NodeError as e:
            if e.result is not None and e.result.timed_out:
                raise SceneRenderError(f"{script} timed out after {timeout_s:.0f}s\n{e.result.stderr_tail}") from e
            raise SceneRenderError(f"{script} could not run: {e}") from e

    r = attempt(env_extra)
    err = str(r.summary.get("error", "")) if r.summary else r.stderr_tail
    lost_browser = _driver_failed(r) and browser_was_lost(err)
    # the rest of the transient vocabulary (`node.TRANSIENT_MARKERS`): a host that timed out
    # waiting for the page under contention ("Waiting failed: 60000ms exceeded") — the object
    # path retried it, this one did not, and a battery cell lost every render to it
    transient = not lost_browser and _driver_failed(r) and transient_failure(err)
    # Every scene driver ends with a JSON summary line (`lib/cli.finish` / `fail`), so a run
    # that produced none had its stdout tail dropped — `proc._ABANDONED`, which a loaded box
    # makes routine.  That is transient and worth one more attempt; without it the empty
    # summary reaches `probes.probe_report`, which used to render it as "[?] scene did not
    # boot" and spend the round's repair budget on a defect that was never there
    # (desert_canyon, bench/out/scene_baseline, 2026-09-05).
    lost_output = not lost_browser and not r.summary
    if lost_browser or lost_output or transient:
        log.warning(
            "%s %s; retrying once%s",
            script,
            f"lost its browser ({err[:200]})" if lost_browser
            else f"exited {r.rc} with no summary line (output lost)" if lost_output
            else f"failed transiently ({err[:200]})",
            "" if transient else " on an owned browser",
        )
        r = attempt(env_extra if transient else {**env_extra, **OWN_BROWSER_ENV})
    summary = r.summary
    if _driver_failed(r):
        raise SceneRenderError(
            f"{script} failed (exit {r.rc}): {summary.get('error') if summary else ''}\n"
            f"stderr tail: {r.stderr_tail}\nstdout tail: {r.stdout[-1000:]}"
        )
    return r


def probe_env_args() -> list[str]:
    """Scene-driver flags from the ``C3D_*`` switches — the ONE parser (review-3 S4),
    on the canonical ``env_flag`` words, so ``C3D_CAMERA_REPAIR=false`` really
    disables and ``C3D_AUTO_EXPOSURE=true`` really enables.  Every driver
    invocation — standalone probe, render, and the combined single-boot build —
    appends these, so a census is always measured under the same settle /
    camera-repair / auto-exposure policy the renders use."""
    args: list[str] = []
    if not env_flag("C3D_SETTLE", True):   # A/B switch for the boot-time auto-seat
        args.append("--no-settle")
    if env_flag("C3D_CAMERA_REPAIR", True):   # default ON since 2026-08-30: pure insurance —
        # zero triggers across a whole healthy battery (scene_px_v1: layout camera-clearance
        # already keeps lenses out of furniture), and the one class it exists for
        # (fv_izakaya: three rounds of camera_in_geometry nobody could fix) is fatal.
        args.append("--camera-repair")
    if env_flag("C3D_AUTO_EXPOSURE", False):   # opt-in: bounded scene-wide exposure into the healthy band
        args.append("--auto-exposure")
    return args


def post_chain_args() -> list[str]:
    """``--no-post`` when ``C3D_POST`` is off.  The post chain (GTAO + selective
    bloom + grade, ``runtime_js/lib/browser/post.js``) is ON by default for scene
    PICTURES only: object renders never touch this driver, and the scene probe runs
    raw because it is a 320x180 geometry instrument, not a judged frame.  Kept out
    of :func:`probe_env_args` for exactly that reason."""
    return [] if env_flag("C3D_POST", True) else ["--no-post"]


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
    times: Sequence[float] = (0.0, 1.5),
    width: int = 1024,
    height: int = 576,
    sheet: bool = True,
    orbit_views: Sequence[ViewPreset] = SCENE_VIEWS,
    fps_seconds: float = 2.0,
    timeout_s: float | None = None,
    bounds: BBox | None = None,
) -> RenderSet:
    """Render authored (+ orbit) views at ``times``; returns a RenderSet.

    ``cameras=None`` uses the cameras authored in ``createScene()``.  ``bounds``
    (default: the workspace plan's bounds) guards the orbit framing against
    scatter that sprawls past the plan.  The contact sheet holds at most
    ``JUDGE_MAX_VIEWS`` tiles (the ``select_judge_views`` subset).  A
    scene that fails to boot yields an empty RenderSet whose ``console_errors``
    say why (the build gate normally catches that earlier); a driver failure
    raises ``SceneRenderError``.
    """
    # never read a previous run's instruments
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
        # The driver died mid-run but this invocation already wrote instruments:
        # a scene-side failure the round loop must see as a gate finding, not a
        # harness crash.  No metrics at all = a genuine driver problem: re-raise.
        if not (out_dir / "metrics.json").is_file():
            raise
        driver_error = str(e).splitlines()[0][:500]
        res = NodeResult(rc=2, stdout="", stderr="", last_json={"error": driver_error}, duration_ms=0)
    metrics = read_json_or_none(out_dir / "metrics.json") or {}
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
    if driver_error and driver_error not in errors:
        errors.append(driver_error)
    if not metrics and res.summary.get("error"):
        errors.append(str(res.summary["error"]))
    rs = RenderSet(
        views=views, renderer=str(metrics.get("renderer") or res.summary.get("renderer") or ""),
        duration_ms=res.duration_ms, console_errors=errors, fps=float(fps) if fps is not None else None,
        out_dir=str(out_dir),
    )
    # stamp the judge subset ONCE (select_judge_views stays a pure function)
    chosen = select_judge_views(rs, max_n=JUDGE_MAX_VIEWS, orbit_names=[v.name for v in orbit_views])
    keep = {id(v) for v in chosen.views}  # same objects, so identity is exact
    for v in rs.views:
        v.judge = id(v) in keep
    _mark_judge_views(out_dir / "views.json", rs.views)
    if sheet and views:
        labelled = [(f"{v.name} t={v.time_s:g}", Path(v.path)) for v in rs.views if v.judge]
        rs.contact_sheet = build_sheet(labelled, out_dir / "sheet.png")
    return rs


def _store_motion(out_dir: Path, metrics: dict[str, Any]) -> None:
    """Measure inter-frame motion (``spatial.frame_motion``) and persist it into the
    payload AND into ``metrics.json``, so every later reader — the ``scene_frames``
    gate, the judge context, the agent's frame table — sees the same numbers without
    re-reading the PNGs.  Never fatal: a scene that could not be diffed simply has no
    ``motion`` key (which means "not measured", never "static")."""
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
    path = out_dir / "metrics.json"
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(metrics, indent=1))
        tmp.replace(path)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        log.warning("could not write motion into %s: %s", path, e)


def perf_detail(rs: RenderSet) -> str:
    """`` — 5028 draw calls (budget 200), 191k triangles`` from a render's own instruments.

    The number is what makes a low-fps finding actionable: "low fps" is a mood, "5028 draw
    calls against a budget of 200" names the fix.  ``""`` when the instruments are missing."""
    try:
        path = metrics_path_for(rs)
        if path is None:
            return ""
        fps = (json.loads(path.read_text()).get("fps") or {})
        calls, tris = fps.get("draw_calls"), fps.get("triangles")
        bits = [f"{int(calls)} draw calls (budget 200)" if isinstance(calls, (int, float)) else "",
                f"{int(tris) / 1000:.0f}k triangles" if isinstance(tris, (int, float)) else ""]
        inner = ", ".join(b for b in bits if b)
        return f" — {inner}" if inner else ""
    except (OSError, ValueError, TypeError):
        return ""


def select_judge_views(rs: RenderSet, max_n: int = JUDGE_MAX_VIEWS, *, orbit_names: Sequence[str] | None = None) -> RenderSet:
    """Copy of ``rs`` with the views a judge should see (≤ ``max_n``), in priority order:
    authored cameras at the first time · the first two overview orbit views at that time
    · the first two authored cameras at the last time · remaining orbit / authored /
    other views.  Tracks call this before judging; the full set stays on disk."""
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
    # Everything left is the harness's own eye-level rig: cameras nobody authored, fitted
    # to the bounds, routinely staring at a fence or standing at street level under a
    # rooftop.  They stay on disk (and in the frame gate) as diagnostics, but a judge that
    # SEES them marks the scene unlit / undressed for pictures the builder never framed.
    if not ordered:
        ordered = list(views)
    chosen = ordered[:max_n]
    keep = {id(v) for v in chosen}
    return rs.model_copy(update={"views": [v for v in views if id(v) in keep]})  # disk order, judge subset


def _mark_judge_views(views_json: Path, views: Sequence[RenderView]) -> None:
    """Serialise each view's stamped ``judge`` flag into the driver's views.json."""
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


def read_metrics(out_dir: Path) -> dict[str, Any]:
    """Full instrument payload written by the driver (census, camera_checks, ...)."""
    return read_json_or_none(Path(out_dir) / "metrics.json") or {}


def metrics_path_for(rs: RenderSet) -> Path | None:
    """``metrics.json`` of a RenderSet: the stamped ``out_dir`` when present, else
    guessed as a sibling of the sheet / views (rounds recorded before stamping)."""
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
