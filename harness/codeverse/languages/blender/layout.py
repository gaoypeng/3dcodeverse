"""Multi-file layout of a blender workspace: ``src/model.py`` + ``src/parts/<snake>.py``.

One place states the file convention so the runtime (``file_for_part``), the skeleton,
the workspace lint and the prompts agree:

* ``src/model.py`` — entry.  Imports the part builders, calls them in order, runs the
  self-check.  No geometry of its own beyond small glue.
* ``src/parts/<snake>.py`` — one per plan part; exports ``def build_<snake>() -> bpy.types.Object``
  returning the named object at its world pose (instances ``Name_0..N-1`` built inside).
* ``src/parts/_<anything>.py`` — optional shared helpers (not a part; no export required).
* A single-file ``src/model.py`` (no ``parts/`` directory) stays valid for small objects.
"""

from __future__ import annotations

import ast
import time
from pathlib import Path

from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.conventions import to_snake
from codeverse.languages.blender.lint import GATE, lint_blender_source
from codeverse.workspace import Workspace

ENTRY_REL = "src/model.py"
PARTS_DIR = "parts"
PARTS_PKG = "parts"


def part_file_rel(part_name: str) -> str:
    """``'Seat Cushion'`` → ``'src/parts/seat_cushion.py'`` (workspace-relative)."""
    return f"src/{PARTS_DIR}/{to_snake(part_name)}.py"


def build_fn_name(part_name: str) -> str:
    """``'SeatCushion'`` → ``'build_seat_cushion'`` (the function a part file must export)."""
    return f"build_{to_snake(part_name)}"


def part_files(ws: Workspace) -> list[Path]:
    """Part modules present in the workspace (sorted; underscore helper modules excluded)."""
    d = ws.src / PARTS_DIR
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.py") if not p.name.startswith("_"))


def helper_files(ws: Workspace) -> list[Path]:
    """Underscore-prefixed shared modules under ``src/parts/`` (linted, not required to export)."""
    d = ws.src / PARTS_DIR
    return sorted(d.glob("_*.py")) if d.is_dir() else []


def source_files(ws: Workspace) -> list[Path]:
    """Every agent-authored python file under ``src/`` (entry first), including nested dirs."""
    entry = ws.src / "model.py"
    rest = sorted(p for p in ws.src.rglob("*.py") if p != entry)
    return ([entry] if entry.is_file() else []) + rest


def _rel(ws: Workspace, p: Path) -> str:
    return p.relative_to(ws.root).as_posix()


def _finding(sev: Severity, target: str, msg: str, hint: str, line: int | None = None) -> GateFinding:
    return GateFinding(gate=GATE, severity=sev, target=target, message=msg, fix_hint=hint,
                       data={"line": line} if line else {})


def _exported_functions(tree: ast.Module) -> set[str]:
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _imported_part_modules(tree: ast.Module) -> set[str]:
    """snake names of ``parts.<snake>`` modules the entry references via import statements."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            dotted = node.module.split(".")
            if dotted[0] == PARTS_PKG:
                if len(dotted) > 1:
                    out.add(dotted[1])
                else:  # ``from parts import seat, leg``
                    out.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for a in node.names:
                dotted = a.name.split(".")
                if dotted[0] == PARTS_PKG and len(dotted) > 1:
                    out.add(dotted[1])
    return out


def _layout_rules(ws: Workspace, parts: list[Path], entry_tree: ast.Module | None) -> list[GateFinding]:
    """Cross-file rules: naming, exported builders, import-time side effects, unused part files."""
    out: list[GateFinding] = []
    E, W = Severity.ERROR, Severity.WARN
    imported = _imported_part_modules(entry_tree) if entry_tree is not None else set()
    for p in parts:
        target = _rel(ws, p)
        stem = p.stem
        if stem != to_snake(stem):
            out.append(_finding(E, target, f"part file name '{p.name}' is not snake_case",
                                f"rename to src/{PARTS_DIR}/{to_snake(stem)}.py (the harness maps plan part "
                                f"'{stem}' → that file) and fix the import in model.py"))
            continue
        try:
            tree = ast.parse(p.read_text(), filename=target)
        except SyntaxError:
            continue  # reported by the per-file lint
        fn = f"build_{stem}"
        if fn not in _exported_functions(tree):
            out.append(_finding(E, target, f"{target} does not define `def {fn}()`",
                                f"every part file exports `def {fn}() -> bpy.types.Object` returning the named object "
                                f"at its world pose; model.py calls it (`from {PARTS_PKG}.{stem} import {fn}`)"))
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                name = getattr(node.value.func, "id", "")
                if name.startswith("build_") or name == "main":
                    out.append(_finding(W, target, f"{target} calls `{name}()` at import time",
                                        "part files only DEFINE builders; model.py calls them once (a module-level "
                                        "call here builds the part twice → auto-suffixed 'Name.001')", node.lineno))
        if entry_tree is not None and stem not in imported:
            out.append(_finding(W, target, f"{target} is never imported by src/model.py → its part is not built",
                                f"add `from {PARTS_PKG}.{stem} import {fn}` to model.py and call `{fn}()` in main()"))
    return out


def lint_workspace(ws: Workspace) -> GateReport:
    """Lint every python file under ``src/`` + the multi-file layout rules (one merged report)."""
    t0 = time.monotonic()
    entry = ws.src / "model.py"
    if not entry.is_file():
        return GateReport(gate=GATE, passed=False, duration_ms=0, findings=[_finding(
            Severity.ERROR, ENTRY_REL, f"{ENTRY_REL} is missing",
            "create src/model.py (entry: imports src/parts/<snake>.py builders and calls them; see the skeleton)")])
    parts = part_files(ws)
    findings: list[GateFinding] = []
    for p in source_files(ws):
        target = _rel(ws, p)
        is_entry = p == entry
        is_helper = p.name.startswith("_")
        rep = lint_blender_source(p.read_text(), target=target,
                                  expect_names=not (is_helper or (is_entry and bool(parts))),
                                  expect_bpy=not is_helper)
        findings.extend(rep.findings)
    entry_tree: ast.Module | None
    try:
        entry_tree = ast.parse(entry.read_text(), filename=ENTRY_REL)
    except SyntaxError:
        entry_tree = None
    findings.extend(_layout_rules(ws, parts, entry_tree))
    passed = not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=GATE, passed=passed, findings=findings, duration_ms=int((time.monotonic() - t0) * 1000))
