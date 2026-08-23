"""OpenGLPythonRuntime: skeleton → AST lint → build (import + render frames in a moderngl subprocess).

``src/program.py`` defines ``setup(ctx, width, height) -> state`` and
``render(ctx, state, t, frame, fbo)``.  The harness runner imports it by path
(cwd = ``src/``), binds + clears a float RGBA framebuffer per frame, calls
``render`` at the judged times, reads back (y-flipped) PNGs, detects NaN/Inf.
Python exceptions are mapped to ``src/program.py:LINE``; GLSL compile errors
inside the program surface as moderngl ``Error`` with the driver's info log.
"""

from __future__ import annotations

from pathlib import Path

from codeverse.config import get_settings
from codeverse.contracts.artifacts import BuildResult, GateReport
from codeverse.contracts.common import Language
from codeverse.contracts.plan import Plan
from codeverse.languages.glsl_shader.gl_build import (
    finish_build,
    judge_times,
    load_plan,
    preview_times,
    resolution_for,
    traceback_location,
)
from codeverse.languages.glsl_shader.wrap import parse_glsl_log
from codeverse.languages.opengl_python.lint import PROGRAM, lint_workspace
from codeverse.languages.opengl_python.skeleton import write_skeleton
from codeverse.prompts import PROMPTS_DIR, load_text
from codeverse.spatial.gl_render import GlHost, GlResult
from codeverse.workspace import Workspace

CONTRACT_FALLBACK = """src/program.py — raw moderngl program: `def setup(ctx, width, height) -> state` (programs, VAOs, textures,
FBOs; GLSL 330 core inline strings) and `def render(ctx, state, t, frame, fbo)` (draw the frame at time t into the given,
already bound framebuffer; multi-pass via your own FBOs, finish with fbo.use()).  No window, no context creation, no clock,
no file IO (except src/*.glsl next to program.py).  Imports: moderngl, numpy, math, random, struct, array, pathlib."""


class OpenGLPythonRuntime:
    language = Language.OPENGL_PYTHON
    entry_globs: tuple[str, ...] = ("src/program.py", "src/*.glsl")

    def __init__(self, *, host: GlHost | None = None):
        self._host = host

    # ------------------------------------------------------------------ contract
    def contract_doc(self) -> str:
        try:
            return load_text("opengl_python/contract.md")
        except FileNotFoundError:
            return CONTRACT_FALLBACK

    def cookbook_path(self) -> Path:
        return PROMPTS_DIR / "opengl_python" / "cookbook.md"

    # ------------------------------------------------------------------ skeleton / lint
    def skeleton(self, ws: Workspace, plan: Plan | None) -> list[Path]:
        return write_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        return lint_workspace(ws)

    # ------------------------------------------------------------------ build
    def host(self, timeout_s: float | None = None) -> GlHost:
        if self._host is not None:
            return self._host
        settings = get_settings()
        return GlHost(gpu=settings.render.gpu, timeout_s=float(timeout_s or settings.limits.render_timeout_s))

    def build(self, ws: Workspace, *, timeout_s: int | None = None, times: list[float] | None = None,
              preview: bool = True, width: int | None = None, height: int | None = None) -> BuildResult:
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        program = ws.root / PROGRAM
        if not program.is_file():
            res = GlResult(ok=False, mode="program", stage="lint", error_type="MissingEntry", error_message=f"{PROGRAM} is missing")
            return finish_build(ws, res, language=self.language.value, error_file=PROGRAM)
        plan = load_plan(ws)
        w, h = resolution_for(plan)
        if width and height:
            w, h = int(width), int(height)
        duration = plan.duration_s if plan else None
        res = self.host(timeout_s).run_program(program, ws.artifacts, width=w, height=h, times=times or judge_times(duration),
                                               extra_times=preview_times(duration) if preview else (), cwd=ws.src)
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
