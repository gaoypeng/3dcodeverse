"""Blender-side build wrapper for the ``urdf_blender`` language (run BY THE HARNESS).

    blender -b --factory-startup --python run_bpy_links.py -- \
        --script src/model.py --urdf src/robot.urdf --out artifacts/ [--rlimit-gb 12] [--seed 0]

1. empties the scene, seeds, executes ``model.py`` (``_wrapper_common``: errors mapped to
   their ``src/`` file and line, ``sys.exit(0)`` is not a failure — as for the blender language),
2. takes the blender language's census of the scene (``_census.collect_census``),
3. matches URDF link names to objects (an object named exactly ``<link>`` plus
   anything parented under it forms that link's mesh),
4. exports ``<out>/meshes/<link>.glb`` per link with the geometry baked in WORLD
   coordinates at the authored rest pose, Z-up (``export_yup=False`` — the file
   holds URDF-frame coordinates, NOT glTF Y-up), then
5. writes ``<out>/census.json`` and ``<out>/build.json``.

Self-contained on purpose: Blender's bundled python cannot import ``codeverse3d``; the
sibling modules are imported from this file's own directory.
"""

import argparse
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from _census import (  # noqa: E402  (siblings, Blender-executed)
    MEASURABLE_TYPES,
    collect_census,
    is_hidden,
    world_bbox,
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

TRI_LIMIT = 600_000  # a warning here (census), the blender language's build error


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


def _urdf_links(urdf_path: Path) -> list[str]:
    root = ET.parse(urdf_path).getroot()
    return [el.get("name", "") for el in root.findall("link")]


def _evaluated_world_mesh(ob, depsgraph):
    """New bpy Mesh datablock of ``ob`` (modifiers applied) in WORLD coordinates."""
    ev = ob.evaluated_get(depsgraph)
    me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=depsgraph)
    me.transform(ob.matrix_world)
    return me


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
    lo, hi = world_bbox(joined, Vector)
    info = {
        "mesh": f"meshes/{link}.glb", "tris": len(joined.data.loop_triangles), "verts": len(joined.data.vertices),
        "bbox_min": lo, "bbox_max": hi, "objects": [o.name for o in objects],
    }
    out_glb.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(out_glb), export_format="GLB", use_selection=True, export_yup=False, export_apply=True,
        export_materials="EXPORT", export_extras=False, export_animations=False, export_cameras=False,
        export_lights=False, export_normals=True, export_texcoords=True, export_skins=False, export_morph=False,
    )
    bpy.data.objects.remove(joined, do_unlink=True)
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--urdf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rlimit-gb", type=float, default=12.0)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(wrapper_argv())
    t0 = time.monotonic()
    script, urdf, out = Path(args.script).resolve(), Path(args.urdf).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = new_report(args.rlimit_gb, error_file="src/model.py")
    census: dict = {"objects": [], "links": {}, "unmatched_objects": [], "missing_links": [], "hints": {}}

    def fail(error_type: str, message: str, error_file: str = "src/model.py") -> int:
        report.update(error_type=error_type, error_message=message, error_file=error_file)
        write_report(str(out), report, census, t0)
        return 0

    try:
        links = _urdf_links(urdf)
    except ET.ParseError as e:
        return fail("UrdfParseError", str(e), "src/robot.urdf")
    bad = _bad_links(links)
    if bad:
        return fail("UnsafeLinkName", "URDF link name(s) unusable as meshes/<link>.glb filenames "
                    "(must match [A-Za-z][A-Za-z0-9_]*): " + ", ".join(repr(b) for b in bad), "src/robot.urdf")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    seed_everything(args.seed)
    exc, _ = run_script(str(script))
    if exc is not None:
        report.update(map_exception(exc, str(script)))
        write_report(str(out), report, census, t0)
        return 0

    census.update(collect_census(bpy, TRI_LIMIT))
    depsgraph = bpy.context.evaluated_depsgraph_get()
    by_name = {ob.name: ob for ob in bpy.data.objects if ob.type in MEASURABLE_TYPES or ob.type == "EMPTY"}
    # a hidden object (a boolean cutter, as the census tells agents to hide one) is not
    # geometry: the blender language's export skips it by the same predicate
    vl_names = set(bpy.context.view_layer.objects.keys())
    claimed: set[str] = set()
    for link in links:
        ob = by_name.get(link)
        if ob is None:
            census["missing_links"].append(link)
            near = [o.name for o in bpy.data.objects if o.type in MEASURABLE_TYPES and _snake(o.name) == _snake(link)]
            if near:
                census["hints"][link] = f"no object named exactly '{link}' — did you mean {near}? names are case-sensitive"
            continue
        group = [o for o in (ob, *ob.children_recursive) if o.type in MEASURABLE_TYPES and not is_hidden(o, vl_names)]
        if not group:
            census["missing_links"].append(link)
            census["hints"][link] = f"object '{link}' has no visible mesh geometry (type {ob.type})"
            continue
        claimed.update(o.name for o in group)
        try:
            census["links"][link] = _export_link(link, group, out / "meshes" / f"{link}.glb", depsgraph)
        except Exception as e:  # export failure is a build failure
            return fail("ExportError", f"link {link}: {e}")
    census["unmatched_objects"] = [o["name"] for o in census["objects"]
                                   if o["type"] in MEASURABLE_TYPES and not o["hidden"] and o["name"] not in claimed]
    if census["missing_links"]:
        return fail("MissingLinkObjects",
                    "URDF links without a mesh object in model.py: " + ", ".join(census["missing_links"]))
    if census["unmatched_objects"]:
        return fail("UnmatchedObjects", "mesh objects that are not URDF links (parent them under a link object or add "
                    "a <link>): " + ", ".join(census["unmatched_objects"]))
    report["ok"] = True
    write_report(str(out), report, census, t0)
    return 0


exit_after(main)
