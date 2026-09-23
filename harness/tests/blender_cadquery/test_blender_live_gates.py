"""Live Blender: the lint's BSDF table matches Blender, and the census counts what the exporter exports."""

from __future__ import annotations

import json
import subprocess

import pytest

from codeverse3d.languages.blender import REMOVED_BSDF_INPUTS, BlenderRuntime, blender_env

pytestmark = pytest.mark.blender


BSDF_PROBE = (
    "import bpy, json\n"
    "m = bpy.data.materials.new('probe')\n"
    "b = m.node_tree.nodes['Principled BSDF']\n"
    "print('BSDF_INPUTS=' + json.dumps([i.name for i in b.inputs]))\n"
)


@pytest.mark.blender
def test_lint_bsdf_table_matches_real_principled_inputs(blender_bin) -> None:
    """Finding: 'Anisotropic'/'Specular Tint' are REAL inputs in Blender 5.0.1 — lint must agree."""
    proc = subprocess.run([blender_bin, "-b", "--factory-startup", "--python-expr", BSDF_PROBE],
                          capture_output=True, text=True, timeout=120, env=blender_env())
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("BSDF_INPUTS="))
    real = set(json.loads(line[len("BSDF_INPUTS="):]))
    # every key the lint calls removed must actually be absent (no false 'KeyError at runtime')
    still_there = {k for k in REMOVED_BSDF_INPUTS if k in real}
    assert not still_there, f"lint flags valid Principled BSDF inputs: {sorted(still_there)}"
    # the names the lint (and the contract docs) steer agents toward must all exist
    expected = {"Anisotropic", "Specular Tint", "Specular IOR Level", "Subsurface Weight",
                "Transmission Weight", "Coat Weight", "Coat Roughness", "Sheen Weight",
                "Emission Color", "Emission Strength", "Base Color", "Roughness", "Metallic"}
    assert expected <= real, f"missing from Blender: {sorted(expected - real)}"


#: one build for three census findings: excluded-collection objects, unused / past-the-end
#: material slots (read off the EVALUATED mesh inside Blender, not a fixture), and a light
CENSUS_MODEL = '''import bmesh
import bpy


def part(name, n_slots, location):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=0.2)
    bm.to_mesh(me)
    bm.free()
    for i in range(n_slots):
        me.materials.append(bpy.data.materials.new(f"{name}Mat{i}"))
    obj = bpy.data.objects.new(name, me)
    obj.location = location
    bpy.context.collection.objects.link(obj)
    return obj


part("FlatArm", 3, (0, 0, 0.1))                       # 3 materials, every polygon on slot 0
painted = part("PaintedArm", 3, (0.5, 0, 0.1))
for i, poly in enumerate(painted.data.polygons):
    poly.material_index = i % 3
overrun = part("OverrunArm", 1, (1.0, 0, 0.1))
for poly in overrun.data.polygons:
    poly.material_index = 2                           # past the last slot: Blender clamps

bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5))
main = bpy.context.object
main.name = "Main"
mat = bpy.data.materials.new("MainMat")
main.data.materials.append(mat)

bpy.ops.mesh.primitive_cube_add(size=1, location=(10, 10, 10))
extra = bpy.context.object
extra.name = "InColl"
coll = bpy.data.collections.new("Extra")
bpy.context.scene.collection.children.link(coll)
for c in list(extra.users_collection):
    c.objects.unlink(extra)
coll.objects.link(extra)
bpy.context.view_layer.layer_collection.children["Extra"].exclude = True

bpy.ops.object.light_add(type="SUN")
'''


@pytest.mark.blender
def test_census_matches_export_and_reports_what_the_export_hides(tmp_ws, blender_bin) -> None:
    """Finding: excluded-collection objects were counted in tri_count/bbox but never exported.
    Plus the census warnings a model sees: unused / clamped material slots, a script light."""
    trimesh = pytest.importorskip("trimesh")
    rt = BlenderRuntime(blender=blender_bin)
    (tmp_ws.src / "model.py").write_text(CENSUS_MODEL)
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    c = r.census
    rec = {o["name"]: o for o in c["objects"]}
    assert rec["InColl"]["in_scene"] and not rec["InColl"]["in_view_layer"] and rec["InColl"]["hidden"]
    assert c["tri_count"] == 4 * 12 and c["n_mesh_objects"] == 4  # Main + the three arms, never InColl
    assert c["scene_bbox_max"][2] <= 1.0 + 1e-4  # bbox not stretched to (10,10,10)
    warns = c["warnings"]
    assert any("excluded from the view layer" in w and "InColl" in w for w in warns)
    assert not any("no material" in w for w in warns)  # no spurious warning for InColl
    scene = trimesh.load(r.glb_path)
    # census matches the export (a multi-material mesh loads as one node per material: <name>_<hash>)
    meshes = {"Main", "FlatArm", "PaintedArm", "OverrunArm"}
    assert {n if n in meshes else n.rsplit("_", 1)[0] for n in scene.graph.nodes_geometry} == meshes
    assert rec["FlatArm"]["material_indices_used"] == [0]
    assert rec["PaintedArm"]["material_indices_used"] == [0, 1, 2]
    (unused,) = [w for w in warns if "slots no polygon uses" in w]
    assert "'FlatArm' (3 slots, 1 used)" in unused and "PaintedArm" not in unused
    (clamped,) = [w for w in warns if "past the last slot" in w]
    assert "'OverrunArm'" in clamped
    assert any("light" in w for w in warns)
