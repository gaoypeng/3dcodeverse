"""One file, one cheap verdict — what ``write_file`` / ``edit_file`` hand back so the agent
never has to ``read_file`` its own output (docs/COST.md §29: ~35 read_file turns per run,
each a 4 s round trip carrying the whole context, to look at a file it wrote a turn ago).

This is deliberately the language's OWN per-file lint, not a new rule set: the same
``lint_*_source`` the workspace lint calls per file (blender / cadquery / opengl_python /
urdf), ``node --check`` for JS (threejs / scene_threejs) and the static shader rules for
GLSL.  Only ERRORs are reported — a WARN is what ``build`` shows.  A file type the
language has no cheap check for gets ``checked=False`` and the tool says nothing about
syntax rather than pretending.  Never raises: a lint crash is reported as unchecked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from codeverse.contracts.artifacts import GateFinding, Severity
from codeverse.contracts.common import Language

log = logging.getLogger(__name__)

MAX_ERRORS = 3


@dataclass(frozen=True)
class FileVerdict:
    checked: bool
    errors: list[str] = field(default_factory=list)  # "line 7: SyntaxError: ..." (≤ MAX_ERRORS)
    n_total: int = 0

    def summary(self) -> str:
        """`syntax OK` / `2 lint error(s): ...` / "" when nothing was checked."""
        if not self.checked:
            return ""
        if not self.errors:
            return "syntax OK"
        head = f"{self.n_total} lint error(s) — fix before build:"
        return head + "".join(f"\n  - {e}" for e in self.errors)


def _line_of(f: GateFinding) -> int | None:
    n = f.data.get("line") if isinstance(f.data, dict) else None
    return int(n) if isinstance(n, int) and n > 0 else None


def _errors(findings: list[GateFinding], max_errors: int) -> FileVerdict:
    errs = [f for f in findings if f.severity == Severity.ERROR]
    out = []
    for f in errs[:max_errors]:
        line = _line_of(f)
        msg = f.message
        out.append((f"line {line}: " if line else "") + msg + (f"  (fix: {f.fix_hint})" if f.fix_hint else ""))
    return FileVerdict(checked=True, errors=out, n_total=len(errs))


def _python(language: Language, rel: str, text: str, max_errors: int) -> FileVerdict:
    name = Path(rel).name
    if language is Language.BLENDER:
        from codeverse.languages.blender.lint import lint_blender_source

        # same per-file settings as blender.layout.lint_workspace: helpers (_x.py) need no bpy;
        # PascalCase-name checks are WARNs anyway and never reported here
        rep = lint_blender_source(text, target=rel, expect_names=False, expect_bpy=not name.startswith("_"))
        return _errors(rep.findings, max_errors)
    if language is Language.CADQUERY:
        from codeverse.languages.cadquery.lint import lint_cadquery_source

        return _errors(lint_cadquery_source(text, target=rel).findings, max_errors)
    if language is Language.OPENGL_PYTHON:
        from codeverse.languages.opengl_python.lint import lint_source

        return _errors(lint_source(text), max_errors)
    if language is Language.URDF_BLENDER:
        from codeverse.languages.urdf.lint import lint_model_text

        # link names are the urdf's business; here only syntax + forbidden APIs + `import bpy`
        return _errors(lint_model_text(text, [], label=rel), max_errors)
    return FileVerdict(checked=False)


def _js(path: Path, max_errors: int) -> FileVerdict:
    from codeverse.config import get_settings
    from codeverse.languages._js_lint import node_check_syntax

    problem = node_check_syntax(path, get_settings().binaries.node or "node")
    if problem is None:
        return FileVerdict(checked=True)
    if problem.line is None and "could not run" in problem.message:
        return FileVerdict(checked=False)
    return FileVerdict(checked=True, n_total=1,
                       errors=[(f"line {problem.line}: " if problem.line else "") + problem.message][:max_errors])


def _glsl(rel: str, text: str, path: Path, max_errors: int) -> FileVerdict:
    from codeverse.languages.glsl_shader import lint as gl

    role = {gl.SHADER: "shader", gl.COMMON: "common", gl.BUFFER_A: "buffer_a"}.get(rel, "shader")
    # the harness-owned recipe file beside it reserves its names (redefines_recipe)
    recipes = path.parent / Path(gl.RECIPES).name
    reserved = frozenset(gl.defined_functions(recipes.read_text(errors="replace"))) if recipes.is_file() else frozenset()
    return _errors(gl._check_file(rel, text, role=role, recipe_names=reserved), max_errors)


def _urdf(rel: str, text: str, max_errors: int) -> FileVerdict:
    from codeverse.languages.urdf.lint import lint_urdf_text

    findings, _links = lint_urdf_text(text, label=rel)
    return _errors(findings, max_errors)


def lint_one_file(language: str, rel: str, text: str, path: Path, *, max_errors: int = MAX_ERRORS) -> FileVerdict:
    """Verdict for ONE just-written source file (``rel`` workspace-relative, ``path`` on disk).

    Cheap by construction: an ``ast.parse`` + visitor for python, one ``node --check``
    for JS, regexes for GLSL / URDF.  Files outside ``src/`` and types the language has no
    per-file check for come back ``checked=False``.
    """
    try:
        lang = Language(language)
    except ValueError:
        return FileVerdict(checked=False)
    if not rel.startswith("src/"):
        return FileVerdict(checked=False)
    suffix = Path(rel).suffix
    try:
        if suffix == ".py":
            return _python(lang, rel, text, max_errors)
        if suffix in (".js", ".mjs") and lang in (Language.THREEJS, Language.SCENE_THREEJS):
            return _js(path, max_errors)
        if suffix in (".frag", ".glsl") and lang is Language.GLSL_SHADER:
            return _glsl(rel, text, path, max_errors)
        if suffix == ".urdf" and lang is Language.URDF_BLENDER:
            return _urdf(rel, text, max_errors)
    except Exception as e:  # noqa: BLE001 — a lint crash is not the agent's problem
        log.warning("per-file lint of %s failed: %s: %s", rel, type(e).__name__, e)
    return FileVerdict(checked=False)


__all__ = ["MAX_ERRORS", "FileVerdict", "lint_one_file"]
