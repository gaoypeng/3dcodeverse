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
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from codeverse3d.contracts.artifacts import GateFinding
from codeverse3d.languages._common import line_of
from codeverse3d.spatial.node import NodeError, run_node, runtime_js_dir

# threejs's regexes (the superset: side-effect ``import './x.js'``, ``export * from``,
# ``export * as ns from`` and multi-line ``import {\n a,\n b\n} from`` all match)
_IMPORT_RE = re.compile(
    r"""(?:^|\n)\s*(?:import\s+(?:[^'";]*?\s+from\s+)?|export\s+(?:\*(?:\s+as\s+\w+)?|\{[^}]*\})\s+from\s+)['"]([^'"]+)['"]"""
)
_DYN_IMPORT_RE = re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_URL_RE = re.compile(r"https?://")


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
            digests[p] = hashlib.sha256(p.read_bytes()).hexdigest()
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
