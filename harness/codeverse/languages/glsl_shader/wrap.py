"""Wrap an agent's Shadertoy-style fragment shader into a compilable GLSL 330 program.

The harness owns the ``#version`` line, the uniform block and the ``main``
trampoline; the agent writes only the body (``mainImage(out vec4 fragColor,
in vec2 fragCoord)`` or a plain ``main()`` writing the harness-declared ``fragColor``).
``compose`` pastes harness header < ``src/recipes.glsl`` (harness-owned seeded
recipes, when present) < ``src/common.glsl`` < ``src/shader.frag`` (+ trailer) and
returns the full source plus a :class:`LineMap` so compiler messages
(``0:LINE(COL)`` / ``0(LINE)`` / ``ERROR: 0:LINE:`` variants) map back to
``src/shader.frag`` / ``src/common.glsl`` / ``src/recipes.glsl`` line numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from codeverse.languages._gl_common import (  # noqa: F401 — re-exported
    GlslMessage,
    LineMap,
    Segment,
    parse_glsl_log,
)

UNIFORM_NAMES: tuple[str, ...] = ("u_time", "u_resolution", "u_mouse", "u_frame", "u_prev", "u_noise", "u_buffer_a")

HEADER = """#version 330 core
// ---- harness header (do not write these lines yourself) ----
uniform float u_time;        // seconds
uniform vec2  u_resolution;  // pixels
uniform vec2  u_mouse;       // pixels (always 0,0 headless)
uniform int   u_frame;       // frame counter
uniform sampler2D u_prev;    // previous frame of THIS pass (feedback)
uniform sampler2D u_noise;   // 256x256 RGBA white noise, repeat, linear
uniform sampler2D u_buffer_a;// output of src/buffer_a.frag (when present)
#define iTime u_time
#define iResolution vec3(u_resolution, 1.0)
#define iFrame u_frame
#define iMouse vec4(u_mouse, 0.0, 0.0)
#define iTimeDelta (1.0/30.0)
#define iChannel0 u_prev
#define iChannel1 u_noise
out vec4 fragColor;          // the ONLY output; write it in mainImage()/main()
"""

MAIN_IMAGE_TRAILER = """
// ---- harness trailer ----
void main() { mainImage(fragColor, gl_FragCoord.xy); }
"""

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_MAIN_IMAGE = re.compile(r"\bvoid\s+mainImage\s*\(")
_PLAIN_MAIN = re.compile(r"\bvoid\s+main\s*\(\s*(void)?\s*\)")
_OUT_DECL = re.compile(r"\bout\s+vec4\s+\w+\s*;")


@dataclass
class Composed:
    source: str
    line_map: LineMap
    convention: str  # "mainImage" | "main"
    uses_feedback: bool


def strip_comments(text: str) -> str:
    """Blank out comments but keep every newline so line numbers stay valid."""
    def _blank(m: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    return _LINE_COMMENT.sub(_blank, _BLOCK_COMMENT.sub(_blank, text))


def detect_convention(shader_src: str) -> str:
    shader_src = strip_comments(shader_src)
    if _MAIN_IMAGE.search(shader_src):
        return "mainImage"
    if _PLAIN_MAIN.search(shader_src):
        return "main"
    return "mainImage"  # trailer will surface "mainImage undeclared" at compile time → clear error


def _count(text: str) -> int:
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _piece(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def compose(shader_src: str, common_src: str | None = None, *, recipes_src: str | None = None,
            shader_file: str = "src/shader.frag", common_file: str = "src/common.glsl",
            recipes_file: str = "src/recipes.glsl") -> Composed:
    """Header + recipes (harness-owned, first: self-contained, nothing in common.glsl can shadow it)
    + common + shader (+ trailer) with a line map back to the agent files."""
    convention = detect_convention(shader_src)
    pieces: list[tuple[str, str]] = [("harness", HEADER)]
    if recipes_src and recipes_src.strip():
        pieces.append((recipes_file, _piece(recipes_src)))
    if common_src and common_src.strip():
        pieces.append((common_file, _piece(common_src)))
    pieces.append((shader_file, _piece(shader_src)))
    if convention == "mainImage":
        pieces.append(("harness", MAIN_IMAGE_TRAILER))
    segments: list[Segment] = []
    cursor = 1
    out: list[str] = []
    for name, text in pieces:
        n = _count(text)
        segments.append(Segment(name, cursor, n))
        cursor += n
        out.append(text)
    src = "".join(out)
    code = strip_comments(shader_src) + "\n" + strip_comments(common_src or "") + "\n" + strip_comments(recipes_src or "")
    uses_feedback = "u_prev" in code or "iChannel0" in code
    return Composed(source=src, line_map=LineMap(segments), convention=convention, uses_feedback=uses_feedback)


# --------------------------------------------------------------------------- compiler messages
# GlslMessage / LineMap / parse_glsl_log live in ``_gl_common`` (shared with opengl_python)
# and are re-exported above; only source composition + first_error remain here.
def first_error(messages: list[GlslMessage]) -> GlslMessage | None:
    for m in messages:
        if m.kind == "error":
            return m
    return None
