"""Shared AST-lint primitives + the per-language forbidden-import supersets (drift guard)."""

from __future__ import annotations

import codeverse3d.languages.blender as blender_lint
import codeverse3d.languages.cadquery as cadquery_lint
import codeverse3d.languages.opengl_python as opengl_lint
import codeverse3d.languages.urdf as urdf_lint
from codeverse3d.contracts.artifacts import Severity
from codeverse3d.languages._ast_lint import BASE_FORBIDDEN_IMPORTS


def test_language_forbidden_sets_are_supersets_of_base() -> None:
    assert BASE_FORBIDDEN_IMPORTS <= blender_lint.FORBIDDEN_IMPORTS
    assert BASE_FORBIDDEN_IMPORTS <= cadquery_lint.FORBIDDEN_IMPORTS
    assert BASE_FORBIDDEN_IMPORTS <= opengl_lint.DANGEROUS_MODULES
    assert BASE_FORBIDDEN_IMPORTS <= urdf_lint.FORBIDDEN_IMPORTS
    assert "pathlib" in blender_lint.FORBIDDEN_IMPORTS
    assert {"os", "sys", "OCP", "build123d"} <= cadquery_lint.FORBIDDEN_IMPORTS
    assert {"os", "sys", "time", "datetime", "io", "tempfile", "signal"} <= opengl_lint.DANGEROUS_MODULES
    # blender's documented allowance: os/sys stay importable (call rules catch abuse)
    assert {"os", "sys"} <= blender_lint.ALLOWED_IMPORTS
    assert not ({"os", "sys"} & (blender_lint.FORBIDDEN_IMPORTS | BASE_FORBIDDEN_IMPORTS))


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
    assert any(f.severity == Severity.WARN and "'os'" in f.message for f in findings)


def test_every_python_lint_survives_a_parser_crash(monkeypatch):
    """A parser crash (SystemError on 3.11, compare_v4_calm) is a lint ERROR, not a dead round."""
    import ast

    from codeverse3d.languages.blender import lint_blender_source
    from codeverse3d.languages.cadquery import lint_cadquery_source
    from codeverse3d.languages.opengl_python import lint_source as lint_gl
    from codeverse3d.languages.urdf import lint_model_text

    real = ast.parse

    def crash(src, *a, **k):
        if "DEEP" in src:
            raise SystemError("AST constructor recursion depth mismatch (before=54, after=62)")
        return real(src, *a, **k)

    monkeypatch.setattr(ast, "parse", crash)
    src = "import bpy\nDEEP = 1\n"
    for name, run in (("blender", lambda: lint_blender_source(src).findings),
                      ("cadquery", lambda: lint_cadquery_source("import cadquery as cq\nDEEP = 1\n").findings),
                      ("opengl", lambda: lint_gl(src)),
                      ("urdf", lambda: lint_model_text(src, ["base"]))):
        findings = run()  # must not raise
        errs = [f for f in findings if f.severity == Severity.ERROR]
        assert errs and "deeper than the parser" in errs[0].message, (name, findings)


def test_real_deeply_nested_source_does_not_escape_the_lint():
    """A 40 000-term binary chain: on 3.11 this is the SystemError, on 3.12+ a RecursionError,
    on some builds a plain SyntaxError — whichever, the lint returns a report."""
    from codeverse3d.languages.blender import lint_blender_source

    deep = "import bpy\nx = " + " + ".join(["1"] * 40_000) + "\n"
    rep = lint_blender_source(deep)
    assert isinstance(rep.passed, bool)
