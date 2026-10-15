"""opengl_python language: raw OpenGL (moderngl) programs rendered headless by the harness."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from codeverse3d.contracts.artifacts import BuildResult, GateFinding, GateReport, Severity
from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.contracts.plan import GraphicsPlan, Plan
from codeverse3d.languages._ast_lint import (
    BASE_FORBIDDEN_IMPORTS,
    check_imports,
    describe_parse_failure,
    safe_parse,
)
from codeverse3d.languages._common import missing_entry, ws_rel
from codeverse3d.languages._gl_common import (
    finish_build,
    graphics_plan,
    invalidate_stale_outputs,
    judge_times,
    make_host,
    parse_glsl_log,
    resolution_for,
    traceback_location,
)
from codeverse3d.languages.base import RuntimeLayout
from codeverse3d.spatial.gl_render import GlHost, gif_times
from codeverse3d.workspace import Workspace

# ===================================================================== lint
GATE = "lint:opengl_python"
PROGRAM = ENTRY_FILE[Language.OPENGL_PYTHON]
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
                                "allowed: " + ", ".join(sorted(ALLOWED_MODULES)), ln)
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
        if attr == "default_rng":   # np.random.default_rng(seed) — the seeding the contract recommends
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
    tree, exc = safe_parse(text)
    if tree is None:
        msg, hint, line = describe_parse_failure(exc)  # type: ignore[arg-type]
        findings.append(_finding(Severity.ERROR, "syntax", msg, hint if not isinstance(exc, SyntaxError) else "fix the syntax at this line", line))
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
        findings.append(_finding(Severity.WARN, "unseeded_random", "uses random.* without random.seed(...) or a default_rng(...)",
                                 "call random.seed(0) / np.random.default_rng(0) in setup() so frames are reproducible"))
    return findings


def lint_workspace(ws: Workspace) -> GateReport:
    p = ws.root / PROGRAM
    if not p.is_file():
        f = [_finding(Severity.ERROR, "missing_entry", f"{PROGRAM} is missing",
                      "create src/program.py with setup(ctx, width, height) and render(ctx, state, t, frame, fbo)")]
        return GateReport.of(GATE, f)
    findings = lint_source(p.read_text(errors="replace"))
    for extra in sorted(ws.src.rglob("*.py")):
        rel = ws_rel(ws, extra)
        if rel != PROGRAM:
            findings.append(GateFinding(gate=GATE, severity=Severity.WARN, target=rel, message=f"{rel}: extra python module is not part of the deliverable (only src/program.py and src/*.glsl are)",
                                        fix_hint="keep all code in src/program.py (GLSL may live in src/*.glsl)", data={"kind": "stray_file", "file": rel}))
    return GateReport.of(GATE, findings)


# ===================================================================== skeleton
_TEMPLATE = '''"""src/program.py — {title}

CONTRACT (the harness imports this module in a headless moderngl process; the full text is the
language contract):
  def setup(ctx, width, height) -> state     create programs / VAOs / textures / FBOs ONCE; return any object
  def render(ctx, state, t, frame, fbo)      draw the frame at time t (seconds) into `fbo` (already bound + cleared)
* `ctx` is a moderngl.Context (GL 3.3 core).  Never create a context or window; never read the clock.
* The final draw of every call goes into `fbo` (bind it and reset ctx.viewport first if you used your own FBOs).
* Same t → same frame, whatever was rendered before; `frame` is a schedule index, not time.  Seed randomness
  in setup.  GLSL strings start with `#version 330 core`.
Judged frames at t = 0, 1, 2.5, 4, 6 s (clipped to the {duration:g} s duration).  Measured on them: largest
frame-to-frame mean |Δ| below 0.002 → static (cap 0.5); above 0.35 → flicker (cap 0.7); NaN/Inf → cap 0;
every frame black or blown → cap 0.5; mean edge density below 0.002 → reported as a near-flat gradient.

PLAN — {summary}
Style: {style}
{passes}
{visuals}
Motion: {motion}
"""
import moderngl
import numpy as np

# This stub only proves the interface: one full-screen pass whose output depends on t.
STUB_VERT = """#version 330 core
in vec2 in_pos;
out vec2 v_uv;
void main() {{ v_uv = in_pos * 0.5 + 0.5; gl_Position = vec4(in_pos, 0.0, 1.0); }}
"""
STUB_FRAG = """#version 330 core
uniform float u_time;
in vec2 v_uv;
out vec4 fragColor;
void main() {{ fragColor = vec4(vec3(0.5 + 0.5 * sin(6.2832 * (v_uv.x - 0.05 * u_time))), 1.0); }}
"""


def setup(ctx: moderngl.Context, width: int, height: int):
    prog = ctx.program(vertex_shader=STUB_VERT, fragment_shader=STUB_FRAG)
    quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
    vao = ctx.vertex_array(prog, [(quad, "2f", "in_pos")])
    # TODO build the plan's passes here (programs, buffers, textures, your own FBOs)
    return {{"prog": prog, "vao": vao, "size": (width, height)}}


def render(ctx: moderngl.Context, state, t: float, frame: int, fbo: moderngl.Framebuffer) -> None:
    fbo.use()
    ctx.disable(moderngl.DEPTH_TEST)
    ctx.viewport = (0, 0, *state["size"])
    state["prog"]["u_time"].value = t
    state["vao"].render(mode=moderngl.TRIANGLE_STRIP)
    # TODO the plan's passes; the final draw goes into `fbo` (bind it and reset ctx.viewport first)
'''


def _comment_lines(prefix: str, items: list[str]) -> str:
    if not items:
        return f"{prefix}: (none)"
    return "\n".join(f"  TODO {prefix} {i + 1}: {x}" for i, x in enumerate(items))


def program_source(plan: GraphicsPlan | None) -> str:
    title = plan.title if plan else "untitled program"
    passes = [f"{p.name} ({p.kind}): {p.description}" for p in plan.passes] if plan else []
    visuals = list(plan.key_visuals) if plan else []
    return _TEMPLATE.format(
        title=title, summary=plan.summary if plan else "", style=plan.style if plan else "",
        motion=plan.motion if plan else "animate with t", duration=plan.duration_s if plan else 8.0,
        passes=_comment_lines("pass", passes), visuals=_comment_lines("key visual", visuals),
    )


def write_skeleton(ws: Workspace, plan: Plan | None) -> list[Path]:
    gplan = plan if isinstance(plan, GraphicsPlan) else None
    ws.src.mkdir(parents=True, exist_ok=True)
    p = ws.src / "program.py"
    p.write_text(program_source(gplan))
    return [p]


# ===================================================================== runtime
class OpenGLPythonRuntime(RuntimeLayout):
    language = Language.OPENGL_PYTHON
    entry_globs: tuple[str, ...] = (ENTRY_FILE[Language.OPENGL_PYTHON], "src/*.glsl")

    def __init__(self, *, host: GlHost | None = None):
        self._host = host

    # ------------------------------------------------------------------ skeleton / lint
    def skeleton(self, ws: Workspace, plan: Plan | None) -> list[Path]:
        return write_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        return lint_workspace(ws)

    # ------------------------------------------------------------------ build
    def host(self, timeout_s: float | None = None) -> GlHost:
        return make_host(self._host, timeout_s)

    def build(self, ws: Workspace, *, timeout_s: int | None = None, times: list[float] | None = None,
              preview: bool = True, width: int | None = None, height: int | None = None) -> BuildResult:
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        invalidate_stale_outputs(ws)  # BEFORE the MISSING_ENTRY return, so it also clears
        if (missing := missing_entry(ws, self.language)) is not None:
            return missing
        program = ws.root / PROGRAM
        plan = graphics_plan(ws)
        w, h = resolution_for(plan)
        if width and height:
            w, h = int(width), int(height)
        duration = plan.duration_s if plan else None
        res = self.host(timeout_s).run_program(program, ws.artifacts, width=w, height=h, times=times or judge_times(duration),
                                               extra_times=gif_times(duration or 8.0) if preview else (), cwd=ws.src)
        err_file, err_line, err_msg = "", None, None
        if not res.ok:
            err_file, err_line = traceback_location(res.traceback, program)
            err_msg = f"{res.stage} failed: {res.error_type}: {res.error_message}"
            glsl = parse_glsl_log(res.error_message)
            if glsl:
                res.error_type = "GlslCompileError"
                err_msg += "\n(GLSL line numbers refer to the shader STRING inside program.py: " + \
                           "; ".join(f"line {m.line}: {m.message}" for m in glsl[:8]) + ")"
        census = {"resolution": [w, h]}
        motion_expected = bool(plan.motion.strip()) if plan else True
        return finish_build(ws, res, language=self.language.value, error_file=err_file, error_line=err_line,
                            error_message=err_msg, census=census, motion_expected=motion_expected)
