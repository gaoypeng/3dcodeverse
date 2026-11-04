"""Shared AST-lint primitives: base-forbidden modules are errors in every language lint."""

from __future__ import annotations

import codeverse3d.languages.blender as blender_lint
import codeverse3d.languages.cadquery as cadquery_lint
import codeverse3d.languages.opengl_python as opengl_lint
import codeverse3d.languages.urdf as urdf_lint
from codeverse3d.contracts.artifacts import Severity


def test_base_modules_error_in_every_language_lint() -> None:
    src_bl = "import bpy\nimport webbrowser\nobj = bpy.context.object\n"
    rep = blender_lint.lint_blender_source(src_bl, expect_names=False)
    assert any(f.severity == Severity.ERROR and "webbrowser" in f.message for f in rep.findings)

    src_cq = "import cadquery as cq\nimport webbrowser\nresult = cq.Workplane('XY').box(1, 1, 1)\n"
    rep = cadquery_lint.lint_cadquery_source(src_cq)
    assert any(f.severity == Severity.ERROR and "webbrowser" in f.message for f in rep.findings)

    findings = opengl_lint.lint_source(
        "import moderngl\nimport webbrowser\n"
        "def setup(ctx, width, height):\n    return None\n"
        "def render(ctx, state, t, frame, fbo):\n    pass\n"
    )
    assert any(f.severity == Severity.ERROR and "webbrowser" in f.message for f in findings)

    findings = urdf_lint.lint_model_text("import bpy\nimport webbrowser\nimport os\n", [])
    assert any(f.severity == Severity.ERROR and "webbrowser" in f.message for f in findings)
    assert not [f for f in findings if "`os`" in f.message]     # the blender language's policy: os/sys allowed


