"""``languages/file_lint.py``: one file → one cheap verdict, per language, never raising."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse.languages.file_lint import FileVerdict, lint_one_file


def _v(language: str, rel: str, text: str, tmp_path: Path, **kw) -> FileVerdict:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return lint_one_file(language, rel, text, p, **kw)


def test_blender_good_and_broken(tmp_path):
    ok = _v("blender", "src/parts/seat.py", "import bpy\n\n\ndef build_seat():\n    return bpy.context.object\n", tmp_path)
    assert ok.checked and not ok.errors and ok.summary() == "syntax OK"
    bad = _v("blender", "src/model.py", "import bpy\nx = (\n", tmp_path)
    assert bad.checked and bad.n_total == 1 and bad.errors[0].startswith("line ") and "SyntaxError" in bad.errors[0]
    assert bad.summary().startswith("1 lint error(s)")
    # a helper module needs no `import bpy`; a part file does
    assert not _v("blender", "src/parts/_helpers.py", "def f():\n    return 1\n", tmp_path).errors
    nobpy = _v("blender", "src/parts/leg.py", "def build_leg():\n    return None\n", tmp_path)
    assert nobpy.errors and "never imports bpy" in nobpy.errors[0]


def test_first_three_errors_only(tmp_path):
    src = "import bpy\nimport subprocess\nimport socket\nimport pathlib\nimport urllib\nimport requests\n"
    v = _v("blender", "src/model.py", src, tmp_path)
    assert v.n_total >= 4 and len(v.errors) == 3
    assert v.summary().startswith(f"{v.n_total} lint error(s)") and v.summary().count("\n  - ") == 3


def test_cadquery_opengl_urdf(tmp_path):
    assert "SyntaxError" in _v("cadquery", "src/model.py", "import cadquery as cq\nresult = cq.Workplane(\n", tmp_path).errors[0]
    gl = _v("opengl_python", "src/program.py", "def setup(ctx, width, height):\n    return {}\n", tmp_path)
    assert gl.checked and any("render" in e for e in gl.errors)          # missing render()
    urdf = _v("urdf_blender", "src/robot.urdf", "<robot name='r'><link name='base'/><joint", tmp_path)
    assert urdf.checked and urdf.errors
    model = _v("urdf_blender", "src/model.py", "import bpy\nx = 1\n", tmp_path)
    assert model.checked and not model.errors


def test_glsl_static_rules(tmp_path):
    good = "void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(1.0); }\n"
    assert _v("glsl_shader", "src/shader.frag", good, tmp_path).summary() == "syntax OK"
    bad = _v("glsl_shader", "src/shader.frag", "#version 330 core\n" + good, tmp_path)
    assert bad.errors and "line 1" in bad.errors[0] and "#version" in bad.errors[0]


@pytest.mark.node
def test_threejs_node_check(tmp_path):
    ok = _v("threejs", "src/parts/seat.js", "export function buildSeat(THREE) { return new THREE.Group(); }\n", tmp_path)
    assert ok.checked and not ok.errors
    bad = _v("threejs", "src/parts/seat.js", "export function buildSeat(THREE) {\n  return new THREE.Group(;\n}\n", tmp_path)
    assert bad.checked and bad.errors and "line 2" in bad.errors[0]


def test_unchecked_cases(tmp_path):
    assert not _v("blender", "public/x.py", "x = (\n", tmp_path).checked          # outside src/
    assert not _v("blender", "src/notes.md", "# hi", tmp_path).checked            # no per-file check
    assert not _v("opengl_python", "src/pass.glsl", "#version 330", tmp_path).checked  # no cheap GLSL compile
    assert not _v("not-a-language", "src/model.py", "x = (", tmp_path).checked
    assert FileVerdict(checked=False).summary() == ""
