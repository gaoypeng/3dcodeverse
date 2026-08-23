"""Wrap an agent's Shadertoy-style fragment shader into a compilable GLSL 330 program.

The harness owns the ``#version`` line, the uniform block and the ``main``
trampoline; the agent writes only the body (``mainImage(out vec4 fragColor,
in vec2 fragCoord)`` or a plain ``main()`` writing the harness-declared ``fragColor``).
``compose`` returns the full source plus a :class:`LineMap` so compiler
messages (``0:LINE(COL)`` / ``0(LINE)`` / ``ERROR: 0:LINE:`` variants) map back
to ``src/shader.frag`` / ``src/common.glsl`` line numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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


@dataclass(frozen=True)
class Segment:
    file: str  # "src/shader.frag" | "src/common.glsl" | "harness"
    start: int  # 1-based first line in the composed source
    n_lines: int

    @property
    def end(self) -> int:
        return self.start + self.n_lines - 1


@dataclass
class LineMap:
    segments: list[Segment] = field(default_factory=list)

    def locate(self, line: int) -> tuple[str, int]:
        """Composed line → (file, line-in-file); harness lines map to ("harness", line)."""
        for s in self.segments:
            if s.start <= line <= s.end:
                return s.file, line - s.start + 1
        return "harness", line


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


def compose(shader_src: str, common_src: str | None = None, *, shader_file: str = "src/shader.frag",
            common_file: str = "src/common.glsl") -> Composed:
    """Header + common + shader (+ trailer) with a line map back to the agent files."""
    convention = detect_convention(shader_src)
    pieces: list[tuple[str, str]] = [("harness", HEADER)]
    if common_src and common_src.strip():
        body = common_src if common_src.endswith("\n") else common_src + "\n"
        pieces.append((common_file, body))
    body = shader_src if shader_src.endswith("\n") else shader_src + "\n"
    pieces.append((shader_file, body))
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
    code = strip_comments(shader_src) + "\n" + strip_comments(common_src or "")
    uses_feedback = "u_prev" in code or "iChannel0" in code
    return Composed(source=src, line_map=LineMap(segments), convention=convention, uses_feedback=uses_feedback)


# --------------------------------------------------------------------------- compiler messages
# Mesa: "0:12(5): error: `foo' undeclared" · NVIDIA: "0(12) : error C1008: ..." · ANGLE/ES: "ERROR: 0:12: 'foo' : undeclared"
_MSG_PATTERNS = (
    re.compile(r"^\s*(?P<src>\d+):(?P<line>\d+)\((?P<col>\d+)\):\s*(?P<kind>error|warning):\s*(?P<msg>.*)$"),
    re.compile(r"^\s*(?P<src>\d+)\((?P<line>\d+)\)\s*:\s*(?P<kind>error|warning)\s*(?P<msg>.*)$"),
    re.compile(r"^\s*(?P<kind>ERROR|WARNING):\s*(?P<src>\d+):(?P<line>\d+):\s*(?P<msg>.*)$"),
)


@dataclass(frozen=True)
class GlslMessage:
    kind: str  # error | warning
    line: int  # composed line
    message: str
    file: str = ""
    file_line: int = 0

    def text(self) -> str:
        loc = f"{self.file}:{self.file_line}" if self.file else f"line {self.line}"
        return f"{loc}: {self.kind}: {self.message}"


def parse_glsl_log(log: str, line_map: LineMap | None = None) -> list[GlslMessage]:
    """Parse a GLSL info log (any driver dialect) into messages mapped to agent files."""
    out: list[GlslMessage] = []
    for raw in (log or "").splitlines():
        for pat in _MSG_PATTERNS:
            m = pat.match(raw)
            if not m:
                continue
            line = int(m.group("line"))
            kind = m.group("kind").lower()
            msg = m.group("msg").strip()
            f, fl = line_map.locate(line) if line_map else ("", 0)
            out.append(GlslMessage(kind=kind, line=line, message=msg, file=f, file_line=fl))
            break
    return out


def first_error(messages: list[GlslMessage]) -> GlslMessage | None:
    for m in messages:
        if m.kind == "error":
            return m
    return None
