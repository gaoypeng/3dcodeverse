"""Shared AST-lint primitives + the per-language forbidden-import supersets (drift guard)."""

from __future__ import annotations

import ast

import codeverse.languages.blender as blender_lint
import codeverse.languages.cadquery as cadquery_lint
import codeverse.languages.opengl_python as opengl_lint
from codeverse.contracts.artifacts import GateFinding, Severity
from codeverse.languages._ast_lint import BASE_FORBIDDEN_IMPORTS, check_imports, dotted

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


# ----------------------------------------------------------------------------- safe_parse (2026-08-25)
def test_safe_parse_turns_parser_crashes_into_findings(monkeypatch):
    """compare_v4_calm: a generated bpy file made CPython 3.11's ``ast.parse`` raise
    ``SystemError: AST constructor recursion depth mismatch`` and the whole harness round
    died in ``build_once``.  Every python lint now goes through ``safe_parse`` and reports
    a lint ERROR the agent can act on instead."""
    import ast

    from codeverse.languages._ast_lint import describe_parse_failure, safe_parse

    tree, exc = safe_parse("x = 1\n")
    assert isinstance(tree, ast.Module) and exc is None
    tree, exc = safe_parse("x = (\n", "src/model.py")
    assert tree is None and isinstance(exc, SyntaxError)
    msg, hint, line = describe_parse_failure(exc)
    assert msg.startswith("SyntaxError:") and "fix the syntax near line" in hint

    def boom(*a, **k):
        raise SystemError("AST constructor recursion depth mismatch (before=54, after=62)")

    monkeypatch.setattr(ast, "parse", boom)
    tree, exc = safe_parse("x = 1\n")
    assert tree is None and isinstance(exc, SystemError)
    msg, hint, line = describe_parse_failure(exc)
    assert "SystemError" in msg and "deeper than the parser" in msg and "flatten" in hint and line is None
    tree, exc = safe_parse("x = 1\n")  # RecursionError / MemoryError take the same path
    monkeypatch.setattr(ast, "parse", lambda *a, **k: (_ for _ in ()).throw(RecursionError("maximum recursion depth exceeded")))
    tree, exc = safe_parse("x = 1\n")
    assert tree is None and isinstance(exc, RecursionError)


def test_every_python_lint_survives_a_parser_crash(monkeypatch):
    import ast

    from codeverse.contracts.artifacts import Severity
    from codeverse.languages.blender import lint_blender_source
    from codeverse.languages.cadquery import lint_cadquery_source
    from codeverse.languages.opengl_python import lint_source as lint_gl
    from codeverse.languages.urdf import lint_model_text

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
        assert errs and "SystemError" in errs[0].message, (name, findings)


def test_real_deeply_nested_source_does_not_escape_the_lint():
    """A 40 000-term binary chain: on 3.11 this is the SystemError, on 3.12+ a RecursionError,
    on some builds a plain SyntaxError — whichever, the lint returns a report."""
    from codeverse.languages.blender import lint_blender_source

    deep = "import bpy\nx = " + " + ".join(["1"] * 40_000) + "\n"
    rep = lint_blender_source(deep)
    assert isinstance(rep.passed, bool)


def test_the_runtime_registry_covers_every_language():
    """get_runtime was a 7-branch if-chain whose last arm was an unreachable
    fall-through; as a table, a missing row is a KeyError at call time instead.  This
    is the drift guard that makes the table safe."""
    from codeverse.contracts.common import Language
    from codeverse.languages.base import _RUNTIMES, get_runtime

    assert set(_RUNTIMES) == set(Language), "every Language needs a runtime row"
    for lang in Language:
        assert get_runtime(lang).language is lang
