"""Offline tests for the bpy lint."""

from __future__ import annotations

from pathlib import Path

from codeverse.contracts.artifacts import Severity
from codeverse.languages.blender.lint import lint_blender_file, lint_blender_source

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


def test_syntax_error() -> None:
    r = lint_blender_source("import bpy\nx = (\n")
    assert not r.passed and r.findings[0].data["line"] == 2 and "SyntaxError" in r.findings[0].message


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


def test_vector_without_import() -> None:
    r = lint_blender_source("import bpy\nv = Vector((1, 2, 3))\n")
    f = [x for x in r.findings if "Vector" in x.message]
    assert f and f[0].severity == Severity.ERROR and f[0].fix_hint == "from mathutils import Vector"


def test_removed_bsdf_inputs() -> None:
    r = lint_blender_source("import bpy\nm = bpy.data.materials.new('x')\nm.node_tree.nodes['Principled BSDF'].inputs['Specular'].default_value = 0.5\n")
    f = [x for x in r.findings if "Specular" in x.message]
    assert f and f[0].severity == Severity.ERROR and "Specular IOR Level" in f[0].fix_hint


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


def test_missing_file(tmp_path: Path) -> None:
    r = lint_blender_file(tmp_path / "model.py")
    assert not r.passed and "missing" in r.findings[0].message
