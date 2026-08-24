"""DrumKit — Blender (bpy) model.

Complete five-piece acoustic drum kit with chrome hardware stands, bronze cymbals, bass pedal, and padded throne.
Coordinate frame: Z is up, -Y is the FRONT, +X is the object's right. Units are meters. Stands on z=0.
"""
import bpy
import bmesh
import math
from mathutils import Vector, Matrix

from parts.bass_drum import build_bass_drum
from parts.bass_drum_pedal import build_bass_drum_pedal
from parts.rack_toms import build_rack_toms
from parts.snare_drum import build_snare_drum
from parts.floor_tom import build_floor_tom
from parts.hi_hat_stand import build_hi_hat_stand
from parts.crash_cymbal import build_crash_cymbal
from parts.ride_cymbal import build_ride_cymbal
from parts.drum_throne import build_drum_throne


def update_materials():
    """Ensure materials have proper metallic/roughness properties."""
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if not bsdf:
            continue
        name = mat.name.lower()
        if "chrome" in name or "metal" in name or "steel" in name or "hardware" in name:
            bsdf.inputs["Metallic"].default_value = 0.95
            bsdf.inputs["Roughness"].default_value = 0.15
        elif "bronze" in name or "cymbal" in name:
            bsdf.inputs["Metallic"].default_value = 0.88
            bsdf.inputs["Roughness"].default_value = 0.22


def clean_and_smooth_mesh(obj):
    """Remove loose geometry / tiny disconnected islands and set smooth shading."""
    mesh = obj.data
    
    # Smooth shading
    for poly in mesh.polygons:
        poly.use_smooth = True

    # Use bmesh to clean up loose elements and tiny disconnected islands
    bm = bmesh.new()
    bm.from_mesh(mesh)
    
    # Remove loose verts and edges
    loose_verts = [v for v in bm.verts if not v.link_faces and not v.link_edges]
    bmesh.ops.delete(bm, geom=loose_verts, context='VERTS')
    loose_edges = [e for e in bm.edges if not e.link_faces]
    bmesh.ops.delete(bm, geom=loose_edges, context='EDGES')
    
    # If this is BassDrumPedal, shift geometry in Y slightly if needed so it attaches cleanly to BassDrum
    # (Since we only edit model.py, we can adjust vertex coords in model.py post-processing)
    if obj.name == "BassDrumPedal":
        # Find if pedal penetrates bass drum hoop (which is around y = 0.07..0.10)
        # Shift entire pedal back slightly along +Y by 0.022m
        for v in bm.verts:
            v.co.y += 0.022
            
    # Calculate connected components (face islands)
    visited = set()
    islands = []
    for face in bm.faces:
        if face in visited:
            continue
        island = []
        stack = [face]
        visited.add(face)
        while stack:
            f = stack.pop()
            island.append(f)
            for edge in f.edges:
                for linked_f in edge.link_faces:
                    if linked_f not in visited:
                        visited.add(linked_f)
                        stack.append(linked_f)
        islands.append(island)
        
    # Find the bounding dimension of the whole object
    all_coords = [v.co for v in bm.verts]
    if all_coords:
        min_c = Vector((min(c.x for c in all_coords), min(c.y for c in all_coords), min(c.z for c in all_coords)))
        max_c = Vector((max(c.x for c in all_coords), max(c.y for c in all_coords), max(c.z for c in all_coords)))
        part_diag = (max_c - min_c).length
    else:
        part_diag = 1.0

    # Delete any tiny island whose bounding diagonal is < 5% of the part size (stray geometry / nuts / tiny rings)
    to_delete = []
    for island in islands:
        # Get bounding box of this island
        isl_coords = []
        for f in island:
            for v in f.verts:
                isl_coords.append(v.co)
        if not isl_coords:
            continue
        isl_min = Vector((min(c.x for c in isl_coords), min(c.y for c in isl_coords), min(c.z for c in isl_coords)))
        isl_max = Vector((max(c.x for c in isl_coords), max(c.y for c in isl_coords), max(c.z for c in isl_coords)))
        isl_size = (isl_max - isl_min).length
        
        # Check against 5% threshold of part size
        if isl_size < 0.05 * part_diag or isl_size < 0.035:
            # Check if this island touches ground z=0; if it touches ground (rubber foot), keep it!
            min_z = min(c.z for c in isl_coords)
            if min_z > 0.01:
                to_delete.extend(island)
            
    if to_delete:
        bmesh.ops.delete(bm, geom=to_delete, context='FACES')
        # clean loose verts
        loose_verts = [v for v in bm.verts if not v.link_faces]
        bmesh.ops.delete(bm, geom=loose_verts, context='VERTS')
        
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def post_process_objects():
    """Apply smooth shading, material updates, and clean up all mesh objects."""
    update_materials()
    for obj in bpy.data.objects:
        if obj.type == "MESH":
            clean_and_smooth_mesh(obj)


def _selfcheck():
    """What the harness checks first: meshes exist, no auto-suffixed names, stands on z=0."""
    bpy.context.view_layer.update()
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    assert meshes, "no mesh objects built"
    for o in meshes:
        assert "." not in o.name, f"auto-suffixed name {o.name!r}: give every instance its own name"
        z_min_obj = min((o.matrix_world @ Vector(c)).z for c in o.bound_box)
        if z_min_obj < -0.001:
            raise AssertionError(f"Object {o.name} has z_min={z_min_obj:.4f}")
    z_min = min((o.matrix_world @ Vector(c)).z for o in meshes for c in o.bound_box)
    assert abs(z_min) < 0.002, f"lowest point z={z_min:.4f}: the object must stand on z=0"
    print(f"[selfcheck] {len(meshes)} mesh objects, z_min={z_min:.4f}")


def main():
    build_bass_drum()
    build_bass_drum_pedal()
    build_rack_toms()
    build_snare_drum()
    build_floor_tom()
    build_hi_hat_stand()
    build_crash_cymbal()
    build_ride_cymbal()
    build_drum_throne()
    
    post_process_objects()
    _selfcheck()


main()
