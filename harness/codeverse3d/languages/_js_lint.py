"""Shared node-side lint primitives for the JS authoring languages (threejs, scene_threejs).

Replaces two copies of the same algorithm: ``threejs/lint.py:check_syntax`` +
``_lint_imports`` and ``scene_threejs/lint.py:_node_check`` + ``_check_imports``.
Both ran ``node --input-type=module --check`` over stdin and scraped the same
``[stdin]:N`` / ``SyntaxError:`` lines; both matched static, re-export and dynamic
import specifiers with near-identical regexes and applied the same allowlist
(``three``, ``three/addons/*``, ``three/examples/jsm/*``, relative files under
``src/``).  They differed only in message text (owned by the caller via
``make_finding``, as in :mod:`_ast_lint`) and in two defects this module fixes:
threejs's fallback message was a ``list`` (``splitlines()[-1:]``), and scene's
escape check was a ``str.startswith`` prefix test that let ``src2/`` pass.
Escape is decided before existence so a symlink/``..`` hop out of ``src/`` is
reported as an escape, never as "missing".
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from codeverse3d.contracts.artifacts import GateFinding

# threejs's regexes (the superset: side-effect ``import './x.js'``, ``export * from``,
# ``export * as ns from`` and multi-line ``import {\n a,\n b\n} from`` all match)
_IMPORT_RE = re.compile(
    r"""(?:^|\n)\s*(?:import\s+(?:[^'";]*?\s+from\s+)?|export\s+(?:\*(?:\s+as\s+\w+)?|\{[^}]*\})\s+from\s+)['"]([^'"]+)['"]"""
)
_DYN_IMPORT_RE = re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_STDIN_LINE_RE = re.compile(r"\[stdin\]:(\d+)|:(\d+)\n")
_URL_RE = re.compile(r"https?://")


@dataclass(frozen=True)
class SyntaxProblem:
    """What ``node --check`` reported: ``line`` is None when it could not be parsed
    (or node could not run), ``stderr_tail`` is the last 600 chars of stderr."""

    line: int | None
    message: str
    stderr_tail: str


def node_check_syntax(path: Path, node_bin: str) -> SyntaxProblem | None:
    """``None`` when ``path`` parses as ESM under ``node --input-type=module --check``.

    Stdin is used because ``node --check file.js`` treats a bare ``.js`` as
    CommonJS-or-detect.
    """
    try:
        proc = subprocess.run(
            [node_bin, "--input-type=module", "--check"],
            input=path.read_bytes(), capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return SyntaxProblem(None, f"node --check could not run: {e}", "")
    if proc.returncode == 0:
        return None
    err = proc.stderr.decode(errors="replace").strip()
    m = _STDIN_LINE_RE.search(err)
    line = int(m.group(1) or m.group(2)) if m else None
    lines = err.splitlines()
    msg = next((ln for ln in lines if "Error" in ln), lines[0] if lines else "syntax error")
    return SyntaxProblem(line, msg, err[-600:])


def import_specs(src: str) -> list[tuple[str, int]]:
    """``(specifier, 1-based line)`` for every static, re-export and dynamic import in ``src``."""
    out = [(m.group(1), m.start(1)) for m in _IMPORT_RE.finditer(src)]
    out += [(m.group(1), m.start(1)) for m in _DYN_IMPORT_RE.finditer(src)]
    return [(spec, src.count("\n", 0, pos) + 1) for spec, pos in out]


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
