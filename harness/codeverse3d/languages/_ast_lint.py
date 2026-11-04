"""Language-neutral AST-lint primitives for the python languages: ``dotted``, ``ImportCollector``,
``BASE_FORBIDDEN_IMPORTS`` and ``check_imports``."""

from __future__ import annotations

import ast
from collections.abc import Callable, Collection, Mapping

from codeverse3d.contracts.artifacts import GateFinding

#: modules no agent script may import in any python language; languages add their own supersets
BASE_FORBIDDEN_IMPORTS: frozenset[str] = frozenset({
    "subprocess", "urllib", "requests", "socket", "http", "shutil", "ctypes", "pickle",
    "multiprocessing", "threading", "webbrowser", "ftplib", "smtplib", "importlib",
})


class ImportCollector(ast.NodeVisitor):

    def __init__(self) -> None:
        self.imports: dict[str, int] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self.imports.setdefault(a.name.split(".")[0], node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.setdefault((node.module or "").split(".")[0], node.lineno)


def safe_parse(source: str, filename: str = "<src>") -> tuple[ast.AST | None, BaseException | None]:
    """``ast.parse`` that never raises: ``(tree, None)`` or ``(None, exception)``."""
    try:
        return ast.parse(source, filename=filename), None
    except SyntaxError as e:
        return None, e
    except (RecursionError, SystemError, MemoryError, ValueError) as e:  # noqa: PERF203 — one call
        return None, e


def describe_parse_failure(exc: BaseException) -> tuple[str, str, int | None]:
    """(message, fix hint, line) for a :func:`safe_parse` failure, in the lints' wording."""
    if isinstance(exc, SyntaxError):
        return (f"SyntaxError: {exc.msg}",
                f"fix the syntax near line {exc.lineno}: {(exc.text or '').strip()!r}", exc.lineno)
    if isinstance(exc, (RecursionError, SystemError, MemoryError)):
        return (f"{type(exc).__name__}: the file nests expressions or literals deeper than the parser can handle ({exc})",
                "flatten deeply nested expressions and literals — build long vertex / face lists in a loop or "
                "from a flat list, never as one giant nested literal", None)
    return f"{type(exc).__name__}: {exc}", "remove null bytes / non-source content from the file", None


def dotted(node: ast.AST) -> str:
    """Dotted name of an Attribute/Name chain (e.g. ``pkg.mod.attr.call``); '' otherwise."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    if parts:  # method on an expression result, e.g. ``wp.box(1).rotate`` → ``<expr>.rotate``
        return "<expr>." + ".".join(reversed(parts))
    return ""


def check_imports(
    imports: Mapping[str, int],
    *,
    forbidden: Collection[str],
    allowed: Collection[str],
    make_finding: Callable[[str, str, int], GateFinding],
) -> list[GateFinding]:
    """Route each ``{top-level module: line}`` through the import policy; ``make_finding`` builds the finding."""
    out: list[GateFinding] = []
    for mod, line in imports.items():
        if not mod:
# `from . import x` has no top-level module (node.module is None); skip it.
            continue
        if mod in forbidden:
            out.append(make_finding("forbidden", mod, line))
        elif mod not in allowed:
            out.append(make_finding("unexpected", mod, line))
    return out
