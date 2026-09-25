"""Blender SCENE build wrapper (scene_blender) — executed BY Blender, never imported by the harness.

    blender -b --factory-startup --python run_bpy_scene.py -- \
        --scene /abs/src/scene.py --out /abs/artifacts --basis 1,0,0,0,0,1,0,-1,0 \
        [--fps 30] [--frames 1,46] [--rlimit-gb 12] [--seed 0] [--zones '{"a": "A"}'] [--no-blend]

What it does (in order), DESIGN §3.3:
  1. caps the address space, empties the factory scene (and its world), metric units, ``fps`` and
     the frame range (harness-owned), seeds ``random`` / numpy / mathutils.noise;
  2. runs ``src/scene.py`` (assembled by the harness: ``ZONES`` / ``ASSETS`` / ``HEROES`` /
     ``CAMERAS`` data; a single-file scene defines ``create_scene(ctx)`` instead) — a failure here
     is the build's error, mapped to ``src/<file>:<line>``;
  3. builds the ctx (``SceneContext``) and runs, EACH IN ITS OWN try/except, ``env.build_env``,
     every hero import (``public/assets/<snake>.glb``), every ``assets.<snake>.build_<snake>`` and
     every ``zones.<snake>.build``: a stage that raises is reported with its ``file:line`` and
     whatever it created is removed — the zone is DROPPED, the rest of the scene still builds and
     is measured (the assembler's probe semantics);
  4. hides the asset sources (they render through their collection instances), creates the
     harness cameras (plan fov is VERTICAL, as in three.js), sets frame 1 (t = 0);
  5. writes the bpy facts the GLB cannot carry (``bpy_census.json``: lights, fog, background,
     drivers that will not run, unique vs instanced triangles), the census GLB (``_census_glb``)
     and the compressed ``scene.blend`` the render driver opens;
  6. writes ``bpy_build.json`` and exits 0.

``--zones`` overrides ``ZONES`` (the assembler's probe: every zone module on disk).  Exit codes: 0 =
reported (even if the script failed); 2 = wrapper bug (no report).  Standalone: the sibling modules
are imported from this file's own directory.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import math
import os
import random
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from _census_glb import (  # noqa: E402  (siblings)
    is_volume_material,
    is_volume_only,
    write_census_glb,
)
from _wrapper_common import (  # noqa: E402
    exit_after,
    map_exception,
    new_report,
    run_script,
    seed_everything,
    wrapper_argv,
    write_report,
)
from run_bpy import MEMORY_HINT, clear_scene, render_fingerprint  # noqa: E402

REPORT_NAME, FACTS_NAME = "bpy_build.json", "bpy_census.json"
GLB_NAME, BLEND_NAME = "census.glb", "scene.blend"
#: the workspace packages a scene imports from ``src/`` (purged before every run: never a stale module)
PACKAGES = ("env", "zones", "assets", "lib")
ASSETS_COLLECTION, CAMERAS_COLLECTION = "_Assets", "_Cameras"
_ERROR_KEYS = ("error_type", "error_message", "error_file", "error_line", "error_source")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="run_bpy_scene.py")
    p.add_argument("--scene", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--basis", required=True, help="9 numbers, row-major: Blender frame -> GLB frame")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--frames", default="1,46", help="first,last frame of the harness frame range")
    p.add_argument("--rlimit-gb", type=float, default=12.0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--zones", default=None, help='JSON {"snake": "Pascal"} that overrides ZONES')
    p.add_argument("--no-blend", action="store_true")
    return p.parse_args(argv)


class SceneContext:
    """What ``build_env`` / ``build_<asset>`` / zone ``build`` receive (DESIGN §3.2): data, never verbs.

    ``scene`` the bpy Scene (render settings are harness-owned) · ``collection`` the Collection this
    builder links into (the zone's own, pre-created and active) · ``height_at(x, y) -> z`` the
    env's ground · ``env`` what ``build_env`` returned · ``assets`` ``{snake: Collection}`` (procedural
    assets and imported heroes) · ``rng(seed)`` the only randomness · ``fps`` / ``times`` the
    harness's frame clock: ``frame(t) = 1 + round(t * fps)``."""

    def __init__(self, scene: Any, fps: int, times: tuple[float, ...]) -> None:
        self.scene = scene
        self.collection = scene.collection
        self.height_at = _flat
        self.env: dict[str, Any] = {}
        self.assets: dict[str, Any] = {}
        self.fps = fps
        self.times = times

    @staticmethod
    def rng(seed: int = 0) -> random.Random:
        return random.Random(seed)

    def frame(self, t: float) -> int:
        return 1 + round(t * self.fps)


def _flat(x: float, y: float) -> float:
    return 0.0


# ----------------------------------------------------------------------------- scene prep
def prepare_scene(bpy: Any, fps: int, frames: tuple[int, int]) -> None:
    clear_scene(bpy)
    for world in list(bpy.data.worlds):   # a scene's world is the env's to build (no factory grey)
        bpy.data.worlds.remove(world)
    scene = bpy.context.scene
    scene.render.fps = fps
    scene.render.fps_base = 1.0
    scene.frame_start, scene.frame_end = frames
    scene.frame_set(frames[0])


def purge_workspace_modules() -> None:
    for name in [m for m in sys.modules if m.split(".")[0] in PACKAGES]:
        del sys.modules[name]
    importlib.invalidate_caches()


def layer_collection(view_layer: Any, coll: Any) -> Any:
    stack = [view_layer.layer_collection]
    while stack:
        lc = stack.pop()
        if lc.collection == coll:
            return lc
        stack.extend(lc.children)
    return None


def new_collection(bpy: Any, name: str, parent: Any) -> Any:
    coll = bpy.data.collections.new(name)
    parent.children.link(coll)
    return coll


def activate(bpy: Any, coll: Any) -> None:
    """Make ``coll`` the active collection, so ``bpy.ops`` primitives land in it."""
    lc = layer_collection(bpy.context.view_layer, coll)
    if lc is not None:
        bpy.context.view_layer.active_layer_collection = lc


class Stages:
    """Runs one builder at a time; a raise is recorded with its ``src/`` file:line and everything
    the builder created is removed again (objects and collections)."""

    def __init__(self, bpy: Any, script: str, report: dict[str, Any]) -> None:
        self.bpy, self.script, self.report = bpy, script, report

    def run(self, stage: str, name: str, default_file: str, fn: Any) -> tuple[Any, bool]:
        bpy = self.bpy
        objs = {o.as_pointer() for o in bpy.data.objects}
        colls = {c.as_pointer() for c in bpy.data.collections}
        try:
            return fn(), True
        except BaseException as e:  # noqa: BLE001 — a builder's failure is a finding, never the wrapper's
            if isinstance(e, KeyboardInterrupt):
                raise
            info = map_exception(e, self.script, memory=MEMORY_HINT)
            entry = {"stage": stage, "name": name, **{k: info[k] for k in _ERROR_KEYS},
                     "traceback": info["traceback"][-2500:]}
            entry["error_file"] = entry["error_file"] or default_file
            self.report["errors"].append(entry)
            print(info["traceback"], file=sys.stderr, end="")
            with contextlib.suppress(Exception):
                if bpy.context.object and bpy.context.object.mode != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
            for o in [o for o in bpy.data.objects if o.as_pointer() not in objs]:
                bpy.data.objects.remove(o, do_unlink=True)
            for c in [c for c in bpy.data.collections if c.as_pointer() not in colls]:
                bpy.data.collections.remove(c)
            return None, False


# ----------------------------------------------------------------------------- stages
def build_env(bpy: Any, ctx: SceneContext, stages: Stages, src: str) -> None:
    if not os.path.isfile(os.path.join(src, "env.py")):
        return

    def run() -> None:
        mod = importlib.import_module("env")
        if callable(getattr(mod, "height_at", None)):
            ctx.height_at = mod.height_at
        if not callable(getattr(mod, "build_env", None)):
            raise AttributeError("src/env.py defines no build_env(ctx)")
        activate(bpy, bpy.context.scene.collection)
        ctx.collection = bpy.context.scene.collection
        out = mod.build_env(ctx)
        ctx.env = out if isinstance(out, dict) else {}

    stages.run("env", "env", "src/env.py", run)


def build_assets(bpy: Any, ctx: SceneContext, stages: Stages, ws: str, heroes: dict[str, str],
                 assets: dict[str, str], report: dict[str, Any]) -> Any:
    root = new_collection(bpy, ASSETS_COLLECTION, bpy.context.scene.collection)
    for snake, pascal in heroes.items():
        glb = os.path.join(ws, "public", "assets", f"{snake}.glb")
        if not os.path.isfile(glb):
            report["warnings"].append(f"hero public/assets/{snake}.glb is missing: zones placing it get no ctx.assets['{snake}']")
            continue

        def load(snake: str = snake, pascal: str = pascal, glb: str = glb) -> Any:
            coll = new_collection(bpy, pascal, root)
            activate(bpy, coll)
            bpy.ops.import_scene.gltf(filepath=glb)
            return coll

        coll, ok = stages.run("hero", snake, f"public/assets/{snake}.glb", load)
        if ok:
            ctx.assets[snake] = coll
    for snake, pascal in assets.items():
        def make(snake: str = snake, pascal: str = pascal) -> Any:
            mod = importlib.import_module(f"assets.{snake}")
            fn = getattr(mod, f"build_{snake}", None)
            if not callable(fn):
                raise AttributeError(f"src/assets/{snake}.py defines no build_{snake}(ctx, variant=0)")
            coll = new_collection(bpy, pascal, root)
            activate(bpy, coll)
            ctx.collection = coll
            out = fn(ctx)
            if isinstance(out, bpy.types.Collection) and out != coll:
                if out.name not in root.children:
                    root.children.link(out)
                for parent in (bpy.context.scene.collection,):
                    if out.name in parent.children:
                        parent.children.unlink(out)
                return out
            return coll

        coll, ok = stages.run("asset", snake, f"src/assets/{snake}.py", make)
        if ok:
            ctx.assets[snake] = coll
    return root


def build_zones(bpy: Any, ctx: SceneContext, stages: Stages, zones: dict[str, str], report: dict[str, Any]) -> dict[str, str]:
    """Build every zone; returns ``{snake: collection name}`` of the zones that built."""
    scene_coll = bpy.context.scene.collection
    built: dict[str, str] = {}
    for snake, pascal in zones.items():
        def make(snake: str = snake, pascal: str = pascal) -> str:
            mod = importlib.import_module(f"zones.{snake}")
            if not callable(getattr(mod, "build", None)):
                raise AttributeError(f"src/zones/{snake}.py defines no build(ctx)")
            coll = new_collection(bpy, pascal, scene_coll)
            activate(bpy, coll)
            ctx.collection = coll
            out = mod.build(ctx)
            if isinstance(out, bpy.types.Collection) and out != coll:
                if out.name not in scene_coll.children and not any(out.name in c.children for c in bpy.data.collections):
                    scene_coll.children.link(out)
                if not coll.all_objects:
                    bpy.data.collections.remove(coll)
                coll = out
            if coll.name != pascal:
                report["warnings"].append(f"zone {snake}: its collection is named {coll.name!r}, not {pascal!r}")
            return coll.name

        name, ok = stages.run("zone", snake, f"src/zones/{snake}.py", make)
        if ok:
            built[snake] = name
        else:
            report["zones_failed"][snake] = report["errors"][-1]["error_message"][:600]
    return built


def add_cameras(bpy: Any, cams: list[dict[str, Any]], report: dict[str, Any]) -> None:
    from mathutils import Vector  # noqa: PLC0415 — Blender-only

    coll = new_collection(bpy, CAMERAS_COLLECTION, bpy.context.scene.collection)
    first = None
    for c in cams:
        data = bpy.data.cameras.new(str(c["name"]))
        data.sensor_fit = "VERTICAL"   # the plan's fov is vertical, as three.js's PerspectiveCamera.fov
        data.angle_y = math.radians(float(c.get("fov", 50.0)))
        data.clip_start, data.clip_end = 0.05, 20000.0
        ob = bpy.data.objects.new(str(c["name"]), data)
        coll.objects.link(ob)
        ob.location = Vector(c["location"])
        ob.rotation_euler = (Vector(c["look_at"]) - ob.location).to_track_quat("-Z", "Y").to_euler()
        ob["c3d_look_at"] = [float(v) for v in c["look_at"]]   # the render driver's aim (Blender frame)
        report["cameras"].append({"name": str(c["name"]), "object": ob.name})
        first = first or ob
    # the plan's camera order, by object name (the render driver reads it from the .blend)
    bpy.context.scene["c3d_cameras"] = [c["object"] for c in report["cameras"]]
    if first is not None:
        bpy.context.scene.camera = first


# ----------------------------------------------------------------------------- facts
def _visible(inst: Any) -> bool:
    host = inst.parent.original if inst.is_instance and inst.parent is not None else inst.object.original
    return not host.hide_render


def _tris(ob: Any) -> int:
    if ob.type == "MESH":
        ob.data.calc_loop_triangles()
        return len(ob.data.loop_triangles)
    try:
        me = ob.to_mesh()
    except RuntimeError:
        return 0
    try:
        me.calc_loop_triangles()
        return len(me.loop_triangles)
    finally:
        ob.to_mesh_clear()


def _volume_density(node: Any) -> float | None:
    sock = node.inputs.get("Density") if node is not None else None
    return float(sock.default_value) if sock is not None and not sock.is_linked else None


def _material_density(mat: Any) -> float | None:
    """The density of the volume shader a volume material outputs (``None`` when unreadable)."""
    tree = getattr(mat, "node_tree", None) if mat is not None else None
    out = next((n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial"), None) if tree is not None else None
    if out is None or not out.inputs["Volume"].is_linked:
        return None
    return _volume_density(out.inputs["Volume"].links[0].from_node)


def _world_facts(world: Any) -> tuple[dict[str, Any] | None, str | None]:
    """(world-volume fog | None, background kind | None) of the scene's world."""
    tree = getattr(world, "node_tree", None) if world is not None else None
    if tree is None:
        return None, None
    out = next((n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputWorld" and n.is_active_output),
               next((n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputWorld"), None))
    if out is None:
        return None, None
    fog = None
    if out.inputs["Volume"].is_linked:
        fog = {"type": "WorldVolume", "near": None, "far": None,
               "density": _volume_density(out.inputs["Volume"].links[0].from_node)}
    background = None
    if out.inputs["Surface"].is_linked:
        node = out.inputs["Surface"].links[0].from_node
        colour = node.inputs.get("Color") if node is not None else None
        if colour is not None and colour.is_linked:
            src = colour.links[0].from_node
            background = {"ShaderNodeTexSky": f"sky:{getattr(src, 'sky_type', '')}",
                          "ShaderNodeTexEnvironment": "environment_texture"}.get(src.bl_idname, f"node:{src.bl_idname}")
        elif colour is not None:
            r, g, b = (max(0.0, min(1.0, float(v))) for v in list(colour.default_value)[:3])
            background = f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"
        else:
            background = f"node:{node.bl_idname}"
    return fog, background


def _drivers(bpy: Any) -> list[dict[str, Any]]:
    """Drivers that will not evaluate here: a Python (non-simple) expression — silently 0 under
    ``--factory-startup`` (DESIGN §1.1) — or one Blender reports invalid (D10)."""
    out: list[dict[str, Any]] = []
    blocks = [("object", bpy.data.objects), ("material", bpy.data.materials), ("node_group", bpy.data.node_groups),
              ("world", bpy.data.worlds), ("shape_key", bpy.data.shape_keys), ("mesh", bpy.data.meshes),
              ("light", bpy.data.lights), ("scene", bpy.data.scenes)]
    for kind, coll in blocks:
        for idb in coll:
            holders = [idb, getattr(idb, "node_tree", None)]
            for h in holders:
                ad = getattr(h, "animation_data", None) if h is not None else None
                for fc in (ad.drivers if ad is not None else ()):
                    d = fc.driver
                    python = d.type == "SCRIPTED" and not d.is_simple_expression
                    if python or not d.is_valid:
                        out.append({"id": f"{kind}:{idb.name}", "path": fc.data_path, "index": fc.array_index,
                                    "expression": d.expression if d.type == "SCRIPTED" else d.type,
                                    "python": python, "valid": bool(d.is_valid)})
    return out[:50]


def handler_counts(bpy: Any) -> dict[str, int]:
    """``{handler list: length}`` — Blender's own add-ons register some at startup, so what the
    scene added is what grew."""
    return {name: len(getattr(bpy.app.handlers, name)) for name in dir(bpy.app.handlers)
            if not name.startswith("_") and isinstance(getattr(bpy.app.handlers, name), list)}


def scene_facts(bpy: Any, handlers_before: dict[str, int]) -> dict[str, Any]:
    """What only Blender knows (DESIGN §4.2): the keys the JS census fills for three.js
    (``fog`` / ``background`` / ``environment`` / lights) plus drivers, handlers and triangles."""
    dg = bpy.context.evaluated_depsgraph_get()
    light_types: dict[str, int] = {}
    unique: dict[Any, int] = {}
    instanced = 0
    volumes: list[dict[str, Any]] = []
    for inst in dg.object_instances:
        ob = inst.object
        if not _visible(inst):
            continue
        if ob.type == "LIGHT":
            light_types[ob.data.type] = light_types.get(ob.data.type, 0) + 1
            continue
        if ob.type not in ("MESH", "CURVE", "SURFACE", "FONT", "META") or (inst.is_instance and ob.type != "MESH"):
            continue
        src = ob if inst.is_instance else ob.original
        if is_volume_only(src):
            if not inst.is_instance:
                dens = [d for d in map(_material_density, (s.material for s in src.material_slots)) if d is not None]
                volumes.append({"object": src.name, "density": max(dens) if dens else None,
                                "size_m": [round(float(v), 3) for v in ob.dimensions]})
            continue
        n = _tris(ob)
        instanced += n
        unique.setdefault(ob.data.as_pointer() if inst.is_instance else ("obj", ob.original.name), n)
    fog, background = _world_facts(bpy.context.scene.world)
    if fog is None and volumes:
        big = max(volumes, key=lambda v: v["size_m"][0] * v["size_m"][1] * v["size_m"][2])
        fog = {"type": "VolumeBox", "near": None, "far": None, "density": big["density"], "object": big["object"],
               "size_m": big["size_m"]}
    handlers = sorted(n for n, k in handler_counts(bpy).items() if k > handlers_before.get(n, 0))
    return {
        "lights": sum(light_types.values()),
        "light_types": light_types,
        "fog": fog,
        "background": background,
        "environment": bpy.context.scene.world is not None,
        "volumes": volumes,
        "drivers_invalid": _drivers(bpy),
        "handlers": handlers,
        "unique_tris": sum(unique.values()),
        "instanced_tris": instanced,
        "volume_materials": sorted(m.name for m in bpy.data.materials if is_volume_material(m))[:20],
    }


# ----------------------------------------------------------------------------- main
def main() -> int:
    args = parse_args(wrapper_argv())
    import bpy  # noqa: PLC0415 — only available inside Blender

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    script = os.path.abspath(args.scene)
    src = os.path.dirname(script)
    ws = os.path.dirname(src)
    t0 = time.monotonic()
    report = new_report(args.rlimit_gb, errors=[], zones_ok=[], zones_failed={}, cameras=[])
    names = (REPORT_NAME, FACTS_NAME)
    if not os.path.isfile(script):
        report.update(error_type="FileNotFoundError", error_message=f"script not found: {script}")
        write_report(out_dir, report, {}, t0, names=names)
        return 0
    first, last = (int(v) for v in args.frames.split(","))
    prepare_scene(bpy, args.fps, (first, last))
    seed_everything(args.seed)
    render_before = render_fingerprint(bpy)
    handlers_before = handler_counts(bpy)
    purge_workspace_modules()

    t_exec = time.monotonic()
    exc, g = run_script(script)   # src/ goes first on sys.path: `import env`, `zones.<snake>` resolve there
    if exc is not None:
        report.update(map_exception(exc, script, memory=MEMORY_HINT))
        report["exec_ms"] = int((time.monotonic() - t_exec) * 1000)
        write_report(out_dir, report, {}, t0, names=names)
        return 0
    g = g or {}
    zones: dict[str, str] = dict(g.get("ZONES") or {})
    if args.zones is not None:
        zones = {str(k): str(v) for k, v in json.loads(args.zones).items()}
    ctx = SceneContext(bpy.context.scene, args.fps, tuple(float(t) for t in g.get("TIMES", (0.0, 1.5))))
    stages = Stages(bpy, script, report)
    build_env(bpy, ctx, stages, src)
    assets_root = build_assets(bpy, ctx, stages, ws, dict(g.get("HEROES") or {}), dict(g.get("ASSETS") or {}), report)
    if callable(g.get("create_scene")):   # single-file mode: one scene.py builds everything
        activate(bpy, bpy.context.scene.collection)
        ctx.collection = bpy.context.scene.collection
        stages.run("scene", "create_scene", "src/scene.py", lambda: g["create_scene"](ctx))
    built = build_zones(bpy, ctx, stages, zones, report)
    report["zones_ok"] = sorted(built)
    report["exec_ms"] = int((time.monotonic() - t_exec) * 1000)
    with contextlib.suppress(RuntimeError):
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    lc = layer_collection(bpy.context.view_layer, assets_root)
    if lc is not None:
        lc.exclude = True   # sources at the origin render only through their instances
    add_cameras(bpy, list(g.get("CAMERAS") or []), report)
    if render_fingerprint(bpy) != render_before:
        report["warnings"].append("the scene changed render settings (engine/resolution/filepath/frames): "
                                  "the harness owns them; delete that code")
    bpy.context.scene.frame_set(first)   # the census is the t = 0 pose

    facts = scene_facts(bpy, handlers_before)
    basis = [[float(v) for v in args.basis.split(",")[i * 3:i * 3 + 3]] for i in range(3)]
    try:
        facts["glb"] = write_census_glb(bpy, os.path.join(out_dir, GLB_NAME), basis=basis,
                                        zones={name: name for name in built.values()}, cameras=list(g.get("CAMERAS") or []))
        report["exported"]["glb"] = os.path.join(out_dir, GLB_NAME)
    except Exception as e:  # noqa: BLE001 — a writer failure is the harness's, reported as such
        import traceback  # noqa: PLC0415

        report.update(error_type="CensusGlbError", error_message=f"census GLB writer failed: {e}",
                      traceback=traceback.format_exc()[-4000:])
    if not args.no_blend:
        blend = os.path.join(out_dir, BLEND_NAME)
        try:
            bpy.ops.wm.save_as_mainfile(filepath=blend, compress=True, copy=True)
            report["exported"]["blend"] = blend
        except Exception as e:  # noqa: BLE001
            report["warnings"].append(f".blend save failed: {e}")
    report["ok"] = not report["error_type"] and "glb" in report["exported"]
    write_report(out_dir, report, facts, t0, names=names)
    return 0


if __name__ == "__main__":
    exit_after(main)
