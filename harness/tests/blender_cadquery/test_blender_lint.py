"""Offline tests for the bpy lint."""

from __future__ import annotations

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.languages.blender import lint_blender_source

GOOD = '''
import bpy
import bmesh
from mathutils import Vector

bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5), scale=(1, 1, 1))
obj = bpy.context.object
obj.name = "Body"
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
mat = bpy.data.materials.new("BodyMat")
mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (1, 0, 0, 1)
obj.data.materials.append(mat)
bm = bmesh.new()
bm.from_mesh(obj.data)
bm.verts.ensure_lookup_table()
v = bm.verts[0]
bm.free()
'''


def _msgs(report, sev=None):
    return [f.message for f in report.findings if sev is None or f.severity == sev]


def test_good_script_passes() -> None:
    r = lint_blender_source(GOOD)
    assert r.gate == "lint:blender" and r.passed, _msgs(r)
    assert not _msgs(r, Severity.ERROR)
    assert any("Body" in m for m in _msgs(r, Severity.INFO))


def test_missing_bpy_import_and_forbidden_calls() -> None:
    src = "import os\nimport subprocess\nbpy.ops.render.render()\nbpy.ops.export_scene.gltf(filepath='x')\nbpy.ops.wm.save_mainfile()\nopen('f').read()\nos.system('ls')\n"
    r = lint_blender_source(src)
    errs = _msgs(r, Severity.ERROR)
    assert any("never imports bpy" in m for m in errs)
    assert any("bpy.ops.render.render" in m for m in errs)
    assert any("bpy.ops.export_scene.gltf" in m for m in errs)
    assert any("bpy.ops.wm.save_mainfile" in m for m in errs)
    assert any("open()" in m for m in errs)
    assert any("os.system" in m for m in errs)
    assert any("subprocess" in m for m in errs)


def test_bmesh_lookup_table_missing_is_error_even_if_mentioned_in_comment() -> None:
    src = "import bpy, bmesh\nbm = bmesh.new()\nv = bm.verts[0]  # forgot ensure_lookup_table\n"
    r = lint_blender_source(src)
    f = [x for x in r.findings if "ensure_lookup_table" in x.message]
    assert f and f[0].severity == Severity.ERROR and f[0].data["line"] == 3 and "ensure_lookup_table()" in f[0].fix_hint


def test_bmesh_lookup_rule_ignores_non_bmesh_bases() -> None:
    """Finding: e.verts[0] (BMEdge) / me.edges[i] (Mesh) never need ensure_lookup_table."""
    src = ("import bpy, bmesh\n"
           "bm = bmesh.new()\n"
           "side = [e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) > 1e-9]\n"
           "o = bpy.context.object\n"
           "o.data.edges[0].use_seam = True\n"
           "me = o.data\n"
           "me.edges[2].use_seam = True\n"
           "bm.free()\n")
    r = lint_blender_source(src)
    assert not any("ensure_lookup_table" in f.message for f in r.findings), _msgs(r)
    assert r.passed


def test_removed_bsdf_inputs() -> None:
    r = lint_blender_source("import bpy\nm = bpy.data.materials.new('x')\nm.node_tree.nodes['Principled BSDF'].inputs['Specular'].default_value = 0.5\n")
    f = [x for x in r.findings if "Specular" in x.message]
    assert f and f[0].severity == Severity.ERROR and "Specular IOR Level" in f[0].fix_hint


def test_valid_bsdf_inputs_not_flagged() -> None:
    """Finding: 'Anisotropic' and 'Specular Tint' exist in Blender 4.x/5.0.1 — never ERROR."""
    src = ("import bpy\nm = bpy.data.materials.new('x')\nb = m.node_tree.nodes['Principled BSDF']\n"
           "b.inputs['Anisotropic'].default_value = 0.6\n"
           "b.inputs['Specular Tint'].default_value = (1, 1, 1, 1)\n")
    r = lint_blender_source(src)
    assert r.passed and not _msgs(r, Severity.ERROR), _msgs(r)
    tint = [f for f in r.findings if "Specular Tint" in f.message]
    assert tint and tint[0].severity == Severity.INFO and "4-tuple" in tint[0].fix_hint


def test_camera_light_and_render_settings_warn() -> None:
    src = "import bpy\nbpy.ops.object.light_add(type='SUN')\ncam = bpy.data.cameras.new('c')\nbpy.context.scene.render.engine = 'CYCLES'\nbpy.context.scene.camera = None\n"
    r = lint_blender_source(src)
    assert r.passed  # warnings only
    w = _msgs(r, Severity.WARN)
    assert any("light_add" in m for m in w) and any("cameras.new" in m for m in w) and any("render settings" in m for m in w)


def test_headless_pitfalls() -> None:
    src = ("import bpy\nbpy.ops.mesh.primitive_cube_add(scale=(1, 2, 3))\nbpy.ops.object.transform_apply(scale=True)\n"
           "bpy.ops.object.modifier_apply(modifier='Bevel')\nbpy.ops.object.join()\nsel = bpy.context.selected_objects\n"
           "bpy.ops.object.delete({'selected_objects': sel})\nobj = bpy.context.object\nobj.data.use_auto_smooth = True\n")
    r = lint_blender_source(src)
    w = _msgs(r, Severity.WARN)
    e = _msgs(r, Severity.ERROR)
    assert any("size=1" in m for m in w)
    assert any("transform_apply" in m for m in w)
    assert any("modifier_apply" in m for m in w)
    assert any("join" in m for m in w)
    assert any("selected_objects" in m for m in w)
    assert any("context-dict" in m for m in e)
    assert any("use_auto_smooth" in m for m in e)


def test_modifier_apply_with_temp_override_is_fine() -> None:
    src = "import bpy\nobj = bpy.context.object\nwith bpy.context.temp_override(object=obj):\n    bpy.ops.object.modifier_apply(modifier='Bevel')\nobj.name = 'Body'\n"
    r = lint_blender_source(src)
    assert not any("modifier_apply" in m for m in _msgs(r, Severity.WARN))
