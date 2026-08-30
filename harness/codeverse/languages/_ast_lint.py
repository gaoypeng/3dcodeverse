"""Shared AST-lint primitives for the python authoring languages (blender, cadquery, opengl_python).

Each language keeps its own collector and rules — only the language-neutral
pieces live here: :func:`dotted` (dotted-name of an Attribute/Name chain), the
:data:`BASE_FORBIDDEN_IMPORTS` floor every language's forbidden set must
include (a drift test pins the superset), and :func:`check_imports` (the
forbidden → ERROR / not-allowed → WARN routing, with the finding text owned by
the caller via ``make_finding``).
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Collection, Mapping

from codeverse.contracts.artifacts import GateFinding

#: modules no agent script may import in ANY python language (subprocesses,
#: network, file-system side effects, dynamic import).  Languages compose their
#: supersets: blender adds ``pathlib`` (but allows ``os``/``sys`` — headless bpy
#: scripts legitimately touch them and the sandbox confines the damage),
#: cadquery adds ``os``/``sys``/``pathlib`` + the OCC/CQ tooling packages,
#: opengl_python adds ``os``/``sys`` + clock/tempfile/io modules (determinism).
BASE_FORBIDDEN_IMPORTS: frozenset[str] = frozenset({
    "subprocess", "urllib", "requests", "socket", "http", "shutil", "ctypes", "pickle",
    "multiprocessing", "threading", "webbrowser", "ftplib", "smtplib", "importlib",
})


def safe_parse(source: str, filename: str = "<src>") -> tuple[ast.AST | None, BaseException | None]:
    """``ast.parse`` that never raises: ``(tree, None)`` or ``(None, exception)``.

    Besides ``SyntaxError``, CPython raises ``RecursionError`` (3.12+), ``SystemError``
    ("AST constructor recursion depth mismatch", 3.11) or ``MemoryError`` on a source
    whose expressions / literals nest deeper than the parser's C stack, and
    ``ValueError`` on null bytes.  A generated file can do any of these; a lint
    that lets them escape crashes the whole run (compare_v4_calm 2026-08-25:
    ``veh_easy_toy_car``'s round died in ``build_once`` instead of the agent being
    told to flatten its literal).
    """
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
    """Route each ``{top-level module: line}`` through the language's import policy.

    ``make_finding(kind, module, line)`` builds the finding — ``kind`` is
    ``"forbidden"`` (module in ``forbidden``) or ``"unexpected"`` (module in
    neither set); the caller owns message, severity and hint text.
    """
    out: list[GateFinding] = []
    for mod, line in imports.items():
        if not mod:
            # `from . import helpers` has no top-level module to police: ast gives
            # `node.module is None` and every visitor records "".  Reporting it produced
            # "import of '' is unexpected" — a finding the agent cannot act on — in all
            # three languages that share this helper (2026-08-30).
            continue
        if mod in forbidden:
            out.append(make_finding("forbidden", mod, line))
        elif mod not in allowed:
            out.append(make_finding("unexpected", mod, line))
    return out
