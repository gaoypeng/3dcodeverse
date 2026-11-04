"""Offline tests for the bpy lint."""

from __future__ import annotations

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.languages.blender import lint_blender_source


def _msgs(report, sev=None):
    return [f.message for f in report.findings if sev is None or f.severity == sev]


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


def test_removed_bsdf_inputs() -> None:
    """A removed input is an ERROR unless its access sits under `'<key>' in x.inputs` (the
    version-proof `elif` 18 of 984 recorded scripts use, e.g. the laptop_urdf model.py)."""
    src = ("import bpy\nm = bpy.data.materials.new('x')\nb = m.node_tree.nodes['Principled BSDF']\n"
           "if 'Specular IOR Level' in b.inputs:\n    b.inputs['Specular IOR Level'].default_value = 0.5\n"
           "elif 'Specular' in b.inputs:\n    b.inputs['Specular'].default_value = 0.5\n"
           "if m and 'Clearcoat' in b.inputs:\n    b.inputs['Clearcoat'].default_value = 0.2\n"
           "b.inputs['Specular'].default_value = 0.5\n")
    r = lint_blender_source(src)
    f = [x for x in r.findings if "does not exist" in x.message]
    assert [(x.severity, x.data["line"]) for x in f] == [(Severity.ERROR, 10)]
    assert "Specular IOR Level" in f[0].fix_hint


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


def test_blender_52_gn_inputs_and_fast_solver_are_errors() -> None:
    """Blender 5.2.2 (D104): `mod["Socket_2"] = v` raises TypeError (geometry-nodes inputs are RNA
    properties now) and the boolean solver 'FAST' no longer exists (FLOAT / EXACT / MANIFOLD)."""
    src = ("import bpy\no = bpy.context.object\nmod = o.modifiers.new('gn', 'NODES')\nmod['Socket_2'] = 5\n"
           "mod['Input_3_attribute_name'] = 'x'\nb = o.modifiers.new('cut', 'BOOLEAN')\nb.solver = 'FAST'\n")
    r = lint_blender_source(src)
    gn = [f for f in r.findings if "geometry-nodes modifier inputs" in f.message]
    assert [(f.severity, f.data["line"]) for f in gn] == [(Severity.ERROR, 4), (Severity.ERROR, 5)]
    assert "mod.properties.inputs" in gn[0].fix_hint
    fast = [f for f in r.findings if "solver 'FAST'" in f.message]
    assert fast and fast[0].severity == Severity.ERROR and fast[0].data["line"] == 7

