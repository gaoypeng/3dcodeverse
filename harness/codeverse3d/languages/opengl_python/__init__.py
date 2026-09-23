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
from codeverse3d.languages._common import MISSING_ENTRY, ws_rel
from codeverse3d.languages._gl_common import (
    finish_build,
    invalidate_stale_outputs,
    judge_times,
    load_plan,
    make_host,
    parse_glsl_log,
    resolution_for,
    traceback_location,
)
from codeverse3d.languages.base import RuntimeLayout
from codeverse3d.spatial.gl_render import GlHost, GlResult, gif_times
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
        findings.append(_finding(Severity.WARN, "unseeded_random", "uses random.* without random.seed(...)",
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
            findings.append(GateFinding(gate=GATE, severity=Severity.WARN, target=rel, message=f"{rel}: extra python module is ignored by the harness",
                                        fix_hint="keep all code in src/program.py (GLSL may live in src/*.glsl)", data={"kind": "stray_file", "file": rel}))
    return GateReport.of(GATE, findings)


# ===================================================================== skeleton
_TEMPLATE = '''"""src/program.py — {title}

CONTRACT (the harness imports this module in a headless moderngl process):
  def setup(ctx, width, height) -> state     create programs / VAOs / textures / FBOs ONCE; return any object
  def render(ctx, state, t, frame, fbo)      draw the frame at time t (seconds) into `fbo` (already bound + cleared)
* `ctx` is a moderngl.Context (GL 3.3 core).  Never create a context or window; never read the clock.
* Multi-pass: render into your own FBOs, then call `fbo.use()` and draw the final image into it.
* Deterministic: same t → same frame.  Seed randomness.  GLSL strings use `#version 330 core`.
* Allowed imports: moderngl, numpy, math, random, struct, array, pathlib (+typing/dataclasses).
Duration {duration:g}s; judged frames at t = 0, 1, 2.5, 4, 6 s — everything must MOVE with t.

PLAN — {summary}
Style: {style}
{passes}
{visuals}
Motion: {motion}
"""
import math

import moderngl
import numpy as np

BACKGROUND_VERT = """#version 330 core
in vec2 in_pos;
out vec2 v_uv;
void main() {{ v_uv = in_pos * 0.5 + 0.5; gl_Position = vec4(in_pos, 0.0, 1.0); }}
"""
BACKGROUND_FRAG = """#version 330 core
uniform float u_time;
in vec2 v_uv;
out vec4 fragColor;
void main() {{
    vec3 top = vec3(0.08, 0.10, 0.25), bottom = vec3(0.85, 0.45, 0.30);
    vec3 col = mix(bottom, top, smoothstep(0.0, 1.0, v_uv.y + 0.1 * sin(u_time * 0.5 + v_uv.x * 6.0)));
    fragColor = vec4(col, 1.0);
}}
"""
CUBE_VERT = """#version 330 core
uniform mat4 u_mvp;
uniform mat4 u_model;
in vec3 in_pos;
in vec3 in_normal;
out vec3 v_normal;
void main() {{ v_normal = mat3(u_model) * in_normal; gl_Position = u_mvp * vec4(in_pos, 1.0); }}
"""
CUBE_FRAG = """#version 330 core
uniform vec3 u_color;
in vec3 v_normal;
out vec4 fragColor;
void main() {{
    vec3 n = normalize(v_normal);
    float diff = max(dot(n, normalize(vec3(0.4, 0.8, 0.5))), 0.0);
    fragColor = vec4(u_color * (0.25 + 0.75 * diff), 1.0);
}}
"""


def _cube() -> np.ndarray:
    """36 vertices: position (3) + normal (3), interleaved float32."""
    faces = [((0, 0, 1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)),
             ((0, 0, -1), (1, -1, -1), (-1, -1, -1), (-1, 1, -1), (1, 1, -1)),
             ((1, 0, 0), (1, -1, 1), (1, -1, -1), (1, 1, -1), (1, 1, 1)),
             ((-1, 0, 0), (-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1)),
             ((0, 1, 0), (-1, 1, 1), (1, 1, 1), (1, 1, -1), (-1, 1, -1)),
             ((0, -1, 0), (-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1))]
    out = []
    for n, a, b, c, d in faces:
        for v in (a, b, c, a, c, d):
            out.extend(v)
            out.extend(n)
    return np.array(out, dtype="f4") * np.tile([0.5, 0.5, 0.5, 1, 1, 1], 36).astype("f4")


def _perspective(fov_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fov_deg) / 2.0)
    m = np.zeros((4, 4), dtype="f4")
    m[0, 0], m[1, 1] = f / aspect, f
    m[2, 2], m[2, 3] = (far + near) / (near - far), (2 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def _look_at(eye, target, up=(0, 1, 0)) -> np.ndarray:
    eye, target, up = np.array(eye, dtype="f4"), np.array(target, dtype="f4"), np.array(up, dtype="f4")
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.identity(4, dtype="f4")
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def _rot_y(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]], dtype="f4")


def setup(ctx: moderngl.Context, width: int, height: int):
    bg_prog = ctx.program(vertex_shader=BACKGROUND_VERT, fragment_shader=BACKGROUND_FRAG)
    quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
    bg_vao = ctx.vertex_array(bg_prog, [(quad, "2f", "in_pos")])
    cube_prog = ctx.program(vertex_shader=CUBE_VERT, fragment_shader=CUBE_FRAG)
    vbo = ctx.buffer(_cube().tobytes())
    cube_vao = ctx.vertex_array(cube_prog, [(vbo, "3f 3f", "in_pos", "in_normal")])
    proj = _perspective(45.0, width / height, 0.1, 100.0)
    view = _look_at((0.0, 1.2, 3.0), (0.0, 0.0, 0.0))
    # TODO build the plan's passes here (instancing, extra FBOs for post-processing, textures)
    return {{"bg_prog": bg_prog, "bg_vao": bg_vao, "cube_prog": cube_prog, "cube_vao": cube_vao, "proj": proj, "view": view}}


def render(ctx: moderngl.Context, state, t: float, frame: int, fbo: moderngl.Framebuffer) -> None:
    fbo.use()
    ctx.disable(moderngl.DEPTH_TEST)
    state["bg_prog"]["u_time"].value = t
    state["bg_vao"].render(mode=moderngl.TRIANGLE_STRIP)
    ctx.enable(moderngl.DEPTH_TEST)
    model = _rot_y(t * 0.8)
    mvp = state["proj"] @ state["view"] @ model
    state["cube_prog"]["u_mvp"].write(mvp.T.astype("f4").tobytes())   # column-major for GLSL
    state["cube_prog"]["u_model"].write(model.T.astype("f4").tobytes())
    state["cube_prog"]["u_color"].value = (0.9, 0.6, 0.2)
    state["cube_vao"].render(mode=moderngl.TRIANGLES)
    # TODO replace the placeholder cube with the plan's content; keep drawing into `fbo` last
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
        program = ws.root / PROGRAM
        if not program.is_file():
            res = GlResult(ok=False, mode="program", stage="lint", error_type=MISSING_ENTRY, error_message=f"{PROGRAM} is missing")
            return finish_build(ws, res, language=self.language.value, error_file=PROGRAM)
        plan = load_plan(ws)
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
