"""Blender-side build wrapper for the ``urdf_blender`` language (run BY THE HARNESS).

    blender -b --factory-startup --python run_bpy_links.py -- \
        --script src/model.py --urdf src/robot.urdf --out artifacts/ [--rlimit-gb 12]

1. empties the scene, executes ``model.py`` (errors mapped to its line numbers),
2. takes a census of every mesh-like object (name, tris, WORLD bbox, Z-up),
3. matches URDF link names to objects (an object named exactly ``<link>`` plus
   anything parented under it forms that link's mesh),
4. exports ``<out>/meshes/<link>.glb`` per link with the geometry baked in WORLD
   coordinates at the authored rest pose, Z-up (``export_yup=False`` — the file
   holds URDF-frame coordinates, NOT glTF Y-up), then
5. writes ``<out>/census.json`` and ``<out>/build.json``.

Self-contained on purpose: Blender's bundled python cannot import ``codeverse3d``.
"""

import argparse
import io
import json
import math
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

MESH_LIKE = {"MESH", "CURVE", "FONT", "SURFACE", "META"}


def _snake(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z]+", "_", name.strip())
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", s)
    return re.sub(r"_+", "_", s).strip("_").lower()


#: link names double as ``meshes/<link>.glb`` filename stems: plain identifiers only.
#: The lint layer states the same rule (``languages/urdf/lint._IDENT``) but only WARNs,
#: so the wrapper enforces it — ``../evil`` must be a build error, never a file written
#: outside ``meshes/``.
_SAFE_LINK = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def _bad_links(links: list[str]) -> list[str]:
    """Link names that are unsafe as filenames (not plain identifiers)."""
    return [name for name in links if not _SAFE_LINK.fullmatch(name or "")]


def _set_rlimit(gb: float) -> None:
    try:
        import resource

        lim = int(gb * 1024**3)
        resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
    except Exception as e:  # pragma: no cover
        print(f"[wrapper] rlimit not applied: {e}")


def _urdf_links(urdf_path: Path) -> list[str]:
    root = ET.parse(urdf_path).getroot()
    return [el.get("name", "") for el in root.findall("link")]


def _exec_script(script: Path) -> dict:
    """Run the agent script; return an error dict (empty on success) + captured stdout."""
    code = compile(script.read_text(), str(script), "exec")
    g = {"__name__": "__main__", "__file__": str(script), "__builtins__": __builtins__}
    buf = io.StringIO()
    err: dict = {}
    try:
        with redirect_stdout(buf):
            exec(code, g)
    except BaseException as e:  # noqa: BLE001 — we report everything, including SystemExit
        tb = traceback.extract_tb(e.__traceback__)
        line = None
        for fr in tb:
            if Path(fr.filename).resolve() == script.resolve():
                line = fr.lineno
        err = {
            "error_type": type(e).__name__,
            "error_message": str(e)[:2000],
            "error_line": line,
            "traceback": "".join(traceback.format_exception(type(e), e, e.__traceback__))[-3000:],
        }
    err["script_stdout"] = buf.getvalue()[-4000:]
    return err


def _world_bbox(ob) -> tuple[list, list]:
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for c in ob.bound_box:
        w = ob.matrix_world @ Vector(c)
        for i in range(3):
            lo[i] = min(lo[i], w[i])
            hi[i] = max(hi[i], w[i])
    return lo, hi


def _evaluated_world_mesh(ob, depsgraph):
    """New bpy Mesh datablock of ``ob`` (modifiers applied) in WORLD coordinates."""
    ev = ob.evaluated_get(depsgraph)
    me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=depsgraph)
    me.transform(ob.matrix_world)
    return me


def _descendants(ob) -> list:
    out = []
    stack = list(ob.children)
    while stack:
        c = stack.pop()
        out.append(c)
        stack.extend(c.children)
    return out


def _census(depsgraph) -> list[dict]:
    rows = []
    for ob in bpy.data.objects:
        if ob.type not in MESH_LIKE:
            continue
        ev = ob.evaluated_get(depsgraph)
        me = ev.to_mesh()
        try:
            n_verts = len(me.vertices)
            me.calc_loop_triangles()
            n_tris = len(me.loop_triangles)
        finally:
            ev.to_mesh_clear()
        lo, hi = _world_bbox(ob)
        rows.append({
            "name": ob.name, "type": ob.type, "verts": n_verts, "tris": n_tris,
            "bbox_min": [round(v, 6) for v in lo], "bbox_max": [round(v, 6) for v in hi],
            "parent": ob.parent.name if ob.parent else None,
            "materials": [m.name for m in ob.material_slots if m.material] if hasattr(ob, "material_slots") else [],
        })
    return rows


def _export_link(link: str, objects: list, out_glb: Path, depsgraph) -> dict:
    """Join ``objects`` (world space) into one temp object and export it as GLB."""
    meshes = [_evaluated_world_mesh(ob, depsgraph) for ob in objects]
    temp_objs = []
    for i, me in enumerate(meshes):
        o = bpy.data.objects.new(f"__c3v_{link}_{i}", me)
        bpy.context.scene.collection.objects.link(o)
        temp_objs.append(o)
    bpy.ops.object.select_all(action="DESELECT")
    for o in temp_objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = temp_objs[0]
    if len(temp_objs) > 1:
        bpy.ops.object.join()
    joined = bpy.context.view_layer.objects.active
    joined.name = link
    joined.matrix_world = Matrix.Identity(4)
    bpy.ops.object.select_all(action="DESELECT")
    joined.select_set(True)
    joined.data.calc_loop_triangles()
    lo, hi = _world_bbox(joined)
    info = {
        "mesh": f"meshes/{link}.glb", "tris": len(joined.data.loop_triangles), "verts": len(joined.data.vertices),
        "bbox_min": [round(v, 6) for v in lo], "bbox_max": [round(v, 6) for v in hi],
        "objects": [o.name for o in objects],
    }
    out_glb.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(out_glb), export_format="GLB", use_selection=True, export_yup=False, export_apply=True,
        export_materials="EXPORT", export_extras=False, export_animations=False, export_cameras=False,
        export_lights=False, export_normals=True, export_texcoords=True, export_skins=False, export_morph=False,
    )
    bpy.data.objects.remove(joined, do_unlink=True)
    return info


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--urdf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rlimit-gb", type=float, default=12.0)
    args = ap.parse_args(argv)
    t0 = time.time()
    script, urdf, out = Path(args.script).resolve(), Path(args.urdf).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    _set_rlimit(args.rlimit_gb)
    build: dict = {"ok": False, "language": "urdf_blender", "error_type": "", "error_message": "",
                   "error_file": "src/model.py", "error_line": None, "extra_paths": {}}
    census: dict = {"objects": [], "links": {}, "unmatched_objects": [], "missing_links": [], "hints": {}}

    def finish() -> None:
        build["duration_ms"] = int((time.time() - t0) * 1000)
        (out / "census.json").write_text(json.dumps(census, indent=1))
        (out / "build.json").write_text(json.dumps(build, indent=1))
        print(f"[wrapper] done ok={build['ok']} in {build['duration_ms']} ms")

    try:
        links = _urdf_links(urdf)
    except ET.ParseError as e:
        build.update(error_type="UrdfParseError", error_message=str(e), error_file="src/robot.urdf")
        finish()
        return
    bad = _bad_links(links)
    if bad:
        build.update(error_type="UnsafeLinkName", error_file="src/robot.urdf",
                     error_message="URDF link name(s) unusable as meshes/<link>.glb filenames "
                                   "(must match [A-Za-z][A-Za-z0-9_]*): "
                                   + ", ".join(repr(b) for b in bad))
        finish()
        return

    bpy.ops.wm.read_factory_settings(use_empty=True)
    err = _exec_script(script)
    build["stdout_tail"] = err.pop("script_stdout", "")
    if err:
        build.update(error_type=err["error_type"], error_message=err["error_message"], error_line=err["error_line"],
                     stderr_tail=err["traceback"])
        finish()
        return

    depsgraph = bpy.context.evaluated_depsgraph_get()
    census["objects"] = _census(depsgraph)
    by_name = {ob.name: ob for ob in bpy.data.objects if ob.type in MESH_LIKE or ob.type == "EMPTY"}
    claimed: set[str] = set()
    for link in links:
        ob = by_name.get(link)
        if ob is None:
            census["missing_links"].append(link)
            near = [o.name for o in bpy.data.objects if o.type in MESH_LIKE and _snake(o.name) == _snake(link)]
            if near:
                census["hints"][link] = f"no object named exactly '{link}' — did you mean {near}? names are case-sensitive"
            continue
        group = [ob] + _descendants(ob)
        group = [o for o in group if o.type in MESH_LIKE]
        if not group:
            census["missing_links"].append(link)
            census["hints"][link] = f"object '{link}' has no mesh geometry (type {ob.type})"
            continue
        claimed.update(o.name for o in group)
        try:
            census["links"][link] = _export_link(link, group, out / "meshes" / f"{link}.glb", depsgraph)
        except Exception as e:  # export failure is a build failure
            build.update(error_type="ExportError", error_message=f"link {link}: {e}", error_file="src/model.py")
            finish()
            return
    census["unmatched_objects"] = [r["name"] for r in census["objects"] if r["name"] not in claimed]
    if census["missing_links"]:
        build.update(error_type="MissingLinkObjects", error_file="src/model.py",
                     error_message="URDF links without a mesh object in model.py: " + ", ".join(census["missing_links"]))
        finish()
        return
    if census["unmatched_objects"]:
        build.update(error_type="UnmatchedObjects", error_file="src/model.py",
                     error_message="mesh objects that are not URDF links (parent them under a link object or add a <link>): "
                                   + ", ".join(census["unmatched_objects"]))
        finish()
        return
    build["ok"] = True
    build["extra_paths"] = {"meshes_dir": str(out / "meshes")}
    finish()


main()
