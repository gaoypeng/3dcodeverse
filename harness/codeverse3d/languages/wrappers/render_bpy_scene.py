"""Blender render driver for a built ``scene_blender`` workspace; runs inside Blender, never imported by the harness.

    blender -b --factory-startup <ws>/artifacts/scene.blend --python render_bpy_scene.py -- <job.json>

The harness (``spatial/render_blender.py``) writes the job, already in the Blender frame.  The
driver owns all render settings, renders the authored cameras then the harness cameras at each
time (``no_fog`` views drop the world volume, fog boxes and room-shell lids), and writes the
result json atomically.  A failed frame is recorded and the rest still render.  Exit 0 = result
written; 2 = driver bug.  Standalone: stdlib + bpy only."""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import bpy  # noqa: E402
from _census_glb import is_volume_only  # noqa: E402
from _wrapper_common import (  # noqa: E402
    _write_json_atomic,
    exit_after,
    wrapper_argv,
)
from mathutils import Vector  # noqa: E402

#: harness-camera clip range; the far plane is simply out of the way
CLIP_START, CLIP_END = 0.05, 20000.0
LOOK_AHEAD_M = 10.0


def tag(t: float) -> str:
    """``0 -> t0``, ``1.5 -> t1p5``: the same file-name tag as the three.js driver."""
    s = f"{t:.2f}".rstrip("0").rstrip(".")
    return "t" + s.replace(".", "p")


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "camera"


def frame_of(t: float, scene: Any) -> int:
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    return 1 + round(t * fps)


# --------------------------------------------------------------------------- settings
def setup_render(scene: Any, job: dict[str, Any]) -> dict[str, Any]:
    samples = int(job.get("samples") or 32)
    want = str(job.get("device") or "GPU").upper()
    info: dict[str, Any] = {"engine": "cycles", "samples": samples, "device": "CPU", "device_names": [], "device_fallback": ""}
    r = scene.render
    r.engine = "CYCLES"
    r.resolution_x, r.resolution_y = int(job["width"]), int(job["height"])
    r.resolution_percentage = 100
    r.film_transparent = False
    r.use_border = False
    img = r.image_settings
    img.file_format = "PNG"
    img.color_mode = "RGB"
    img.color_depth = "8"
    try:
        scene.display_settings.display_device = "sRGB"
        vs = scene.view_settings
        vs.view_transform = "AgX"
        vs.look = "None"
        vs.exposure = 0.0
        vs.gamma = 1.0
        vs.use_curve_mapping = False
    except (TypeError, AttributeError) as e:
        info["colour_warning"] = str(e)[:200]
    c = scene.cycles
    if want == "GPU":
        # CUDA only: OptiX is never probed (setting compute_device_type to OPTIX crashes Blender under WSL2)
        prefs = bpy.context.preferences.addons["cycles"].preferences
        try:
            prefs.compute_device_type = "CUDA"
            prefs.refresh_devices()
            gpus = [d for d in prefs.devices if d.type == "CUDA"]
        except (TypeError, AttributeError, RuntimeError):
            gpus = []
        for d in prefs.devices:
            d.use = d.type == "CUDA"
        if gpus:
            info["device"] = "GPU"
            info["device_names"] = [d.name for d in gpus]
        else:
            info["device_fallback"] = "no CUDA device"
    c.device = info["device"]
    c.samples = samples
    c.use_adaptive_sampling = True
    c.use_denoising = True
    c.denoiser = "OPENIMAGEDENOISE"
    c.denoising_use_gpu = info["device"] == "GPU"
    c.seed = 0
    c.use_animated_seed = False
    # persistent data keeps the synced scene across a job's cameras; the per-frame time cap bounds sampling (0 = none)
    r.use_persistent_data = True
    c.time_limit = max(0.0, float(job.get("frame_time_limit_s") or 0.0))
    return info


# --------------------------------------------------------------------------- cameras
def _vec(v: Any) -> list[float]:
    return [round(float(x), 5) for x in v]


def vertical_fov_deg(cam: Any, aspect: float) -> float:
    d = cam.data
    fit = d.sensor_fit
    if fit == "VERTICAL":
        return math.degrees(d.angle_y)
    if fit == "HORIZONTAL" or aspect >= 1.0:
        return math.degrees(2 * math.atan(math.tan(d.angle_x / 2) / aspect))
    return math.degrees(d.angle_x)


def authored_cameras(scene: Any) -> list[Any]:
    cams = [o for o in scene.objects if o.type == "CAMERA"]
    order = scene.get("c3d_cameras")
    if order:
        names = [str(n) for n in (json.loads(order) if isinstance(order, str) else list(order))]
        by = {o.name: o for o in cams}
        return [by[n] for n in names if n in by]
    cams.sort(key=lambda o: (o is not scene.camera, o.name))
    return cams


def authored_spec(obj: Any, aspect: float, times: list[float]) -> dict[str, Any]:
    m = obj.matrix_world
    pos = m.translation.copy()
    look = obj.get("c3d_look_at")
    if look is not None and len(look) == 3:
        target = Vector([float(x) for x in look])
    else:
        fwd = (m.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
        target = pos + fwd * LOOK_AHEAD_M
    return {"name": obj.name, "kind": "authored", "position": _vec(pos), "look_at": _vec(target),
            "fov": round(vertical_fov_deg(obj, aspect), 4), "no_fog": False, "times": times, "object": obj}


def harness_camera(spec: dict[str, Any]) -> Any:
    data = bpy.data.cameras.new(f"c3d_{spec['name']}")
    data.sensor_fit = "VERTICAL"
    data.angle_y = math.radians(float(spec.get("fov") or 50.0))
    data.clip_start, data.clip_end = CLIP_START, CLIP_END
    obj = bpy.data.objects.new(f"c3d_{spec['name']}", data)
    bpy.context.scene.collection.objects.link(obj)
    pos, target = Vector(spec["position"]), Vector(spec["look_at"])
    d = target - pos
    if d.length < 1e-9:
        d = Vector((0.0, 1.0, 0.0))
    obj.location = pos
    obj.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return obj


# --------------------------------------------------------------------------- fog
def unlink_world_volume(scene: Any) -> list[tuple[Any, Any]]:
    w = scene.world
    if w is None or w.node_tree is None:
        return []
    removed = []
    for n in w.node_tree.nodes:
        if n.type == "OUTPUT_WORLD" and "Volume" in n.inputs:
            for link in list(n.inputs["Volume"].links):
                removed.append((link.from_socket, link.to_socket))
                w.node_tree.links.remove(link)
    return removed


def relink(scene: Any, links: list[tuple[Any, Any]]) -> None:
    for a, b in links:
        scene.world.node_tree.links.new(a, b)


# --------------------------------------------------------------------------- fog boxes
#: a volume-only mesh at least this wide (m) is a fog box; smaller ones are steam / dust and stay
FOG_BOX_MIN_M = 8.0


def hide_fog_boxes(scene: Any) -> list[Any]:
    """Render-hide every bounded fog volume (volume-only mesh, largest bbox side >= ``FOG_BOX_MIN_M``);
    returns them.  Linked library data and already hidden objects are left alone."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    boxes = []
    for ob in scene.objects:
        if ob.type != "MESH" or ob.hide_render or ob.library is not None or not is_volume_only(ob):
            continue
        ev = ob.evaluated_get(depsgraph)
        corners = [ev.matrix_world @ Vector(c) for c in ev.bound_box]
        if max(max(getattr(c, a) for c in corners) - min(getattr(c, a) for c in corners) for a in "xyz") >= FOG_BOX_MIN_M:
            boxes.append(ob)
    for ob in boxes:
        ob.hide_render = True
    return boxes


# --------------------------------------------------------------------------- room shells
ROOM_SHELL_PREFIX, LID_PREFIX = "RoomShell", "Ceiling"


def lift_lids(scene: Any, camera: Any) -> list[Any]:
    """Render-hide every room-shell lid (a ``Ceiling…`` mesh under a ``RoomShell…`` parent) whose
    bbox top is below ``camera``; returns them.  Mirrors ``scene_host.skyLayersBelow``."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eye_z = camera.evaluated_get(depsgraph).matrix_world.translation.z
    lids = []
    for ob in scene.objects:
        if ob.type != "MESH" or ob.hide_render or ob.parent is None or ob.library is not None:
            continue
        if not (ob.name.startswith(LID_PREFIX) and ob.parent.name.startswith(ROOM_SHELL_PREFIX)):
            continue
        ev = ob.evaluated_get(depsgraph)
        top = max((ev.matrix_world @ Vector(corner)).z for corner in ev.bound_box)
        if top < eye_z:
            lids.append(ob)
    for ob in lids:
        ob.hide_render = True
    return lids


# --------------------------------------------------------------------------- main
def main() -> int:
    job_path = wrapper_argv()[0]
    with open(job_path) as fh:
        job = json.load(fh)
    t_start = time.perf_counter()
    scene = bpy.context.scene
    out = job["out"]
    os.makedirs(out, exist_ok=True)
    result: dict[str, Any] = {"ok": False, "blender": bpy.app.version_string, "blend": bpy.data.filepath,
                              "frames": [], "errors": []}
    result.update(setup_render(scene, job))
    aspect = float(job["width"]) / float(job["height"])
    times = sorted({float(t) for t in job.get("times") or [0.0]})
    scene.frame_set(frame_of(times[0], scene))
    specs: list[dict[str, Any]] = []
    if job.get("authored", True):
        specs += [authored_spec(o, aspect, times) for o in authored_cameras(scene)]
    for s in job.get("cameras") or []:
        specs.append({**s, "kind": s.get("kind") or "orbit", "times": sorted(float(t) for t in s.get("times") or times),
                      "object": None})
    seen: set[str] = set()
    unique = []
    for s in specs:
        s["file_stem"] = safe_name(str(s["name"]))
        if s["file_stem"] in seen:
            continue
        seen.add(s["file_stem"])
        unique.append(s)
    for s in unique:
        if s["object"] is None:
            s["object"] = harness_camera(s)
    all_times = sorted({t for s in unique for t in s["times"]})
    for t in all_times:
        frame = frame_of(t, scene)
        scene.frame_set(frame)
        for s in unique:
            if t not in s["times"]:
                continue
            path = f"{s['file_stem']}_{tag(t)}.png"
            scene.camera = s["object"]
            scene.render.filepath = os.path.join(out, path)
            fog = unlink_world_volume(scene) if s.get("no_fog") else []
            lids = lift_lids(scene, s["object"]) if s.get("no_fog") else []
            hazes = hide_fog_boxes(scene) if s.get("no_fog") else []
            t0 = time.perf_counter()
            try:
                bpy.ops.render.render(write_still=True)
            except Exception as e:  # noqa: BLE001
                result["errors"].append(f"render failed for '{s['name']}' at t={t:g}: {type(e).__name__}: {e}"[:600])
                continue
            finally:
                if fog:
                    relink(scene, fog)
                for ob in (*lids, *hazes):
                    ob.hide_render = False
            ms = int((time.perf_counter() - t0) * 1000)
            if not os.path.isfile(os.path.join(out, path)):
                result["errors"].append(f"render of '{s['name']}' at t={t:g} wrote no image")
                continue
            result["frames"].append({
                "name": s["name"], "kind": s["kind"], "path": path, "time_s": t, "frame": frame,
                "position": s["position"], "look_at": s["look_at"], "fov": s["fov"],
                "no_fog": bool(s.get("no_fog")), "hidden": sorted(ob.name for ob in lids),
                "fog_hidden": sorted(ob.name for ob in hazes), "render_ms": ms,
            })
    result["cameras"] = [{k: v for k, v in s.items() if k not in ("object", "file_stem")} for s in unique]
    result["ok"] = bool(result["frames"]) and not result["errors"]
    result["duration_ms"] = int((time.perf_counter() - t_start) * 1000)
    _write_json_atomic(job["result"], result)
    return 0


if __name__ == "__main__":
    exit_after(main)
