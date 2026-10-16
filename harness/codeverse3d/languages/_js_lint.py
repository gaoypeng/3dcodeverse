"""Shared node-side lint primitives for the JS authoring languages (threejs, scene_threejs).

Replaces two copies of the same algorithm: ``threejs/lint.py:check_syntax`` +
``_lint_imports`` and ``scene_threejs/lint.py:_node_check`` + ``_check_imports``.
Both matched static, re-export and dynamic import specifiers with near-identical
regexes and applied the same allowlist (``three``, ``three/addons/*``,
``three/examples/jsm/*``, relative files under ``src/``).  They differed only in
message text (owned by the caller via ``make_finding``, as in :mod:`_ast_lint`) and
in two defects this module fixes: threejs's fallback message was a ``list``
(``splitlines()[-1:]``), and scene's escape check was a ``str.startswith`` prefix
test that let ``src2/`` pass.  Escape is decided before existence so a
symlink/``..`` hop out of ``src/`` is reported as an escape, never as "missing".

Syntax is ``runtime_js/lib/syntax_check.mjs`` (export_glb's checker too) in ONE node
process per lint, skipping files whose bytes already parsed — it was one ``node --check``
per file: 51 on a scene, 44 of them unchanged ``src/lib`` modules, ≈ 2.5 s a build call.

:func:`check_named_imports` is the second half of the import policy: ``check_imports`` only
asks whether the TARGET exists, so ``import { concrete } from '../lib/materials.js'`` passed
the lint and died at module link ("does not provide an export named 'concrete'") — the
argon loop's iteration 0 lost two whole zones to it at assembly (run
``it00_scene_threejs_r1_18``, scored 0.47).  Exports are read from the target's source
text (:func:`module_exports`), once per file per lint through the caller's cache.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from codeverse3d.contracts.artifacts import GateFinding
from codeverse3d.languages._common import line_of
from codeverse3d.proc import sha256_file
from codeverse3d.spatial.node import NodeError, run_node, runtime_js_dir

# threejs's regexes (the superset: side-effect ``import './x.js'``, ``export * from``,
# ``export * as ns from`` and multi-line ``import {\n a,\n b\n} from`` all match)
_IMPORT_RE = re.compile(
    r"""(?:^|\n)\s*(?:import\s+(?:[^'";]*?\s+from\s+)?|export\s+(?:\*(?:\s+as\s+\w+)?|\{[^}]*\})\s+from\s+)['"]([^'"]+)['"]"""
)
_DYN_IMPORT_RE = re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_URL_RE = re.compile(r"https?://")
# strings and comments in ONE alternation so a '//' inside a string is not a comment
_TOKEN_RE = re.compile(
    r"""'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`|//[^\n]*|/\*.*?\*/""", re.S
)


def strip_js(src: str, *, strings: bool = True) -> str:
    """Blank out comments (and, with ``strings``, string contents), keeping newlines so line
    numbers survive: the forbidden-token lints read code, never a comment quoting the contract.
    scene_threejs keeps strings (its network rule matches URL literals)."""
    def repl(m: re.Match[str]) -> str:
        tok = m.group(0)
        if not tok.startswith("/"):
            return '""' + "\n" * tok.count("\n") if strings else tok
        return "\n" * tok.count("\n")
    return _TOKEN_RE.sub(repl, src)


@dataclass(frozen=True)
class SyntaxProblem:
    """What ``node --check`` reported: ``line`` is None when it could not be parsed
    (or node could not run), ``stderr_tail`` is the last 600 chars of stderr."""

    line: int | None
    message: str
    stderr_tail: str


#: sha256 of file contents that parsed — library modules are byte-identical build after build
_PARSED: set[str] = set()


def node_check_syntax(paths: Sequence[Path]) -> dict[Path, SyntaxProblem]:
    """``{path: problem}`` for every file of ``paths`` that does not parse as an ES
    module; one ``node`` for the lot, and none at all when every file's content
    already parsed in this process."""
    digests: dict[Path, str] = {}
    out: dict[Path, SyntaxProblem] = {}
    for p in paths:
        try:
            digests[p] = sha256_file(p)
        except OSError as e:
            out[p] = SyntaxProblem(None, f"cannot read the file: {e}", "")
    todo = [p for p, d in digests.items() if d not in _PARSED]
    if not todo:
        return out
    try:
        res = run_node(runtime_js_dir() / "lib" / "syntax_check.mjs", [str(p) for p in todo],
                       node_args=["--experimental-vm-modules"], timeout_s=60, check=False)
        bad = {row["file"]: row for row in (res.last_json or {})["bad"]}
    except (NodeError, KeyError, TypeError) as e:
        return out | {p: SyntaxProblem(None, f"the node syntax check could not run: {e}", "") for p in todo}
    for p in todo:
        row = bad.get(str(p))
        if row is None:
            _PARSED.add(digests[p])
        else:
            out[p] = SyntaxProblem(row.get("line"), str(row.get("message") or "syntax error"), str(row.get("stderr", "")))
    return out


def js_sources(src: Path, *, dotfiles: bool = False, mjs: bool = False) -> list[Path]:
    """The ``*.js`` under ``src`` (never ``node_modules``); scene also lints dot-files and ``*.mjs``."""
    if not src.is_dir():
        return []
    js = sorted(p for p in src.rglob("*.js") if "node_modules" not in p.parts and (dotfiles or not p.name.startswith(".")))
    return js + (sorted(src.rglob("*.mjs")) if mjs else [])


def import_specs(src: str) -> list[tuple[str, int]]:
    """``(specifier, 1-based line)`` for every static, re-export and dynamic import in ``src``."""
    out = [(m.group(1), m.start(1)) for m in _IMPORT_RE.finditer(src)]
    out += [(m.group(1), m.start(1)) for m in _DYN_IMPORT_RE.finditer(src)]
    return [(spec, line_of(src, pos)) for spec, pos in out]


class ImportKind(StrEnum):
    ALLOWED = "allowed"    # 'three', 'three/addons/*', 'three/examples/jsm/*', or an existing file under src/
    ESCAPES = "escapes"    # relative path resolves outside src/
    MISSING = "missing"    # relative path stays under src/ but is not a file
    ABSOLUTE = "absolute"  # '/x.js'
    URL = "url"            # http(s)://...
    PACKAGE = "package"    # any other bare specifier (npm package)


@dataclass(frozen=True)
class ImportVerdict:
    kind: ImportKind
    #: resolved relative target (set for the three relative kinds), else None
    target: Path | None = None


def classify_import(spec: str, path: Path, src_root: Path) -> ImportVerdict:
    """Apply the JS import policy to one specifier found in ``path`` (a file under ``src_root``)."""
    if spec == "three" or spec.startswith("three/addons/") or spec.startswith("three/examples/jsm/"):
        return ImportVerdict(ImportKind.ALLOWED)
    if spec.startswith("./") or spec.startswith("../"):
        target = (path.parent / spec).resolve()
        if not target.is_relative_to(src_root.resolve()):
            return ImportVerdict(ImportKind.ESCAPES, target)
        if not target.is_file():
            return ImportVerdict(ImportKind.MISSING, target)
        return ImportVerdict(ImportKind.ALLOWED, target)
    if spec.startswith("/"):
        return ImportVerdict(ImportKind.ABSOLUTE)
    if _URL_RE.match(spec):
        return ImportVerdict(ImportKind.URL)
    return ImportVerdict(ImportKind.PACKAGE)


def check_imports(
    src: str,
    path: Path,
    src_root: Path,
    *,
    make_finding: Callable[[ImportVerdict, str, int], GateFinding],
) -> tuple[list[GateFinding], set[Path]]:
    """Classify every import in ``src``; return the findings for the non-allowed ones
    and the set of resolved relative files it imports.

    ``make_finding(verdict, spec, line)`` builds the finding — the caller owns
    message, severity and hint text.
    """
    findings: list[GateFinding] = []
    targets: set[Path] = set()
    for spec, line in import_specs(src):
        verdict = classify_import(spec, path, src_root)
        if verdict.kind is ImportKind.ALLOWED:
            if verdict.target is not None:
                targets.add(verdict.target)
        else:
            findings.append(make_finding(verdict, spec, line))
    return findings, targets


# ===================================================================== named imports
_IDENT = r"[A-Za-z_$][\w$]*"
_IDENT_RE = re.compile(rf"^{_IDENT}$")
# ``import { a, b as c } from '…'``, ``import x, { a } from '…'``, braces over several lines;
# ``import * as m`` and a bare default import have no brace and never match.  Group 1 is the
# ``import`` keyword (the finding's line), 2 the brace body, 3 the specifier.
_NAMED_IMPORT_RE = re.compile(
    rf"""(?:^|\n)\s*(import)(?:\s+{_IDENT}\s*,)?\s*\{{([^}}]*)\}}\s*from\s*['"]([^'"]+)['"]"""
)
# ``export [async] function[*] NAME`` / ``export class|const|let|var NAME``
_EXPORT_DECL_RE = re.compile(
    rf"\bexport\s+(?:async\s+)?(?:function\s*\*?\s*|(?:class|const|let|var)\s+)({_IDENT})"
)
# ``export { a, b as c }`` and ``export { a as b } from '…'``: the exported name is the LAST token
_EXPORT_LIST_RE = re.compile(r"\bexport\s*\{([^}]*)\}")
# ``export * as ns from '…'`` exports exactly ``ns``; a bare ``export * from '…'`` exports another
# module's surface (group 1 empty)
_EXPORT_STAR_RE = re.compile(rf"\bexport\s*\*\s*(?:as\s+({_IDENT})\s+)?from\b")
_EXPORT_DEFAULT_RE = re.compile(r"\bexport\s+default\b")
# ``export const { a, b } = …`` / ``export const [x] = …``: names inside a pattern
_EXPORT_PATTERN_RE = re.compile(r"\bexport\s+(?:const|let|var)\s*[\[{]")

#: ``{resolved target: its exports}`` — one lint reads each ``src/lib`` module once, not once
#: per importer (a scene has ~50 files importing the same dozen modules)
ExportsCache = dict[Path, frozenset[str] | None]


@dataclass(frozen=True)
class NamedImport:
    """One static ``import { a, b as c } from '<spec>'``: ``names`` are the IMPORTED names
    (``a`` and ``b`` — what the target module must export, not the local aliases)."""

    spec: str
    names: tuple[str, ...]
    line: int


def named_import_specs(src: str) -> list[NamedImport]:
    """Every static named import of ``src`` (comments blanked first, so a commented-out
    import is not parsed); ``import * as m`` and default-only imports yield nothing."""
    code = strip_js(src, strings=False)   # the specifier is a string literal: keep strings
    out: list[NamedImport] = []
    for m in _NAMED_IMPORT_RE.finditer(code):
        names = tuple(n for n in (item.split()[0] for item in m.group(2).split(",") if item.strip())
                      if _IDENT_RE.match(n))
        if names:
            out.append(NamedImport(m.group(3), names, line_of(code, m.start(1))))
    return out


def module_exports(src: str) -> frozenset[str] | None:
    """The names an ES module's source text exports, or ``None`` when its surface cannot be
    enumerated from the text alone: a bare ``export * from '…'`` (another module's surface —
    not resolved transitively, so nothing is reported against such a module) or a
    destructuring ``export const { a } = …`` pattern.  ``export default`` exports ``default``."""
    code = strip_js(src)
    if _EXPORT_PATTERN_RE.search(code):
        return None
    names = {m.group(1) for m in _EXPORT_DECL_RE.finditer(code)}
    for m in _EXPORT_LIST_RE.finditer(code):
        names |= {last for last in (item.split()[-1] for item in m.group(1).split(",") if item.strip())
                  if _IDENT_RE.match(last)}
    for m in _EXPORT_STAR_RE.finditer(code):
        if not m.group(1):
            return None
        names.add(m.group(1))
    if _EXPORT_DEFAULT_RE.search(code):
        names.add("default")
    return frozenset(names)


_EXPORT_FN_RE = re.compile(r"\bexport\s+(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(")
# ``export const name = (params) => …`` / ``= async (…) =>`` / ``= function (…) {`` — the ``(`` opens the
# parameter list only when ``=>`` follows its close (or the value is a ``function``); checked below
_EXPORT_CONST_FN_RE = re.compile(r"\bexport\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?"
                                 r"(function\b\s*\*?\s*[\w$]*\s*)?\(")
# ``export const name = x => …``: one bare parameter
_EXPORT_CONST_ARROW1_RE = re.compile(r"\bexport\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?"
                                     r"([A-Za-z_$][\w$]*)\s*=>")
#: one signature line's ceiling: a destructured options object can run to a screenful
SIGNATURE_CHARS = 240


def export_signatures(src: str) -> list[str]:
    """Every name the module exports, an exported function — a declaration, or a ``const`` bound to
    an arrow / function expression — as ``name(params)`` (its parameter list as written, whitespace
    collapsed, capped at :data:`SIGNATURE_CHARS`), sorted; ``[]`` when the surface cannot be
    enumerated (:func:`module_exports`).  Facts read off the text —
    the index a single-shot writer gets of a library it cannot open (D112)."""
    names = module_exports(src)
    if not names:
        return []
    code = strip_js(src, strings=False)
    sigs: dict[str, str] = {}

    def _add(name: str, params: str) -> None:
        sig = f"{name}({' '.join(params.split())})"
        sigs[name] = sig if len(sig) <= SIGNATURE_CHARS else sig[:SIGNATURE_CHARS - 2] + "…)"

    def _close(start: int) -> int:
        """The index just past the ``)`` matching the ``(`` before ``start``."""
        depth, i = 1, start
        while i < len(code) and depth:
            depth += {"(": 1, ")": -1}.get(code[i], 0)
            i += 1
        return i

    for m in _EXPORT_FN_RE.finditer(code):
        _add(m.group(1), code[m.end():_close(m.end()) - 1])
    for m in _EXPORT_CONST_FN_RE.finditer(code):
        end = _close(m.end())
        if m.group(2) or code[end:].lstrip().startswith("=>"):   # a function, not a parenthesised value
            _add(m.group(1), code[m.end():end - 1])
    for m in _EXPORT_CONST_ARROW1_RE.finditer(code):
        _add(m.group(1), m.group(2))
    return [sigs.get(n, n) for n in sorted(names - {"default"})]


def exported_names(path: Path) -> frozenset[str] | None:
    """:func:`module_exports` of the file at ``path`` (``None`` when it cannot be read)."""
    try:
        return module_exports(path.read_text(errors="replace"))
    except OSError:
        return None


def describe_exports(names: frozenset[str], *, limit: int = 12) -> str:
    """``it exports: a, b, …`` (sorted, at most ``limit`` named) or ``it exports nothing``."""
    if not names:
        return "it exports nothing"
    shown = sorted(names)[:limit]
    return "it exports: " + ", ".join(shown) + (", …" if len(names) > limit else "")


def check_named_imports(
    src: str,
    path: Path,
    src_root: Path,
    *,
    make_finding: Callable[[str, str, int, frozenset[str]], GateFinding],
    exports: ExportsCache | None = None,
) -> list[GateFinding]:
    """A finding per imported name that its target module does not export, for every named
    import of ``src`` whose specifier resolves to an existing ``.js`` / ``.mjs`` file under
    ``src_root`` (``'three'`` and the non-allowed kinds are :func:`check_imports`'s business).

    ``make_finding(spec, name, line, exports)`` builds the finding — the caller owns message,
    severity and hint; ``exports`` is the target's surface, for an "it exports: …" listing.
    Pass one ``exports`` cache for the whole lint so each target is read once.
    """
    cache: ExportsCache = {} if exports is None else exports
    findings: list[GateFinding] = []
    for imp in named_import_specs(src):
        verdict = classify_import(imp.spec, path, src_root)
        target = verdict.target
        if verdict.kind is not ImportKind.ALLOWED or target is None or target.suffix not in (".js", ".mjs"):
            continue
        if target not in cache:
            cache[target] = exported_names(target)
        surface = cache[target]
        if surface is None:
            continue
        findings.extend(make_finding(imp.spec, name, imp.line, surface) for name in imp.names if name not in surface)
    return findings
