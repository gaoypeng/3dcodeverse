"""Render a built ``scene_blender`` workspace: Blender makes the pictures, the JS host measures.

``render_blender_scene(ws, out_dir, ...)`` is what ``SceneBlenderRuntime.render_scene`` calls
(DESIGN §2 "render", owner D1/D3/D8 2026-09-24).  Four steps, one output format:

1. **orbit fit** — ``runtime_js/fit_orbit.mjs`` fits ``conventions.SCENE_VIEWS`` to the census
   the build's probe wrote (``artifacts/census.json``): the same ``orbit.mjs`` the three.js
   driver runs in-page, so there is one implementation of the fitting;
2. **pixels** — ``languages/wrappers/render_bpy_scene.py`` inside Blender on the built
   ``artifacts/scene.blend``: the authored cameras × every time + the first
   ``MAX_JUDGE_OVERVIEWS`` overview views at the first time (what someone reads — the judge and
   the motion metric; the eye-level rig views get geometry checks but no pixels).  Engine /
   samples / device come from ``Settings.render.blender_*`` (Cycles CUDA 32 spp + OIDN by
   default); a GPU render holds one of ``blender_gpu_slots`` machine-wide flock'd slots and
   falls back to the CPU when none frees within ``blender_gpu_wait_s`` or the GPU run dies;
3. **geometry** — ``runtime_js/render_scene.mjs --external-frames`` on the census GLB the build
   wrote (loaded through the language's host entry, ``scene_rel``): camera checks (near
   geometry, inside a mesh, ground below/above, line of sight, coverage masks) from the scene,
   luminance statistics from the Blender PNG (``host_metrics.imageFrameStats`` — the one
   ``frameStats``), ``views.json`` / ``metrics.json`` in the three.js driver's schema, with no
   settle, no camera repair, no post chain (D3: the picture is Blender's);
4. **the RenderSet** — ``render_scene.render_set_from_metrics``, exactly as for three.js:
   motion, errors, the judge subset, the contact sheet.

``metrics.json`` gains two keys the three.js payload does not carry: ``language``
(``"scene_blender"``: the frame gate's fix hints are per language) and ``blender`` (engine,
device, samples, the fallback reason, the per-step wall times).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codeverse3d.config import Settings, get_settings
from codeverse3d.contracts.artifacts import RenderSet
from codeverse3d.contracts.plan import BBox, CameraPlan
from codeverse3d.conventions import (
    SCENE_VIEWS,
    ViewPreset,
    from_authoring_frame,
    to_authoring_frame,
)
from codeverse3d.models.retry import KeyPoolExhausted, Slots
from codeverse3d.proc import ProcResult, read_json_or_none, run_subprocess, write_json_atomic
from codeverse3d.spatial._render_common import out_directory, view_specs
from codeverse3d.spatial.render_scene import (
    MAX_JUDGE_OVERVIEWS,
    SceneRenderError,
    plan_bounds,
    render_set_from_metrics,
    run_scene_script,
)
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

DRIVER = Path(__file__).resolve().parent.parent / "languages" / "wrappers" / "render_bpy_scene.py"
#: the language whose authoring frame the .blend is in.  scene_blender's LANGUAGE_FRAME row
#: (lane A) is Z-up / -Y front, the same frame as blender's; switch to it once that row lands.
FRAME_LANGUAGE = "blender"
LANGUAGE = "scene_blender"
JOB_NAME = "blender_job.json"
RESULT_NAME = "blender_frames.json"
EXTERNAL_NAME = "blender_views.json"
SLOT_DIR = "blender_gpu"
#: the keys of a view entry — render_scene.mjs's own view schema
_VIEW_KEYS = ("name", "kind", "path", "time_s", "position", "lookAt", "fov", "render_ms")


def gpu_slots(settings: Settings | None = None) -> Slots:
    """The machine-wide GPU Blender render slots (``<cache_dir>/slots/blender_gpu/NN.lock``)."""
    s = settings or get_settings()
    return Slots(s.render.blender_gpu_slots, s.cache_dir / "slots" / SLOT_DIR)


def _to_blender(v: Sequence[float]) -> list[float]:
    return list(to_authoring_frame(v, FRAME_LANGUAGE))


def _to_glb(v: Sequence[float]) -> list[float]:
    return [round(c, 5) for c in from_authoring_frame(v, FRAME_LANGUAGE)]


def _blender_env(engine: str, device: str) -> dict[str, str]:
    """A headless Blender child's env; EEVEE / Workbench on the GPU need the Mesa d3d12 env
    (without it they silently land on llvmpipe — measured 10x slower, no error)."""
    from codeverse3d.languages.blender import blender_env
    from codeverse3d.spatial.gl_render import GPU_ENV

    env = blender_env()
    if engine != "cycles" and device == "GPU":
        env.update(GPU_ENV)
    return env


# --------------------------------------------------------------------------- pixels
def _run_driver(blend: Path, job: dict[str, Any], out_dir: Path, *, device: str, timeout_s: float,
                settings: Settings) -> tuple[ProcResult, dict[str, Any] | None]:
    job = {**job, "device": device}
    job_path = out_dir / JOB_NAME
    result_path = out_dir / RESULT_NAME
    result_path.unlink(missing_ok=True)
    write_json_atomic(job_path, job)
    blender = settings.resolve_blender()
    if not blender:
        raise SceneRenderError("no Blender binary (set C3D_BINARIES__BLENDER or put blender-5.0 on PATH)")
    cmd = [blender, "-b", "--factory-startup", str(blend), "--python", str(DRIVER), "--", str(job_path)]
    proc = run_subprocess(cmd, cwd=out_dir, env=_blender_env(str(job["engine"]), device), timeout_s=timeout_s)
    result = read_json_or_none(result_path)
    return proc, result if isinstance(result, dict) and proc.returncode == 0 else None


def _failure(proc: ProcResult) -> str:
    from codeverse3d.languages._common import strip_blender_noise

    tail = strip_blender_noise(proc.stderr + "\n" + proc.stdout)[-800:]
    return f"exit {proc.returncode}" + (f": {tail}" if tail else "")


def render_frames(blend: Path, out_dir: Path, job: dict[str, Any], *, timeout_s: float,
                  settings: Settings | None = None) -> dict[str, Any]:
    """Run the Blender driver under the GPU-slot policy; its result json + ``fallback`` / ``slot_wait_ms``.

    ``blender_device == "gpu"``: take a slot (wait ≤ ``blender_gpu_wait_s``) and render on the
    GPU; all slots busy → CPU; a GPU run that dies without a result → one CPU run.  A timeout
    is the scene's weight, not the device's: it raises ``SceneRenderError`` with no retry."""
    s = settings or get_settings()
    fallback = ""
    wait_ms = 0
    if s.render.blender_device == "cpu":
        device = "CPU"
    else:
        slots = gpu_slots(s)
        t0 = time.monotonic()
        try:
            fd: int | None = slots.take(s.render.blender_gpu_wait_s)
        except KeyPoolExhausted:
            fd = None
            fallback = f"all {slots.n} GPU render slots busy for {s.render.blender_gpu_wait_s:g}s ({slots.root})"
        wait_ms = int((time.monotonic() - t0) * 1000)
        device = "GPU" if fd is not None else "CPU"
        if fd is not None:
            try:
                proc, result = _run_driver(blend, job, out_dir, device="GPU", timeout_s=timeout_s, settings=s)
            finally:
                Slots.give(fd)
            if proc.timed_out:
                raise SceneRenderError(f"blender render timed out after {timeout_s:.0f}s on the GPU")
            if result is not None:
                return {**result, "fallback": result.get("device_fallback") or "", "slot_wait_ms": wait_ms}
            fallback = f"GPU render failed ({_failure(proc)[:300]})"
            device = "CPU"
    if fallback:
        log.warning("scene_blender render on the CPU: %s", fallback)
    proc, result = _run_driver(blend, job, out_dir, device=device, timeout_s=timeout_s, settings=s)
    if proc.timed_out:
        raise SceneRenderError(f"blender render timed out after {timeout_s:.0f}s on the {device}")
    if result is None:
        raise SceneRenderError(f"blender render driver failed: {_failure(proc)}")
    return {**result, "fallback": fallback or result.get("device_fallback") or "", "slot_wait_ms": wait_ms}


# --------------------------------------------------------------------------- orbit
def fit_orbit(census_path: Path, views: Sequence[ViewPreset], *, width: int, height: int,
              bounds: BBox | None, timeout_s: float = 60.0) -> dict[str, Any]:
    """``fit_orbit.mjs`` on a census: ``{framing_bbox, cameras}`` in the GLB frame (``{}`` on failure)."""
    args = ["--census", str(census_path), "--orbit-views", json.dumps(view_specs(views)),
            "--width", str(width), "--height", str(height)]
    if bounds is not None:
        args += ["--bounds", json.dumps({"min": list(bounds.min), "max": list(bounds.max)})]
    try:
        return dict(run_scene_script("fit_orbit.mjs", args, timeout_s=timeout_s).summary)
    except SceneRenderError as e:
        log.warning("orbit fit failed: %s", e)
        return {}


def _pixel_orbit(specs: list[dict[str, Any]]) -> set[str]:
    """The rig views that get Blender pixels: the first ``MAX_JUDGE_OVERVIEWS`` overviews."""
    return set([str(o["name"]) for o in specs if str(o["name"]).startswith("overview")][:MAX_JUDGE_OVERVIEWS])


# --------------------------------------------------------------------------- entry
def render_blender_scene(
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
    timeout_s: float | None = None,
    bounds: BBox | None = None,
    blend: Path | None = None,
    census: Path | None = None,
    scene_rel: str = "src/scene.js",
    engine: str | None = None,
    samples: int | None = None,
) -> RenderSet:
    """Authored cameras (+ the orbit rig) × ``times`` rendered by Blender → a RenderSet whose
    ``metrics.json`` has the three.js driver's schema.

    ``cameras=None`` renders the cameras the .blend carries (the build made them from the
    plan); explicit ``cameras`` (GLB frame, like every plan camera) replace them.  ``blend``
    defaults to ``artifacts/scene.blend``, ``census`` (the orbit fit) to ``artifacts/census.json``;
    ``scene_rel`` is the host entry that loads the census GLB for the geometry pass.  ``engine``
    / ``samples`` override Settings (a tool's preview tier: ``engine="workbench"``).  A missing
    .blend yields an empty RenderSet whose ``console_errors`` say why; a driver that cannot run
    raises ``SceneRenderError``."""
    s = get_settings()
    out_dir = out_directory(out_dir, clean=("metrics.json", "views.json", RESULT_NAME, EXTERNAL_NAME))
    blend = blend or ws.artifacts / "scene.blend"
    census = census or ws.artifacts / "census.json"
    times = sorted({float(t) for t in times}) or [0.0]
    tmo = float(timeout_s or s.limits.render_timeout_s)
    t_all = time.monotonic()
    if not blend.is_file():
        return _empty(out_dir, f"no {blend.name}: the build did not save the scene (see build.json)",
                      width=width, height=height, orbit_views=orbit_views)
    # 1. the orbit rig, fitted to the census content (GLB frame)
    t0 = time.monotonic()
    fit: dict[str, Any] = {}
    if orbit and orbit_views:
        if census.is_file():
            fit = fit_orbit(census, orbit_views, width=width, height=height, bounds=bounds or plan_bounds(ws))
        else:
            log.warning("no %s: the orbit rig is not rendered", census)
    orbit_specs: list[dict[str, Any]] = list(fit.get("cameras") or [])
    fit_ms = int((time.monotonic() - t0) * 1000)
    # 2. pixels
    pixel = _pixel_orbit(orbit_specs)
    job_cams = [{"name": c.name, "kind": "authored", "position": _to_blender(c.position), "look_at": _to_blender(c.look_at),
                 "fov": c.fov, "no_fog": False, "times": times} for c in cameras or []]
    job_cams += [{"name": o["name"], "kind": "orbit", "position": _to_blender(o["position"]),
                  "look_at": _to_blender(o["lookAt"]), "fov": o.get("fov") or 50.0, "no_fog": bool(o.get("noFog")),
                  "times": times[:1]} for o in orbit_specs if o["name"] in pixel]
    job = {"out": str(out_dir), "result": str(out_dir / RESULT_NAME), "engine": engine or s.render.blender_engine,
           "samples": int(samples or s.render.blender_samples), "width": width, "height": height, "times": times,
           "authored": cameras is None, "cameras": job_cams}
    t0 = time.monotonic()
    frames = render_frames(blend, out_dir, job, timeout_s=tmo, settings=s)
    blender_ms = int((time.monotonic() - t0) * 1000)
    views = [_glb_view(f) for f in frames.get("frames") or []]
    write_json_atomic(out_dir / EXTERNAL_NAME, views)
    # 3. geometry: every camera the scene has (authored as Blender saw them) + the whole rig
    specs = [{"name": c["name"], "kind": c.get("kind") or "authored", "position": _to_glb(c["position"]),
              "lookAt": _to_glb(c["look_at"]), "fov": c.get("fov")}
             for c in frames.get("cameras") or [] if c.get("kind") == "authored"]
    specs += [{**o, "kind": "orbit"} for o in orbit_specs]
    t0 = time.monotonic()
    metrics, geometry_error = _geometry_pass(ws, out_dir, specs, times=times, width=width, height=height,
                                             timeout_s=tmo, scene_rel=scene_rel)
    geometry_ms = int((time.monotonic() - t0) * 1000)
    if not metrics.get("views"):
        metrics["views"] = views   # the pictures exist even when the geometry pass could not run
    # 4. merge the Blender facts; one schema downstream
    errors = [f"blender: {e}" for e in frames.get("errors") or []]
    if geometry_error:
        errors.append(geometry_error)
    metrics["console_errors"] = list(metrics.get("console_errors") or []) + [e for e in errors
                                                                             if e not in (metrics.get("console_errors") or [])]
    metrics["ok"] = bool(metrics.get("ok", True)) and not errors and bool(views)
    metrics["language"] = LANGUAGE
    metrics["renderer"] = _renderer_line(frames)
    if fit.get("framing_bbox") is not None:
        metrics["framing_bbox"] = fit["framing_bbox"]
    warnings = list(metrics.get("host_warnings") or [])
    if frames.get("engine") in ("eevee", "workbench") and "llvmpipe" in str(frames.get("gl_renderer") or "").lower():
        warnings.append(f"{frames['engine']} rendered on llvmpipe (software GL): {frames.get('gl_renderer')}")
    if frames.get("fallback"):
        warnings.append(f"rendered on the CPU: {frames['fallback']}")
    metrics["host_warnings"] = warnings
    metrics["blender"] = {
        "engine": frames.get("engine"), "device": frames.get("device"), "device_names": frames.get("device_names") or [],
        "samples": frames.get("samples"), "fallback": frames.get("fallback") or "", "version": frames.get("blender"),
        "gl_renderer": frames.get("gl_renderer") or "", "n_frames": len(views),
        "frame_ms": [v["render_ms"] for v in views],
        "timings_ms": {"fit": fit_ms, "slot_wait": frames.get("slot_wait_ms", 0), "blender": blender_ms,
                       "blender_setup": frames.get("setup_ms", 0), "geometry": geometry_ms,
                       "total": int((time.monotonic() - t_all) * 1000)},
    }
    metrics["duration_ms"] = metrics["blender"]["timings_ms"]["total"]
    write_json_atomic(out_dir / "metrics.json", metrics)
    write_json_atomic(out_dir / "views.json", metrics["views"])
    for w in warnings:
        log.warning("render_blender_scene: %s", w)
    return render_set_from_metrics(out_dir, metrics, width=width, height=height, orbit_views=orbit_views, sheet=sheet,
                                   duration_ms=metrics["duration_ms"])


def _glb_view(f: dict[str, Any]) -> dict[str, Any]:
    """A Blender frame record → a render_scene.mjs view entry (GLB frame)."""
    v = {"name": f["name"], "kind": f.get("kind") or "authored", "path": f["path"], "time_s": f["time_s"],
         "position": _to_glb(f["position"]), "lookAt": _to_glb(f["look_at"]), "fov": f.get("fov"),
         "render_ms": f.get("render_ms")}
    return {k: v[k] for k in _VIEW_KEYS}


def _renderer_line(frames: dict[str, Any]) -> str:
    bits = [f"Blender {frames.get('blender', '?')}", str(frames.get("engine", "?")), str(frames.get("device", "?"))]
    if frames.get("device_names"):
        bits.append("/".join(frames["device_names"]))
    if frames.get("samples"):
        bits.append(f"{frames['samples']} spp")
    if frames.get("gl_renderer"):
        bits.append(str(frames["gl_renderer"]))
    return " · ".join(bits)


def _geometry_pass(ws: Workspace, out_dir: Path, specs: list[dict[str, Any]], *, times: Sequence[float], width: int,
                   height: int, timeout_s: float, scene_rel: str) -> tuple[dict[str, Any], str]:
    """render_scene.mjs --external-frames → (its metrics payload, an error line or '')."""
    if not specs:
        return {"views": [], "camera_checks": [], "console_errors": []}, ""
    args = ["--ws", str(ws.root), "--out", str(out_dir), "--scene", scene_rel,
            "--cameras", json.dumps(specs), "--orbit-views", "none",
            "--times", ",".join(f"{t:g}" for t in times), "--width", str(width), "--height", str(height),
            "--fps-seconds", "0", "--timeout-ms", str(int(timeout_s * 1000)),
            "--no-settle", "--no-post", "--external-frames", str(out_dir / EXTERNAL_NAME)]
    try:
        run_scene_script("render_scene.mjs", args, timeout_s=timeout_s + 30)
    except SceneRenderError as e:
        metrics = read_json_or_none(out_dir / "metrics.json") or {}
        return metrics, f"geometry pass: {str(e).splitlines()[0][:400]}"
    return read_json_or_none(out_dir / "metrics.json") or {}, ""


def _empty(out_dir: Path, error: str, *, width: int, height: int, orbit_views: Sequence[ViewPreset]) -> RenderSet:
    metrics = {"ok": False, "language": LANGUAGE, "renderer": "", "views": [], "camera_checks": [], "fps": None,
               "census": None, "shader_errors": [], "console_errors": [error]}
    write_json_atomic(out_dir / "metrics.json", metrics)
    write_json_atomic(out_dir / "views.json", [])
    return render_set_from_metrics(out_dir, metrics, width=width, height=height, orbit_views=orbit_views)
