"""Shared AST-lint primitives + the per-language forbidden-import supersets (drift guard)."""

from __future__ import annotations

import ast

from codeverse.contracts.artifacts import GateFinding, Severity
from codeverse.languages._ast_lint import BASE_FORBIDDEN_IMPORTS, check_imports, dotted
from codeverse.languages.blender import lint as blender_lint
from codeverse.languages.cadquery import lint as cadquery_lint
from codeverse.languages.opengl_python import lint as opengl_lint

# ----------------------------------------------------------------- superset (drift fails here)


def test_base_forbidden_imports_shape() -> None:
    assert len(BASE_FORBIDDEN_IMPORTS) == 14
    # blender legitimately allows os/sys — the base set must never grow them
    assert "os" not in BASE_FORBIDDEN_IMPORTS and "sys" not in BASE_FORBIDDEN_IMPORTS
    assert {"subprocess", "webbrowser", "ftplib", "smtplib", "importlib"} <= BASE_FORBIDDEN_IMPORTS


def test_language_forbidden_sets_are_supersets_of_base() -> None:
    assert BASE_FORBIDDEN_IMPORTS <= blender_lint.FORBIDDEN_IMPORTS
    assert BASE_FORBIDDEN_IMPORTS <= cadquery_lint.FORBIDDEN_IMPORTS
    assert BASE_FORBIDDEN_IMPORTS <= opengl_lint.DANGEROUS_MODULES


def test_language_specific_extras_survive() -> None:
    assert "pathlib" in blender_lint.FORBIDDEN_IMPORTS
    assert {"os", "sys", "OCP", "build123d"} <= cadquery_lint.FORBIDDEN_IMPORTS
    assert {"os", "sys", "time", "datetime", "io", "tempfile", "signal"} <= opengl_lint.DANGEROUS_MODULES
    # blender's documented allowance: os/sys stay importable (call rules catch abuse)
    assert {"os", "sys"} <= blender_lint.ALLOWED_IMPORTS
    assert not ({"os", "sys"} & blender_lint.FORBIDDEN_IMPORTS)


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


# ----------------------------------------------------------------- check_imports / dotted


def _mk(kind: str, mod: str, line: int) -> GateFinding:
    return GateFinding(gate="lint:test", severity=Severity.ERROR if kind == "forbidden" else Severity.WARN,
                       message=f"{kind}:{mod}", data={"line": line})


def test_check_imports_routes_forbidden_unexpected_allowed() -> None:
    out = check_imports({"subprocess": 1, "mystery": 2, "math": 3},
                        forbidden=frozenset({"subprocess"}), allowed=frozenset({"math"}), make_finding=_mk)
    assert [(f.message, f.severity) for f in out] == [
        ("forbidden:subprocess", Severity.ERROR), ("unexpected:mystery", Severity.WARN)]
    assert [f.data["line"] for f in out] == [1, 2]


def test_dotted_chains() -> None:
    def expr(src: str) -> ast.AST:
        return ast.parse(src, mode="eval").body

    assert dotted(expr("bpy.ops.render.render")) == "bpy.ops.render.render"
    assert dotted(expr("name")) == "name"
    assert dotted(ast.parse("wp.box(1).rotate", mode="eval").body) == "<expr>.rotate"
    assert dotted(expr("(a + b)")) == ""
    # the same function is what the language lints re-export
    assert blender_lint.dotted is dotted and cadquery_lint.dotted is dotted
