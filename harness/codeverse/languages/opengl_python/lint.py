"""AST lint for ``src/program.py`` (raw moderngl program).

Rules (gate ``lint:opengl_python``, ``data["kind"]`` names the rule):
* syntax must parse; ``setup(ctx, width, height)`` and ``render(ctx, state, t, frame, fbo)`` must exist;
* imports restricted to an allow-list (moderngl, numpy, math, random, struct, array, pathlib,
  typing, dataclasses, itertools, functools, collections, colorsys);
* no window / input libs (glfw, pygame, pyglet, PyQt…), no os / sys / subprocess / socket / requests;
* no ``open()`` / exec / eval / __import__ / compile (GLSL travels as inline strings or via
  ``Path(__file__).with_name("x.glsl").read_text()``);
* no ``time.time()`` / ``datetime.now()`` / unseeded randomness (frames must be deterministic in ``t``);
* ``moderngl.create_context`` / ``create_standalone_context`` inside the program (the harness owns the context);
* GLSL strings in the file get a light ``#version`` check (must be ``330 core`` or absent → warn).
"""

from __future__ import annotations

import ast
import re

from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.languages._ast_lint import BASE_FORBIDDEN_IMPORTS, check_imports
from codeverse.workspace import Workspace

GATE = "lint:opengl_python"
PROGRAM = "src/program.py"
ALLOWED_MODULES: frozenset[str] = frozenset({
    "moderngl", "numpy", "math", "random", "struct", "array", "pathlib", "typing", "dataclasses", "itertools",
    "functools", "collections", "colorsys", "__future__", "enum",
})
WINDOW_LIBS = {"glfw", "pygame", "pyglet", "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter", "moderngl_window", "sdl2", "wx", "OpenGL"}
DANGEROUS_MODULES = frozenset(BASE_FORBIDDEN_IMPORTS | {
    "os", "sys",  # no interpreter / file-system access
    "signal", "tempfile", "io", "time", "datetime",  # frames must be a pure, deterministic function of t
})
FORBIDDEN_CALLS = {"open", "exec", "eval", "compile", "__import__", "input", "exit", "quit", "breakpoint"}
MAX_CHARS = 120_000
_VERSION_RE = re.compile(r"#\s*version\s+(\d+)(?:\s+(\w+))?")


def _finding(sev: Severity, kind: str, msg: str, hint: str, line: int | None = None) -> GateFinding:
    return GateFinding(gate=GATE, severity=sev, target=PROGRAM, message=(f"{PROGRAM}:{line}: " if line else f"{PROGRAM}: ") + msg,
                       fix_hint=hint, data={"kind": kind, "line": line, "file": PROGRAM})


def _root(name: str) -> str:
    return name.split(".", 1)[0]


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: list[GateFinding] = []
        self.funcs: dict[str, ast.FunctionDef] = {}
        self.seeded = False
        self.uses_random = False

    # imports -------------------------------------------------------------
    def _check_module(self, name: str, line: int) -> None:
        root = _root(name)
        if root in WINDOW_LIBS:
            self.findings.append(_finding(Severity.ERROR, "window_lib", f"imports `{name}` (window / input library)",
                                          "the harness creates the context and framebuffer; draw into the fbo it passes — no window", line))
            return

        def _import_finding(kind: str, _mod: str, ln: int) -> GateFinding:
            if kind == "forbidden":
                return _finding(Severity.ERROR, "forbidden_import", f"imports `{name}` (not allowed in a rendering program)",
                                "allowed: moderngl, numpy, math, random, struct, array, pathlib, typing, dataclasses, itertools, "
                                "functools, collections, colorsys", ln)
            return _finding(Severity.ERROR, "forbidden_import", f"imports `{name}` which is outside the allow-list",
                            "use only moderngl + numpy + stdlib math/random/struct/array/pathlib", ln)

        self.findings.extend(check_imports({root: line}, forbidden=DANGEROUS_MODULES, allowed=ALLOWED_MODULES,
                                           make_finding=_import_finding))

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self._check_module(a.name, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._check_module(node.module, node.lineno)
        self.generic_visit(node)

    # calls ---------------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else None
        attr = fn.attr if isinstance(fn, ast.Attribute) else None
        base = fn.value.id if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) else None
        if name in FORBIDDEN_CALLS:
            self.findings.append(_finding(Severity.ERROR, "forbidden_call", f"calls `{name}()`",
                                          "no file/console IO: put GLSL in triple-quoted strings, or Path(__file__).with_name('x.glsl').read_text()",
                                          node.lineno))
        if attr in ("create_context", "create_standalone_context") and base == "moderngl":
            self.findings.append(_finding(Severity.ERROR, "own_context", "creates its own moderngl context",
                                          "use the `ctx` passed to setup()/render(); never create a context or window", node.lineno))
        if base == "time" and attr in ("time", "perf_counter", "monotonic"):
            self.findings.append(_finding(Severity.ERROR, "wall_clock", "reads the wall clock", "use the `t` argument of render(); frames must be a pure function of t", node.lineno))
        if base == "random" and attr == "seed":
            self.seeded = True
        if base == "random" and attr in ("random", "uniform", "randint", "gauss", "choice", "shuffle"):
            self.uses_random = True
        if base in ("np", "numpy") and attr == "seed":
            self.seeded = True
        if attr in ("write_text", "write_bytes", "unlink", "mkdir", "rmdir", "rename"):
            self.findings.append(_finding(Severity.ERROR, "file_write", f"calls `.{attr}()` (file system write)", "programs never write files", node.lineno))
        if attr == "read_text" or attr == "read_bytes":
            self.findings.append(_finding(Severity.INFO, "read_file", f"reads a file via `.{attr}()` — only src/*.glsl shipped next to program.py is allowed",
                                          "Path(__file__).with_name('x.glsl').read_text()", node.lineno))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.funcs[node.name] = node
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and "#version" in node.value:
            m = _VERSION_RE.search(node.value)
            if m and (m.group(1) != "330" or (m.group(2) or "core") != "core"):
                self.findings.append(_finding(Severity.WARN, "glsl_version", f"GLSL string uses `#version {m.group(1)} {m.group(2) or ''}`; the context is GL 3.3 core",
                                              "use `#version 330 core` in every shader string", node.lineno))
        if isinstance(node.value, str) and "gl_FragColor" in node.value:
            self.findings.append(_finding(Severity.ERROR, "legacy_glsl", "GLSL string uses gl_FragColor (GLSL 1.x)",
                                          "declare `out vec4 fragColor;` and write it (GLSL 330 core)", node.lineno))
        if isinstance(node.value, str) and ("attribute " in node.value or "varying " in node.value) and "#version" in node.value:
            self.findings.append(_finding(Severity.ERROR, "legacy_glsl", "GLSL string uses attribute/varying (GLSL 1.x)",
                                          "use `in` / `out` qualifiers (GLSL 330 core)", node.lineno))


def lint_source(text: str) -> list[GateFinding]:
    findings: list[GateFinding] = []
    if len(text) > MAX_CHARS:
        findings.append(_finding(Severity.WARN, "too_long", f"file is {len(text)} chars", "keep the program focused"))
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        findings.append(_finding(Severity.ERROR, "syntax", f"SyntaxError: {e.msg}", "fix the syntax at this line", e.lineno))
        return findings
    v = _Visitor()
    v.visit(tree)
    findings.extend(v.findings)
    for fn, nargs in (("setup", 3), ("render", 5)):
        node = v.funcs.get(fn)
        if node is None:
            findings.append(_finding(Severity.ERROR, "missing_entry", f"missing `def {fn}(...)`",
                                     "def setup(ctx, width, height) -> state  and  def render(ctx, state, t, frame, fbo) -> None"))
        elif len(node.args.args) != nargs and node.args.vararg is None:
            findings.append(_finding(Severity.ERROR, "bad_signature", f"`{fn}` takes {len(node.args.args)} positional args, expected {nargs}",
                                     "setup(ctx, width, height) / render(ctx, state, t, frame, fbo)", node.lineno))
    if v.uses_random and not v.seeded:
        findings.append(_finding(Severity.WARN, "unseeded_random", "uses random.* without random.seed(...)",
                                 "call random.seed(0) / np.random.default_rng(0) in setup() so frames are reproducible"))
    return findings


def lint_workspace(ws: Workspace) -> GateReport:
    p = ws.root / PROGRAM
    if not p.is_file():
        f = [_finding(Severity.ERROR, "missing_entry", f"{PROGRAM} is missing",
                      "create src/program.py with setup(ctx, width, height) and render(ctx, state, t, frame, fbo)")]
        return GateReport(gate=GATE, passed=False, findings=f)
    findings = lint_source(p.read_text(errors="replace"))
    for extra in sorted(ws.src.rglob("*.py")):
        rel = str(extra.relative_to(ws.root))
        if rel != PROGRAM:
            findings.append(GateFinding(gate=GATE, severity=Severity.WARN, target=rel, message=f"{rel}: extra python module is ignored by the harness",
                                        fix_hint="keep all code in src/program.py (GLSL may live in src/*.glsl)", data={"kind": "stray_file", "file": rel}))
    passed = not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=GATE, passed=passed, findings=findings)
