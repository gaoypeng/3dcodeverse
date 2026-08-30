"""Scene census for the Blender build wrapper — executed BY Blender, never imported by the harness.

``run_bpy.py`` puts its own directory on ``sys.path`` and does ``import _census``; this
module must therefore stay standalone (no ``codeverse`` imports).  It inspects the scene
after the agent script ran: evaluated triangle counts, world bboxes, materials, parents,
and the contract warnings the build cannot otherwise see (cameras, lights, visible
boolean cutters, unlinked meshes, default primitive names, material slots no polygon uses).
"""

from __future__ import annotations

import re
from typing import Any

DEFAULT_NAME_RE = re.compile(
    r"^(Cube|Cylinder|Sphere|Plane|Cone|Torus|Icosphere|Circle|Grid|Monkey|Mesh|Text|Curve)(\.\d+)?$"
)
MEASURABLE_TYPES = ("MESH", "CURVE", "SURFACE", "FONT", "META")


def world_bbox(obj_eval: Any, Vector: Any) -> tuple[list[float], list[float]] | None:
    """World-space AABB of an evaluated object (rounded to 0.01 mm); None when it has no bounds."""
    corners = [obj_eval.matrix_world @ Vector(c) for c in obj_eval.bound_box]
    if not corners:
        return None
    mn = [min(c[i] for c in corners) for i in range(3)]
    mx = [max(c[i] for c in corners) for i in range(3)]
    return [round(v, 5) for v in mn], [round(v, 5) for v in mx]


def is_hidden(obj: Any, vl_names: set[str] | None = None) -> bool:
    """True when the object will not render/export (hidden or not in the view layer).

    ``vl_names`` is ``set(bpy.context.view_layer.objects.keys())``: objects that are not in
    the view layer (e.g. their collection has ``layer_collection.exclude = True``) report
    ``hide_get() == False`` yet are NOT exported — the census must not count them either.
    """
    if vl_names is not None and obj.name not in vl_names:
        return True
    try:
        return bool(obj.hide_render or obj.hide_viewport or obj.hide_get())
    except RuntimeError:  # object not in the view layer
        return True


def _object_record(obj: Any, dg: Any, scene_objects: set[str], vl_names: set[str], Vector: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "name": obj.name,
        "type": obj.type,
        "parent": obj.parent.name if obj.parent else None,
        "in_scene": obj.name in scene_objects,
        "in_view_layer": obj.name in vl_names,
        "hidden": is_hidden(obj, vl_names),
        "materials": [s.material.name for s in obj.material_slots if s.material],
        "n_material_slots": len(obj.material_slots),
        "modifiers": [m.type for m in obj.modifiers],
        "location": [round(v, 5) for v in obj.matrix_world.translation],
    }
    if obj.type in MEASURABLE_TYPES:
        try:
            ob_eval = obj.evaluated_get(dg)
            me = ob_eval.to_mesh()
            me.calc_loop_triangles()
            rec["tri_count"] = len(me.loop_triangles)
            rec["vert_count"] = len(me.vertices)
            rec["has_uv"] = bool(me.uv_layers)
            rec["has_vertex_colors"] = bool(me.color_attributes)
            if rec["n_material_slots"] and len(me.polygons):
                # which slots the EXPORT actually uses: modifiers can add indices the
                # authored mesh never had (solidify's material_offset), and an index past
                # the last slot is clamped at export instead of raising
                per_poly = [0] * len(me.polygons)
                me.polygons.foreach_get("material_index", per_poly)
                rec["material_indices_used"] = sorted(set(per_poly))
            bb = world_bbox(ob_eval, Vector)
            ob_eval.to_mesh_clear()
        except RuntimeError as e:
            rec["tri_count"] = 0
            rec["census_error"] = str(e)
            bb = None
        if bb:
            rec["bbox_min"], rec["bbox_max"] = bb
    return rec


def _named(items: list[str], limit: int = 6) -> str:
    """Join at most ``limit`` names — the build shows only 10 warnings, one must not eat them all."""
    head = ", ".join(items[:limit])
    return head if len(items) <= limit else f"{head} (+{len(items) - limit} more)"


def material_slot_warnings(meshes: list[dict[str, Any]]) -> list[str]:
    """Material slots the polygons never use — the part was authored in N colours and exports in one.

    Every new polygon starts on slot 0, so an assignment that silently no-opped (a real run
    read ``res['faces']`` from ``bmesh.ops.create_cube``, which returns only ``{'verts'}``,
    and shipped an 11-slot arm in one off-white) is invisible in the census's material list:
    the materials are all there.  Only the per-polygon indices show it.
    """
    unused: list[str] = []
    clamped: list[str] = []
    for o in meshes:
        used = o.get("material_indices_used")
        n_slots = o.get("n_material_slots", 0)
        if not used:
            continue
        if used[-1] >= n_slots:
            clamped.append(f"'{o['name']}' (index {used[-1]}, {n_slots} slot(s))")
        elif len(used) < n_slots:
            unused.append(f"'{o['name']}' ({n_slots} slots, {len(used)} used)")
    warnings: list[str] = []
    if unused:
        warnings.append(
            f"mesh(es) {_named(unused)} carry material slots no polygon uses — those colours never reach "
            "the export; assign per face (for f in bm.faces: f.material_index = k, BEFORE bm.to_mesh, or "
            "poly.material_index on the built mesh — see the cookbook's material_index recipe) or drop the "
            "unused slots"
        )
    if clamped:
        warnings.append(
            f"mesh(es) {_named(clamped)} have polygons on a material_index past the last slot: Blender "
            "clamps them to the last material instead of raising; append the missing materials in slot order"
        )
    return warnings


def _warnings(bpy: Any, objects: list[dict[str, Any]], boolean_operands: dict[str, list[str]],
              scene_objects: set[str], vl_names: set[str], total_tris: int, tri_limit: int) -> list[str]:
    warnings: list[str] = []
    excluded = [o["name"] for o in objects if o["in_scene"] and not o["in_view_layer"]]
    if excluded:
        warnings.append(f"object(s) {excluded} are in a collection excluded from the view layer → NOT exported "
                        "and not counted; re-enable the collection (layer_collection.exclude = False) or remove them")
    cams = [o.name for o in bpy.data.objects if o.type == "CAMERA"]
    lights = [o.name for o in bpy.data.objects if o.type == "LIGHT"]
    if cams:
        warnings.append(f"script created camera(s) {cams}: not exported; remove camera code (the harness owns cameras)")
    if lights:
        warnings.append(f"script created light(s) {lights}: not exported; remove light code (the harness owns lighting)")
    for cutter, users in boolean_operands.items():
        o = bpy.data.objects.get(cutter)
        if o is not None and not is_hidden(o, vl_names) and o.name in scene_objects:
            warnings.append(
                f"object '{cutter}' is a boolean operand of {users} but is still visible and WILL be exported as "
                f"geometry; hide it: obj.hide_set(True); obj.hide_render = True  (or bpy.data.objects.remove(obj))"
            )
    visible = [o for o in objects if o["in_scene"] and not o["hidden"] and o["type"] == "MESH"]
    if not visible:
        warnings.append("no visible mesh objects in the scene — nothing to export")
    for o in visible:
        if not o["materials"]:
            warnings.append(f"mesh '{o['name']}' has no material (will export grey)")
        if DEFAULT_NAME_RE.match(o["name"]):
            warnings.append(f"mesh '{o['name']}' keeps a default primitive name; set obj.name = '<PartName>'")
    warnings.extend(material_slot_warnings(visible))
    unlinked = [o["name"] for o in objects if o["type"] == "MESH" and not o["in_scene"]]
    if unlinked:
        warnings.append(f"mesh object(s) {unlinked} were created but never linked to the scene → not exported; "
                        "call bpy.context.collection.objects.link(obj)")
    if total_tris > tri_limit:
        warnings.append(f"triangle budget exceeded: {total_tris} > {tri_limit}")
    return warnings


def collect_census(bpy: Any, tri_limit: int) -> dict[str, Any]:
    """Census of every object in ``bpy.data`` (visible meshes count toward the scene bbox / tris)."""
    from mathutils import Vector

    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    scene_objects = set(bpy.context.scene.objects.keys())
    vl_names = set(bpy.context.view_layer.objects.keys())  # what the exporter can actually see
    boolean_operands: dict[str, list[str]] = {}
    objects: list[dict[str, Any]] = []
    total_tris = 0
    mins: list[list[float]] = []
    maxs: list[list[float]] = []
    for obj in bpy.data.objects:
        for m in obj.modifiers:
            if m.type == "BOOLEAN" and getattr(m, "object", None) is not None:
                boolean_operands.setdefault(m.object.name, []).append(obj.name)
        rec = _object_record(obj, dg, scene_objects, vl_names, Vector)
        if rec["in_scene"] and not rec["hidden"] and obj.type in MEASURABLE_TYPES:
            total_tris += rec["tri_count"]
            if "bbox_min" in rec:
                mins.append(rec["bbox_min"])
                maxs.append(rec["bbox_max"])
        objects.append(rec)
    visible = [o for o in objects if o["in_scene"] and not o["hidden"] and o["type"] == "MESH"]
    census = {
        "objects": objects,
        "n_mesh_objects": len(visible),
        "tri_count": total_tris,
        "tri_limit": tri_limit,
        "materials": [m.name for m in bpy.data.materials],
        "cameras": [o.name for o in bpy.data.objects if o.type == "CAMERA"],
        "lights": [o.name for o in bpy.data.objects if o.type == "LIGHT"],
        "warnings": _warnings(bpy, objects, boolean_operands, scene_objects, vl_names, total_tris, tri_limit),
        "frame": "z_up_neg_y_front",
    }
    if mins:
        census["scene_bbox_min"] = [round(min(m[i] for m in mins), 5) for i in range(3)]
        census["scene_bbox_max"] = [round(max(m[i] for m in maxs), 5) for i in range(3)]
    return census
