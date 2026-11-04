"""Shared node-side lint primitives for the JS authoring languages (threejs, scene_threejs):
syntax check, import policy, named-import and export-surface checks.  The caller owns finding text
via ``make_finding``."""

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

_IMPORT_RE = re.compile(
    r"""(?:^|\n)\s*(?:import\s+(?:[^'";]*?\s+from\s+)?|export\s+(?:\*(?:\s+as\s+\w+)?|\{[^}]*\})\s+from\s+)['"]([^'"]+)['"]"""
)
_DYN_IMPORT_RE = re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_URL_RE = re.compile(r"https?://")
_TOKEN_RE = re.compile(
    r"""'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`|//[^\n]*|/\*.*?\*/""", re.S
)


def strip_js(src: str, *, strings: bool = True) -> str:
    """Blank out comments (and, with ``strings``, string contents), keeping newlines so line numbers survive."""
    def repl(m: re.Match[str]) -> str:
        tok = m.group(0)
        if not tok.startswith("/"):
            return '""' + "\n" * tok.count("\n") if strings else tok
        return "\n" * tok.count("\n")
    return _TOKEN_RE.sub(repl, src)


@dataclass(frozen=True)
class SyntaxProblem:
    """What ``node --check`` reported: ``line`` is None when unparseable; ``stderr_tail`` is the last 600 chars."""

    line: int | None
    message: str
    stderr_tail: str


_PARSED: set[str] = set()


def node_check_syntax(paths: Sequence[Path]) -> dict[Path, SyntaxProblem]:
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
    if not src.is_dir():
        return []
    js = sorted(p for p in src.rglob("*.js") if "node_modules" not in p.parts and (dotfiles or not p.name.startswith(".")))
    return js + (sorted(src.rglob("*.mjs")) if mjs else [])


def import_specs(src: str) -> list[tuple[str, int]]:
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
    target: Path | None = None


def classify_import(spec: str, path: Path, src_root: Path) -> ImportVerdict:
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
    """Classify every import in ``src``; return the findings for non-allowed ones and the resolved relative files."""
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

ExportsCache = dict[Path, frozenset[str] | None]


@dataclass(frozen=True)
class NamedImport:
    """One static ``import { a, b as c } from '<spec>'``; ``names`` are the imported names, not aliases."""

    spec: str
    names: tuple[str, ...]
    line: int


def named_import_specs(src: str) -> list[NamedImport]:
    code = strip_js(src, strings=False)   # the specifier is a string literal: keep strings
    out: list[NamedImport] = []
    for m in _NAMED_IMPORT_RE.finditer(code):
        names = tuple(n for n in (item.split()[0] for item in m.group(2).split(",") if item.strip())
                      if _IDENT_RE.match(n))
        if names:
            out.append(NamedImport(m.group(3), names, line_of(code, m.start(1))))
    return out


def module_exports(src: str) -> frozenset[str] | None:
    """Names an ES module's source exports, or ``None`` when not enumerable from text (bare ``export * from``, destructured exports)."""
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
    """Every exported name, functions as ``name(params)`` (capped at :data:`SIGNATURE_CHARS`), sorted; ``[]`` when not enumerable."""
    names = module_exports(src)
    if not names:
        return []
    code = strip_js(src, strings=False)
    sigs: dict[str, str] = {}

    def _add(name: str, params: str) -> None:
        sig = f"{name}({' '.join(params.split())})"
        sigs[name] = sig if len(sig) <= SIGNATURE_CHARS else sig[:SIGNATURE_CHARS - 2] + "…)"

    def _close(start: int) -> int:
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
    try:
        return module_exports(path.read_text(errors="replace"))
    except OSError:
        return None


def describe_exports(names: frozenset[str], *, limit: int = 12) -> str:
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
    """A finding per imported name its target module does not export, for relative targets under ``src_root``.

    Pass one ``exports`` cache for the whole lint."""
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
