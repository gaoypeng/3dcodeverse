"""CadQuery language: runtime, lint, skeleton; wrapper runs in a python subprocess."""

from __future__ import annotations

import ast
import os
import re
import sys
import time
from pathlib import Path

from codeverse3d.config import Settings, get_settings
from codeverse3d.contracts.artifacts import BuildResult, GateFinding, GateReport, Severity
from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.conventions import PASCAL_RE
from codeverse3d.languages._ast_lint import (
    BASE_FORBIDDEN_IMPORTS,
    ImportCollector,
    check_imports,
    describe_parse_failure,
    dotted,
    safe_parse,
)
from codeverse3d.languages._common import run_wrapper_build
from codeverse3d.languages.base import RuntimeLayout
from codeverse3d.proc import scrub_secrets
from codeverse3d.workspace import Workspace

# ===================================================================== lint
GATE = "lint:cadquery"
ALLOWED_IMPORTS = {"cadquery", "math", "random", "numpy", "itertools", "functools", "collections",
                   "typing", "dataclasses", "enum", "copy", "statistics", "operator", "__future__"}
FORBIDDEN_IMPORTS = frozenset(BASE_FORBIDDEN_IMPORTS | {
    "os", "sys", "pathlib",  # no file-system / interpreter access (unlike blender, nothing legitimate needs them)
    "OCP", "OCC", "ocp_vscode", "cq_editor", "cq_warehouse", "build123d",  # raw cadquery only — no OCC/tooling layers
})
FORBIDDEN_CALLS: tuple[tuple[str, str], ...] = (
    ("show_object", "show_object() exists only in CQ-editor; delete it — the harness renders for you"),
    ("show", "show() is for notebooks/CQ-editor; delete it"),
    ("open", "no file IO; the harness exports STEP/STL/GLB from `result`"),
    ("cq.exporters.export", "no exporting; assign the model to `result` and the harness exports"),
    ("exporters.export", "no exporting; assign the model to `result` and the harness exports"),
    ("cq.importers.", "no importing external geometry; build it procedurally"),
    ("importers.", "no importing external geometry; build it procedurally"),
    ("exit", "never exit; `result` must be readable after the module runs"),
    ("quit", "never exit"),
)
FORBIDDEN_METHODS = {"save": "no .save()/.export() on the assembly — the harness exports", "export": "no .export() — the harness exports",
                     "exportStep": "no export calls", "exportStl": "no export calls", "exportSvg": "no export calls"}
RADIAN_CONSTS = {"pi", "tau"}
TO_RADIANS_FUNCS = {"radians", "deg2rad"}
TO_DEGREES_FUNCS = {"degrees", "rad2deg"}


def looks_like_radians(expr: ast.expr) -> bool:
    """True when ``expr`` is a radians expression (``math.pi / 2``, ``math.radians(a)``), not a conversion to degrees."""
    hit = False

    def walk(node: ast.AST, inverted: bool) -> bool:
        """Return True to stop: the expression is a conversion to degrees."""
        nonlocal hit
        if isinstance(node, ast.Call):
            fn = dotted(node.func).rsplit(".", 1)[-1]
            if fn in TO_DEGREES_FUNCS:
                return True
            if fn in TO_RADIANS_FUNCS:
                hit = True
            return any(walk(a, inverted) for a in node.args)
        if isinstance(node, ast.BinOp):
            if walk(node.left, inverted):
                return True
            return walk(node.right, inverted != isinstance(node.op, ast.Div))
        is_const = (isinstance(node, ast.Attribute) and node.attr in RADIAN_CONSTS) or (isinstance(node, ast.Name) and node.id in RADIAN_CONSTS)
        if is_const:
            if inverted:  # ``x * 180 / math.pi`` → degrees
                return True
            hit = True
            return False
        return any(walk(ch, inverted) for ch in ast.iter_child_nodes(node))

    if walk(expr, False):
        return False
    return hit


def _f(sev: Severity, msg: str, line: int | None = None, hint: str = "", target: str = "src/model.py") -> GateFinding:
    return GateFinding(gate=GATE, severity=sev, target=target, message=msg, fix_hint=hint, data={"line": line} if line else {})


class CqCollector(ImportCollector):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, ast.Call]] = []
        self.add_calls: list[ast.Call] = []
        self.name_kw: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = dotted(node.func)
        self.calls.append((name, node))
        if name.endswith(".add"):
            self.add_calls.append(node)
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    self.name_kw.append(kw.value.value)
        self.generic_visit(node)


def _top_level_assigned(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Names bound at module level; names bound only under ``if __name__ == '__main__'``."""
    top: set[str] = set()
    guarded: set[str] = set()

    def targets(stmt: ast.stmt) -> list[str]:
        out = []
        if isinstance(stmt, (ast.Assign,)):
            for t in stmt.targets:
                out += [n.id for n in ast.walk(t) if isinstance(n, ast.Name)]
        elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)) and isinstance(stmt.target, ast.Name):
            out.append(stmt.target.id)
        elif isinstance(stmt, (ast.For, ast.With, ast.If, ast.Try)):
            for sub in ast.iter_child_nodes(stmt):
                if isinstance(sub, ast.stmt):
                    out += targets(sub)
        return out

    for stmt in tree.body:
        if isinstance(stmt, ast.If) and "__main__" in ast.dump(stmt.test):
            for sub in stmt.body:
                guarded.update(targets(sub))
        else:
            top.update(targets(stmt))
    return top, guarded


def _rules(c: CqCollector, tree: ast.Module, source: str) -> list[GateFinding]:
    E, W, I = Severity.ERROR, Severity.WARN, Severity.INFO  # noqa: E741
    out: list[GateFinding] = []
    if "cadquery" not in c.imports:
        out.append(_f(E, "model.py never imports cadquery", 1, "start with `import cadquery as cq`"))

    def _import_finding(kind: str, mod: str, line: int) -> GateFinding:
        if kind == "forbidden":
            return _f(E, f"forbidden import `{mod}`", line, "only `cadquery`, `math`, `random`, `numpy` (+ stdlib data helpers) are allowed")
        return _f(W, f"unexpected import `{mod}` (may not exist in the build sandbox)", line, "stick to cadquery / math / random / numpy")

    out.extend(check_imports(c.imports, forbidden=FORBIDDEN_IMPORTS, allowed=ALLOWED_IMPORTS, make_finding=_import_finding))
    top, guarded = _top_level_assigned(tree)
    if "result" not in top:
        hint = "assign `result = cq.Assembly(...)` (or a Workplane) at module level, NOT under `if __name__ == '__main__':`" if "result" in guarded \
            else "end the file with a module-level `result = <cq.Assembly or cq.Workplane>`"
        out.append(_f(E, "no module-level `result` assignment", None, hint))
    for name, call in c.calls:
        for pat, hint in FORBIDDEN_CALLS:
            if name == pat or (pat.endswith(".") and name.startswith(pat)):
                out.append(_f(E, f"forbidden call `{name}()`", call.lineno, hint))
        last = name.rsplit(".", 1)[-1] if "." in name else ""
        if last in FORBIDDEN_METHODS and not name.startswith("cq."):
            out.append(_f(E, f"forbidden call `{name}()`", call.lineno, FORBIDDEN_METHODS[last]))
        if name.endswith("Solid.makeSphere") and not any(k.arg and k.arg.startswith("angle") for k in call.keywords):
            out.append(_f(W, "cq.Solid.makeSphere(r) without angle args builds a partial sphere in some versions", call.lineno,
                          "use `cq.Workplane('XY').sphere(r)` or pass angleDegrees1=-90, angleDegrees2=90, angleDegrees3=360"))
        if name.endswith(".rotate") and len(call.args) == 3 and looks_like_radians(call.args[2]):
            out.append(_f(E, ".rotate(...) takes DEGREES; this argument looks like radians (math.pi expression)", call.lineno,
                          "pass degrees: `.rotate((0,0,0), (0,0,1), 90)` or `math.degrees(angle_rad)`"))
    if c.add_calls:
        unnamed = [a.lineno for a in c.add_calls if not any(k.arg == "name" for k in a.keywords)]
        if unnamed:
            out.append(_f(W, f"assembly .add() without name= at line(s) {unnamed[:6]} → auto uuid node names", unnamed[0],
                          "give every part a PascalCase name: `.add(shape, name='SeatCushion', color=cq.Color(r, g, b))`"))
        uncol = [a.lineno for a in c.add_calls if not any(k.arg == "color" for k in a.keywords)]
        if uncol:
            out.append(_f(I, f".add() without color= at line(s) {uncol[:6]} (exports grey)", uncol[0], "add `color=cq.Color(r, g, b)` (0-1 floats)"))
        bad = [n for n in c.name_kw if not PASCAL_RE.match(n)]
        if bad:
            out.append(_f(W, f"part names not PascalCase: {bad[:6]}", None, "use PascalCase part names, instances Name_0..Name_N"))
    elif "Assembly" not in source:
        out.append(_f(W, "result is not a cq.Assembly: the model exports as ONE unnamed part", None,
                      "prefer `result = cq.Assembly(); result.add(part, name='PartName', color=cq.Color(...))` per part"))
    if re.search(r"\.cylinder\(\s*[0-9.]+\s*,\s*[0-9.]+\s*\)", source):
        out.append(_f(I, "Workplane.cylinder(height, radius): height comes FIRST", None, "check the argument order: .cylinder(height, radius)"))
    return out


def lint_cadquery_source(source: str, *, target: str = "src/model.py") -> GateReport:
    t0 = time.monotonic()
    tree, exc = safe_parse(source, target)
    if tree is None:
        msg, hint, line = describe_parse_failure(exc)  # type: ignore[arg-type]
        f = _f(Severity.ERROR, msg, line, hint, target)
        return GateReport.of(GATE, [f], duration_ms=int((time.monotonic() - t0) * 1000))
    c = CqCollector()
    c.visit(tree)
    findings = _rules(c, tree, source)
    for f in findings:
        f.target = target
    return GateReport.of(GATE, findings, duration_ms=int((time.monotonic() - t0) * 1000))


def lint_cadquery_file(path: Path, *, target: str = "src/model.py") -> GateReport:
    if not path.is_file():
        return GateReport.of(GATE, [_f(Severity.ERROR, f"{target} is missing", None, "create src/model.py (see the skeleton)", target)])
    return lint_cadquery_source(path.read_text(), target=target)


# ===================================================================== skeleton


# ===================================================================== runtime
WRAPPER = Path(__file__).resolve().parent.parent / "wrappers" / "run_cq.py"


def cadquery_env() -> dict[str, str]:
    """Environment for the wrapper subprocess: the user's env minus credential-shaped vars."""
    env = scrub_secrets(dict(os.environ))
    env.pop("PYTHONSTARTUP", None)
    env.setdefault("OMP_NUM_THREADS", "4")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


class CadQueryRuntime(RuntimeLayout):
    """LanguageRuntime for ``Language.CADQUERY``."""

    language = Language.CADQUERY
    entry_globs: tuple[str, ...] = (ENTRY_FILE[Language.CADQUERY],)

    def __init__(self, *, python: str | None = None, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._python = python or sys.executable

    def entry_file(self, ws: Workspace) -> Path:
        return ws.src / "model.py"

    def build_command(self, ws: Workspace) -> list[str]:
        """The wrapper's own constants are the tessellation and the seed."""
        return [self._python, str(WRAPPER), "--script", str(self.entry_file(ws)), "--out", str(ws.artifacts)]


    def lint(self, ws: Workspace) -> GateReport:
        return lint_cadquery_file(self.entry_file(ws))

    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        return run_wrapper_build(
            ws, language=self.language, extras={"step": "object.step", "stl": "object.stl"},
            argv=lambda: self.build_command(ws),
            env=cadquery_env(), timeout_s=timeout_s or self._settings.limits.build_timeout_s)

