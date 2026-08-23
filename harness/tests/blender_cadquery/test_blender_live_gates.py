"""Live Blender regressions for the review findings (skeleton instances, BSDF names, census).

All tests need the real binary (``-m blender``): they verify the harness's own skeleton
clears its own contract gate, that the lint's Principled BSDF table matches Blender 5.x,
and that the census counts exactly what the exporter exports.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from codeverse.contracts.artifacts import Severity
from codeverse.languages.blender.lint import REMOVED_BSDF_INPUTS
from codeverse.languages.blender.runtime import BlenderRuntime, blender_env

pytestmark = pytest.mark.blender


@pytest.mark.blender
def test_skeleton_with_instances_passes_contract_gate(tmp_ws, table_plan, blender_bin) -> None:
    """Finding: instances under an Empty merged into one GLB part → 'plan part missing' ERROR."""
    from codeverse.spatial.contract import check_contract
    from codeverse.spatial.measure import measure_glb

    rt = BlenderRuntime(blender=blender_bin)
    rt.skeleton(tmp_ws, table_plan)  # multi-file: src/model.py + src/parts/*.py
    assert (tmp_ws.src / "parts" / "leg.py").is_file()
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    m = measure_glb(r.glb_path)
    names = {p.name for p in m.parts}
    assert {"Leg_0", "Leg_1", "Leg_2", "Leg_3"} <= names  # one measured part per instance
    assert "Legs" not in names
    rep = check_contract(m, table_plan, language="blender")
    missing = [f for f in rep.findings if f.severity == Severity.ERROR and "missing" in f.message]
    assert not missing, [(f.target, f.message) for f in missing]
    assert rep.passed, [(f.severity, f.message) for f in rep.findings]


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


EXCLUDED_MODEL = '''import bpy

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
'''


@pytest.mark.blender
def test_census_matches_export_for_excluded_collections(tmp_ws, blender_bin) -> None:
    """Finding: excluded-collection objects were counted in tri_count/bbox but never exported."""
    trimesh = pytest.importorskip("trimesh")
    rt = BlenderRuntime(blender=blender_bin)
    (tmp_ws.src / "model.py").write_text(EXCLUDED_MODEL)
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    c = r.census
    rec = {o["name"]: o for o in c["objects"]}
    assert rec["InColl"]["in_scene"] and not rec["InColl"]["in_view_layer"] and rec["InColl"]["hidden"]
    assert c["tri_count"] == 12 and c["n_mesh_objects"] == 1  # Main only
    assert c["scene_bbox_max"][2] <= 1.0 + 1e-4  # bbox not stretched to (10,10,10)
    assert any("excluded from the view layer" in w and "InColl" in w for w in c["warnings"])
    assert not any("no material" in w for w in c["warnings"])  # no spurious warning for InColl
    scene = trimesh.load(r.glb_path)
    assert set(scene.graph.nodes_geometry) == {"Main"}  # census now matches the export
