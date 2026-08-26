"""GlslShaderRuntime: skeleton → lint → build (moderngl frames) for Shadertoy-style shaders.

``src/shader.frag`` (+ optional ``src/common.glsl``, ``src/buffer_a.frag``, and the
harness-owned ``src/recipes.glsl`` when the track seeded recipes) is wrapped by
:mod:`wrap` (header with the uniform contract + main trampoline; recipes pasted
above common), compiled and rendered in a subprocess by :class:`GlHost` at the
plan's resolution for t ∈ {0, 1, 2.5, 4, 6}s (+ preview frames for the GIF).
Compile errors come back mapped to ``src/shader.frag:LINE`` (or common / recipes).
"""

from __future__ import annotations

from pathlib import Path

from codeverse.contracts.artifacts import BuildResult, GateReport
from codeverse.contracts.common import ENTRY_FILE, Language
from codeverse.contracts.plan import Plan
from codeverse.languages._gl_common import (
    finish_build,
    judge_times,
    load_plan,
    make_host,
    parse_glsl_log,
    preview_times,
    resolution_for,
)
from codeverse.languages.glsl_shader.lint import BUFFER_A, COMMON, RECIPES, SHADER, lint_workspace
from codeverse.languages.glsl_shader.skeleton import write_skeleton
from codeverse.languages.glsl_shader.wrap import Composed, compose, first_error
from codeverse.prompts import PROMPTS_DIR, load_text
from codeverse.spatial.gl_render import GlHost, GlResult
from codeverse.workspace import Workspace

CONTRACT_FALLBACK = """src/shader.frag — GLSL 330 fragment shader body (NO #version line, NO uniform declarations):
write `void mainImage(out vec4 fragColor, in vec2 fragCoord)` using the harness uniforms u_time, u_resolution,
u_mouse, u_frame, u_prev (previous frame), u_noise (256² noise), u_buffer_a (src/buffer_a.frag output).
Optional src/common.glsl (helpers, pasted in first) and src/buffer_a.frag (one feedback pass). Animate with u_time."""


class GlslShaderRuntime:
    language = Language.GLSL_SHADER
    entry_globs: tuple[str, ...] = (ENTRY_FILE[Language.GLSL_SHADER], COMMON, BUFFER_A)

    def __init__(self, *, host: GlHost | None = None):
        self._host = host

    # ------------------------------------------------------------------ contract
    def contract_doc(self) -> str:
        try:
            return load_text("glsl_shader/contract.md")
        except FileNotFoundError:
            return CONTRACT_FALLBACK

    def cookbook_path(self) -> Path:
        return PROMPTS_DIR / "glsl_shader" / "cookbook.md"

    # ------------------------------------------------------------------ skeleton / lint
    def skeleton(self, ws: Workspace, plan: Plan | None) -> list[Path]:
        return write_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        return lint_workspace(ws)

    # ------------------------------------------------------------------ build
    def host(self, timeout_s: float | None = None) -> GlHost:
        return make_host(self._host, timeout_s)

    def compose_sources(self, ws: Workspace) -> tuple[Composed, Composed | None]:
        shader = (ws.root / SHADER).read_text(errors="replace")
        common_p = ws.root / COMMON
        common = common_p.read_text(errors="replace") if common_p.is_file() else None
        recipes_p = ws.root / RECIPES
        recipes = recipes_p.read_text(errors="replace") if recipes_p.is_file() else None
        image = compose(shader, common, recipes_src=recipes)
        buf_p = ws.root / BUFFER_A
        buffer_a = (compose(buf_p.read_text(errors="replace"), common, recipes_src=recipes, shader_file=BUFFER_A)
                    if buf_p.is_file() else None)
        return image, buffer_a

    def build(self, ws: Workspace, *, timeout_s: int | None = None, times: list[float] | None = None,
              preview: bool = True, width: int | None = None, height: int | None = None) -> BuildResult:
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        if not (ws.root / SHADER).is_file():
            res = GlResult(ok=False, mode="shader", stage="lint", error_type="MissingEntry", error_message=f"{SHADER} is missing")
            return finish_build(ws, res, language=self.language.value, error_file=SHADER)
        plan = load_plan(ws)
        w, h = resolution_for(plan)
        if width and height:
            w, h = int(width), int(height)
        duration = plan.duration_s if plan else None
        image, buffer_a = self.compose_sources(ws)
        host = self.host(timeout_s)
        res = host.render_fragment_shader(
            image.source, ws.artifacts, width=w, height=h, times=times or judge_times(duration),
            buffer_a_src=buffer_a.source if buffer_a else None, feedback=image.uses_feedback or buffer_a is not None,
            extra_times=preview_times(duration) if preview else (),
        )
        census = {"convention": image.convention, "has_common": (ws.root / COMMON).is_file(), "has_buffer_a": buffer_a is not None,
                  "has_recipes": (ws.root / RECIPES).is_file(), "resolution": [w, h]}
        err_file, err_line, err_msg = "", None, None
        if not res.ok and res.stage in ("compile", "compile_buffer_a"):
            comp = buffer_a if res.stage == "compile_buffer_a" and buffer_a else image
            msgs = parse_glsl_log(res.error_message, comp.line_map)
            first = first_error(msgs)
            if first is not None:
                err_file, err_line = first.file, first.file_line
            listed = "\n".join(m.text() for m in msgs[:12]) or res.error_message.strip()
            err_msg = f"GLSL compile error ({'buffer_a' if res.stage == 'compile_buffer_a' else 'shader'}):\n{listed}"
            res.error_type = "GlslCompileError"
        motion_expected = bool(plan.motion.strip()) if plan else True
        return finish_build(ws, res, language=self.language.value, error_file=err_file, error_line=err_line,
                            error_message=err_msg, census=census, motion_expected=motion_expected)
