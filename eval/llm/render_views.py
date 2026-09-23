"""Render a GLB from the 4 canonical 3DCodeBench views (Image_005/015/025/035 = azimuth 45/135/225/315 deg).

Camera / light / material setup is the official one (3dcodebench core/render.py): the object is centred at
the origin, cam(θ) = (1.8·extent·cos θ, 1.8·extent·sin θ, 0.6·extent), 50 mm lens, three area lights, a
neutral grey material on every mesh, dark world, transparent film, Cycles (GPU if available) at 512 px.
The official renderer re-executes the generated *script*; we render the exported GLB instead (identical
geometry; the script's own materials survive the GLB round trip, and like the official rig a neutral grey is
applied only to meshes without one — `keep_materials=False` renders everything as grey clay).

    render_glb_views(glb, out_dir) -> {status, n_views, latency_s, error}
    render_dir(gen_dir)            -> renders <gen_dir>/<id>/exec/renders/Image_0xx.png for every OK task
"""
from __future__ import annotations

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import config
from ._jsonl import read_rows

VIEWS = ["Image_005.png", "Image_015.png", "Image_025.png", "Image_035.png"]

BLENDER_SCRIPT = r'''
import sys, os, json, math, time, traceback
import bpy
from mathutils import Vector
argv = sys.argv[sys.argv.index("--") + 1:]
glb, out_dir, samples, res, engine = argv[0], argv[1], int(argv[2]), int(argv[3]), argv[4]
keep_materials = len(argv) < 6 or argv[5] != "strip"
REF_VIEWS = [(5, "Image_005.png"), (15, "Image_015.png"), (25, "Image_025.png"), (35, "Image_035.png")]
rec = {"status": None, "n_views": 0, "latency_s": None, "error": None, "extent": None}
t0 = time.time()
try:
    bpy.ops.object.select_all(action="SELECT"); bpy.ops.object.delete()
    for col in (bpy.data.meshes, bpy.data.curves, bpy.data.cameras, bpy.data.lights, bpy.data.materials, bpy.data.node_groups):
        for item in list(col): col.remove(item)
    bpy.ops.import_scene.gltf(filepath=glb)
    for o in list(bpy.context.scene.objects):
        if o.type in ("CAMERA", "LIGHT"): bpy.data.objects.remove(o, do_unlink=True)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes: raise RuntimeError("no mesh in GLB")
    bpy.context.view_layer.update()
    corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    xs, ys, zs = zip(*[(v.x, v.y, v.z) for v in corners])
    cx, cy, cz = (min(xs)+max(xs))/2, (min(ys)+max(ys))/2, (min(zs)+max(zs))/2
    extent = max(max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs)) or 1.0
    roots = [o for o in bpy.context.scene.objects if o.parent is None]
    for o in roots: o.location = o.location - Vector((cx, cy, cz))
    center = Vector((0, 0, 0)); cam_r, cam_h = extent * 1.8, extent * 0.6
    w = bpy.context.scene.world or bpy.data.worlds.new("World"); bpy.context.scene.world = w; w.use_nodes = True
    for n in list(w.node_tree.nodes): w.node_tree.nodes.remove(n)
    bg = w.node_tree.nodes.new("ShaderNodeBackground"); out = w.node_tree.nodes.new("ShaderNodeOutputWorld")
    bg.inputs[0].default_value = (0.012, 0.013, 0.021, 1); bg.inputs[1].default_value = 1.0
    w.node_tree.links.new(bg.outputs[0], out.inputs[0])
    def add_light(name, loc, energy, sz):
        bpy.ops.object.light_add(type="AREA", location=loc); lt = bpy.context.object
        lt.name, lt.data.energy, lt.data.size = name, energy, extent * sz
        lt.rotation_euler = (center - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    add_light("Key", (cam_r*0.9, -cam_r*0.7, cam_h*1.7), 1200, 0.9)
    add_light("Fill", (-cam_r*0.6, -cam_r*0.4, cam_h), 400, 1.2)
    add_light("Rim", (0, cam_r*0.9, cam_h*1.3), 600, 0.7)
    mat = bpy.data.materials.new("eval_default"); mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.72, 0.72, 0.75, 1); bsdf.inputs["Roughness"].default_value = 0.65
    for o in meshes:                       # official rig: default material only where the script assigned none
        if keep_materials and o.data.materials and any(m is not None for m in o.data.materials): continue
        o.data.materials.clear(); o.data.materials.append(mat)
    bpy.ops.object.camera_add(location=(cam_r, 0, cam_h)); cam = bpy.context.object
    bpy.context.scene.camera = cam; cam.data.lens = 50; cam.data.clip_end = extent * 25
    sc = bpy.context.scene
    sc.render.resolution_x = sc.render.resolution_y = res
    sc.render.image_settings.file_format = "PNG"; sc.render.image_settings.color_mode = "RGBA"
    sc.render.film_transparent = True; sc.render.engine = engine
    if engine == "CYCLES":
        sc.cycles.device = "CPU"
        try:
            pr = bpy.context.preferences.addons["cycles"].preferences
            for dev_type in ("OPTIX", "CUDA"):
                try: pr.compute_device_type = dev_type
                except TypeError: continue
                if hasattr(pr, "refresh_devices"): pr.refresh_devices()
                elif hasattr(pr, "get_devices"): pr.get_devices()
                gpus = [d for d in pr.devices if d.type == dev_type]
                if gpus:
                    for d in pr.devices: d.use = (d.type == dev_type)
                    sc.cycles.device = "GPU"; break
        except Exception: sc.cycles.device = "CPU"
        sc.cycles.samples = samples; sc.cycles.use_denoising = True
    else:
        for attr in ("taa_render_samples", "samples"):
            try: setattr(sc.eevee, attr, samples); break
            except AttributeError: pass
    for frame_idx, fname in REF_VIEWS:
        a = 2 * math.pi * frame_idx / 40
        cam.location = (cam_r * math.cos(a), cam_r * math.sin(a), cam_h)
        cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
        sc.render.filepath = os.path.join(out_dir, fname); bpy.ops.render.render(write_still=True)
        rec["n_views"] += 1
    rec["extent"] = float(extent); rec["status"] = "OK" if rec["n_views"] == 4 else "ERR_RENDER"
except Exception as e:
    rec["status"] = "ERR_RENDER"; rec["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-800:]}"
rec["latency_s"] = round(time.time() - t0, 2)
open(os.path.join(out_dir, "render_log.json"), "w").write(json.dumps(rec, indent=1))
'''


def render_glb_views(glb: str | Path, out_dir: str | Path, samples: int = 64, resolution: int = 512, engine: str = "CYCLES",
                     timeout: int = 240, overwrite: bool = False, keep_materials: bool = True) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "render_log.json"
    if log.exists() and not overwrite and all((out_dir / v).exists() for v in VIEWS):
        return json.loads(log.read_text()) | {"cached": True}
    blender = config.find_blender()
    if blender is None:
        return {"status": "ERR_RENDER", "error": "blender not found", "n_views": 0, "latency_s": 0.0}
    script = out_dir / "_render_views.py"
    script.write_text(BLENDER_SCRIPT)
    cmd = [str(blender), "-b", "--factory-startup", "-noaudio", "--python", str(script), "--",
           str(Path(glb).resolve()), str(out_dir), str(samples), str(resolution), engine, "keep" if keep_materials else "strip"]
    t0 = time.time()
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=config.tool_env(HOME="/tmp", XDG_CONFIG_HOME="/tmp/.bcfg"))
    except subprocess.TimeoutExpired:
        rec = {"status": "ERR_TIMEOUT", "error": f"render > {timeout}s", "n_views": 0, "latency_s": round(time.time() - t0, 1)}
        log.write_text(json.dumps(rec))
        return rec
    if log.exists():
        return json.loads(log.read_text())
    rec = {"status": "ERR_NOLOG", "error": "blender exited without a log", "n_views": 0, "latency_s": round(time.time() - t0, 1)}
    log.write_text(json.dumps(rec))
    return rec


def render_dir(gen_dir: Path, workers: int = 2, samples: int = 64, resolution: int = 512, engine: str = "CYCLES") -> dict[str, dict]:
    """Render every task whose exec_results status is OK; returns {id: render_log}."""
    gen_dir = Path(gen_dir)
    ex = {r["id"]: r for r in read_rows(gen_dir / "exec_results.jsonl")}
    jobs = [(tid, e["mesh"]) for tid, e in ex.items() if e.get("status") == "OK" and e.get("mesh") and str(e["mesh"]).endswith(".glb")]
    print(f"[render] {gen_dir.name}: {len(jobs)} GLBs, {workers} workers, {engine} {samples}spp {resolution}px", flush=True)
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(workers) as pool:
        futs = {pool.submit(render_glb_views, mesh, gen_dir / tid / "exec" / "renders", samples, resolution, engine): tid for tid, mesh in jobs}
        for i, fu in enumerate(as_completed(futs), 1):
            out[futs[fu]] = fu.result()
            if i % 25 == 0:
                print(f"[render] {gen_dir.name} {i}/{len(jobs)}", flush=True)
    ok = sum(r.get("status") == "OK" for r in out.values())
    print(f"[render] {gen_dir.name}: {ok}/{len(jobs)} rendered", flush=True)
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="a .glb file or a gen dir")
    ap.add_argument("--out", default=None)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--engine", default="CYCLES")
    ap.add_argument("--samples", type=int, default=64)
    a = ap.parse_args()
    t = Path(a.target)
    if t.is_file():
        print(json.dumps(render_glb_views(t, a.out or t.parent / "renders", samples=a.samples, engine=a.engine), indent=1))
    else:
        render_dir(t, a.workers, a.samples, engine=a.engine)
