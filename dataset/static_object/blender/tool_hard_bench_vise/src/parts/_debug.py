"""Debug print bbox of all objects."""
import bpy
from mathutils import Vector

def print_bbox(obj):
    bbox_world = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    min_c = Vector((min(v.x for v in bbox_world), min(v.y for v in bbox_world), min(v.z for v in bbox_world)))
    max_c = Vector((max(v.x for v in bbox_world), max(v.y for v in bbox_world), max(v.z for v in bbox_world)))
    center = (min_c + max_c) / 2
    extents = max_c - min_c
    print(f"DEBUG [{obj.name}]: center=({center.x:.4f}, {center.y:.4f}, {center.z:.4f}), extents=({extents.x:.4f}, {extents.y:.4f}, {extents.z:.4f})")
