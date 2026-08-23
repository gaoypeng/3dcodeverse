"""Static lint for agent-authored CadQuery scripts (``src/model.py``).

AST based, offline.  Enforces the import allow-list, forbids file IO / export /
``show_object``, requires a module-level ``result`` and flags the known OCC
pitfalls (degree/radian confusion, hemisphere ``makeSphere``, unnamed parts …).
"""

from __future__ import annotations

import ast
import re
import time
from pathlib import Path

from codeverse.contracts.artifacts import GateFinding, GateReport, Severity

GATE = "lint:cadquery"
ALLOWED_IMPORTS = {"cadquery", "cq", "math", "random", "numpy", "np", "itertools", "functools", "collections",
                   "typing", "dataclasses", "enum", "copy", "statistics", "operator", "__future__"}
FORBIDDEN_IMPORTS = {"os", "sys", "subprocess", "shutil", "pathlib", "urllib", "requests", "socket", "http", "pickle",
                     "ctypes", "multiprocessing", "threading", "importlib", "OCP", "OCC", "ocp_vscode", "cq_editor", "cq_warehouse", "build123d"}
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
PASCAL_RE = re.compile(r"^[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)*(?:_\d+)?$")


def dotted(node: ast.AST) -> str:
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


RADIAN_CONSTS = {"pi", "tau"}
TO_RADIANS_FUNCS = {"radians", "deg2rad"}
TO_DEGREES_FUNCS = {"degrees", "rad2deg"}


def looks_like_radians(expr: ast.expr) -> bool:
    """True when ``expr`` is a radians expression (``math.pi``, ``math.pi / 2``, ``i * 2 * math.pi / n``,
    ``math.radians(a)``) — never a plain name/number, never a conversion TO degrees
    (``a * 180 / math.pi``, ``math.degrees(a)``).  Only the ``pi``/``tau`` constants and the
    radians/degrees helpers are inspected, so identifiers such as ``n_pins`` or ``pitch`` cannot match.
    """
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


class _Collector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: dict[str, int] = {}
        self.calls: list[tuple[str, ast.Call]] = []
        self.add_calls: list[ast.Call] = []
        self.name_kw: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            top = a.name.split(".")[0]
            self.imports.setdefault(top, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.setdefault((node.module or "").split(".")[0], node.lineno)

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


def _rules(c: _Collector, tree: ast.Module, source: str) -> list[GateFinding]:
    E, W, I = Severity.ERROR, Severity.WARN, Severity.INFO  # noqa: E741
    out: list[GateFinding] = []
    if "cadquery" not in c.imports:
        out.append(_f(E, "model.py never imports cadquery", 1, "start with `import cadquery as cq`"))
    for mod, line in c.imports.items():
        if mod in FORBIDDEN_IMPORTS:
            out.append(_f(E, f"forbidden import `{mod}`", line, "only `cadquery`, `math`, `random`, `numpy` (+ stdlib data helpers) are allowed"))
        elif mod not in ALLOWED_IMPORTS:
            out.append(_f(W, f"unexpected import `{mod}` (may not exist in the build sandbox)", line, "stick to cadquery / math / random / numpy"))
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
        if name.endswith(".fillet") or name.endswith(".chamfer"):
            out.append(_f(I, f"{last}: OCC fails ('BRep_API: command not done') when radius >= wall thickness or edges conflict", call.lineno,
                          "keep radius < 0.45 x min thickness; select few edges (`.edges('|Z')`); wrap cosmetic fillets in try/except"))
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
    try:
        tree = ast.parse(source, filename=target)
    except SyntaxError as e:
        f = _f(Severity.ERROR, f"SyntaxError: {e.msg}", e.lineno, f"fix the syntax near line {e.lineno}: {(e.text or '').strip()!r}", target)
        return GateReport(gate=GATE, passed=False, findings=[f], duration_ms=int((time.monotonic() - t0) * 1000))
    c = _Collector()
    c.visit(tree)
    findings = _rules(c, tree, source)
    for f in findings:
        f.target = target
    passed = not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=GATE, passed=passed, findings=findings, duration_ms=int((time.monotonic() - t0) * 1000))


def lint_cadquery_file(path: Path, *, target: str = "src/model.py") -> GateReport:
    if not path.is_file():
        return GateReport(gate=GATE, passed=False, findings=[_f(Severity.ERROR, f"{target} is missing", None, "create src/model.py (see the skeleton)", target)])
    return lint_cadquery_source(path.read_text(), target=target)
