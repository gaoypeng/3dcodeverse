"""glsl_shader language: Shadertoy-style fragment shaders rendered by moderngl."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from codeverse3d.contracts.artifacts import BuildResult, GateFinding, GateReport, Severity
from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.languages._common import line_of, missing_entry, ws_rel
from codeverse3d.languages._gl_common import (
    GlslMessage,
    LineMap,
    Segment,
    finish_build,
    graphics_plan,
    invalidate_stale_outputs,
    judge_times,
    make_host,
    parse_glsl_log,
    resolution_for,
)
from codeverse3d.languages.base import RuntimeLayout
from codeverse3d.spatial.gl_render import GlHost, gif_times
from codeverse3d.workspace import Workspace

# ===================================================================== wrap
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

#: One left-to-right scan: `//` inside a block comment and `/*` inside a line comment are comment text.
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_MAIN_IMAGE = re.compile(r"\bvoid\s+mainImage\s*\(")
_PLAIN_MAIN = re.compile(r"\bvoid\s+main\s*\(\s*(void)?\s*\)")


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

    return _COMMENT.sub(_blank, text)


def _unconditional(src: str) -> str:
    """The lines outside every ``#if``/``#ifdef``/``#ifndef`` … ``#endif`` block."""
    depth, keep = 0, []
    for line in src.splitlines():
        head = line.lstrip()
        if head.startswith(("#if", "# if")):
            depth += 1
        elif head.startswith(("#endif", "# endif")):
            depth = max(0, depth - 1)
        elif depth == 0:
            keep.append(line)
    return "\n".join(keep)


def detect_convention(shader_src: str) -> str:
    """``main`` when the author wrote a ``main()``, else ``mainImage`` (the harness appends the ``main()`` trailer)."""
    shader_src = strip_comments(shader_src)
    # a main() only inside #if/#ifdef (a STANDALONE guard) is not the entry point the harness compiles
    if _PLAIN_MAIN.search(_unconditional(shader_src)):
        return "main"
    if _MAIN_IMAGE.search(shader_src):
        return "mainImage"
    return "mainImage"  # trailer will surface "mainImage undeclared" at compile time → clear error


def _count(text: str) -> int:
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _piece(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def compose(shader_src: str, includes: Sequence[tuple[str, str]] = (), *,
            shader_file: str = "src/shader.frag") -> Composed:
    """Header + every include file (in the order given) + shader (+ trailer), with a line map back to the agent files."""
    convention = detect_convention(shader_src)
    pieces: list[tuple[str, str]] = [("harness", HEADER)]
    for name, text in includes:
        if text.strip():
            pieces.append((name, _piece(text)))
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
    code = "\n".join(strip_comments(t) for t in [shader_src, *(t for _, t in includes)])
    uses_feedback = "u_prev" in code or "iChannel0" in code
    return Composed(source=src, line_map=LineMap(segments), convention=convention, uses_feedback=uses_feedback)


def include_files(ws: Workspace) -> list[Path]:
    """The include files: every ``src/*.glsl``, ``common.glsl`` first, then file-name order — all pasted above the passes."""
    files = sorted(p for p in ws.src.glob("*.glsl") if p.is_file())
    return sorted(files, key=lambda p: (p.name != COMMON_NAME, p.name))


# --------------------------------------------------------------------------- compiler messages
# GlslMessage / LineMap / parse_glsl_log live in ``_gl_common``.
def first_error(messages: list[GlslMessage]) -> GlslMessage | None:
    for m in messages:
        if m.kind == "error":
            return m
    return None


# ===================================================================== lint
GATE = "lint:glsl_shader"
SHADER = ENTRY_FILE[Language.GLSL_SHADER]
COMMON_NAME = "common.glsl"
INCLUDES = "src/*.glsl"
BUFFER_A = "src/buffer_a.frag"
SAMPLERS = {"u_prev", "u_noise", "u_buffer_a", "iChannel0", "iChannel1"}
MAX_CHARS = 60_000

_VERSION = re.compile(r"^[ \t]*#[ \t]*version\b", re.M)
_INCLUDE = re.compile(r"^[ \t]*#[ \t]*include\b", re.M)
_UNIFORM_DECL = re.compile(r"\buniform\s+\w+\s+(\w+)")
_OUT_DECL = re.compile(r"^[ \t]*(?:layout\s*\([^)]*\)\s*)?out\s+vec4\s+(\w+)\s*;", re.M)
_TEXTURE = re.compile(r"\btexture(?:Lod|Grad|Proj)?\s*\(\s*(\w+)")
_MISSING_INPUTS = re.compile(r"\b(iChannel[23]|iChannelResolution|iChannelTime|iDate|iSampleRate|iFrameRate)\b")
_LEGACY = {
    "gl_FragColor": "write `fragColor` (declared by the harness) instead of gl_FragColor",
    "texture2D(": "use `texture(sampler, uv)` — GLSL 330 has no texture2D",
    "varying ": "fragment-only shader: no varyings; use gl_FragCoord / uniforms",
    "attribute ": "fragment-only shader: no attributes",
}
_FLOAT_LITERAL_MOD = re.compile(r"(\d\.\d*|\.\d+)\s*%|%\s*(\d\.\d*|\.\d+)")
_PRECISION = re.compile(r"^[ \t]*precision\s+(lowp|mediump|highp)\s+\w+\s*;", re.M)


def _check_file(rel: str, text: str, *, role: str) -> list[GateFinding]:
    out: list[GateFinding] = []
    raw_len = len(text)
    text = strip_comments(text)

    def add(sev: Severity, kind: str, msg: str, hint: str, line: int | None = None) -> None:
        out.append(GateFinding(gate=GATE, severity=sev, target=rel, message=(f"{rel}:{line}: " if line else f"{rel}: ") + msg,
                               fix_hint=hint, data={"kind": kind, "line": line, "file": rel}))

    if raw_len > MAX_CHARS:
        add(Severity.WARN, "too_long", f"file is {raw_len} chars; keep shaders compact",
            "remove dead code; split the layers into include files (src/*.glsl, one layer each)")
    for m in _VERSION.finditer(text):
        add(Severity.ERROR, "version_line", "contains a #version line; the harness prepends `#version 330 core`",
            "delete the #version line", line_of(text, m.start()))
    for m in _INCLUDE.finditer(text):
        add(Severity.ERROR, "include", "#include is not GLSL; every src/*.glsl is pasted in automatically",
            "delete the #include line; functions in src/*.glsl are already visible", line_of(text, m.start()))
    for m in _UNIFORM_DECL.finditer(text):
        name = m.group(1)
        if name in UNIFORM_NAMES:
            add(Severity.ERROR, "redeclared_uniform", f"redeclares harness uniform `{name}`",
                f"delete the declaration; `{name}` is provided by the harness header", line_of(text, m.start()))
        else:
            add(Severity.ERROR, "custom_uniform", f"declares uniform `{name}` which nothing will set",
                "only u_time / u_resolution / u_mouse / u_frame / u_prev / u_noise / u_buffer_a exist; turn it into a `const`",
                line_of(text, m.start()))
    for m in _OUT_DECL.finditer(text):
        add(Severity.ERROR, "redeclared_output", f"declares output `{m.group(1)}`; the harness already declares `out vec4 fragColor`",
            "delete the `out vec4 ...;` line and write to fragColor", line_of(text, m.start()))
    for m in _TEXTURE.finditer(text):
        name = m.group(1)
        if name not in SAMPLERS:
            add(Severity.ERROR, "undeclared_sampler", f"texture() reads sampler `{name}` which does not exist",
                "only u_prev (previous frame), u_noise (256² RGBA noise) and u_buffer_a (buffer pass) can be sampled; "
                "generate patterns procedurally (hash / noise / the u_noise texture)", line_of(text, m.start()))
    for m in _MISSING_INPUTS.finditer(text):
        add(Severity.ERROR, "missing_input", f"`{m.group(1)}` is not available in this harness",
            "available: u_time/iTime, u_resolution/iResolution, u_frame/iFrame, u_mouse (always 0), u_prev/iChannel0, u_noise/iChannel1",
            line_of(text, m.start()))
    for needle, hint in _LEGACY.items():
        pos = text.find(needle)
        if pos >= 0:
            add(Severity.ERROR, "legacy_glsl", f"uses `{needle.strip()}` (GLSL 1.x)", hint, line_of(text, pos))
    for m in _FLOAT_LITERAL_MOD.finditer(text):
        add(Severity.ERROR, "float_modulo", "`%` on a float literal — GLSL `%` is integer-only", "use mod(x, y)", line_of(text, m.start()))
    if role in ("shader", "buffer_a"):
        if not _MAIN_IMAGE.search(text) and not _PLAIN_MAIN.search(text):
            add(Severity.ERROR, "no_entry", "neither `void mainImage(out vec4 fragColor, in vec2 fragCoord)` nor `void main()` found",
                "define `void mainImage(out vec4 fragColor, in vec2 fragCoord) { ... fragColor = vec4(col, 1.0); }`")
        if "fragColor" not in text and "gl_FragColor" not in text:
            add(Severity.ERROR, "no_output", "never writes fragColor", "end mainImage()/main() with `fragColor = vec4(col, 1.0);`")
    if role == "include" and (_MAIN_IMAGE.search(text) or _PLAIN_MAIN.search(text)):
        add(Severity.ERROR, "entry_in_include", f"{rel} must not define main()/mainImage()",
            "move the entry point to src/shader.frag; an include file holds only functions and constants")
    if role == "buffer_a" and "u_buffer_a" in text:
        add(Severity.WARN, "buffer_self_ref", "buffer_a.frag samples u_buffer_a; inside the buffer pass the previous buffer is `u_prev`",
            "use texture(u_prev, uv) for feedback inside buffer_a.frag")
    for m in _PRECISION.finditer(text):
        add(Severity.INFO, "precision", "precision qualifier is ignored on desktop GLSL 330", "safe to delete", line_of(text, m.start()))
    if "u_mouse" in text or "iMouse" in text:
        add(Severity.INFO, "mouse", "u_mouse is always (0,0) when rendered headless", "do not depend on the mouse for the look")
    return out


def lint_workspace(ws: Workspace) -> GateReport:
    findings: list[GateFinding] = []
    shader = ws.root / SHADER
    if not shader.is_file():
        findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, target=SHADER, message=f"{SHADER} is missing",
                                    fix_hint="create src/shader.frag with a mainImage(out vec4 fragColor, in vec2 fragCoord) function",
                                    data={"kind": "missing_entry", "file": SHADER}))
    else:
        findings.extend(_check_file(SHADER, shader.read_text(errors="replace"), role="shader"))
    for inc in include_files(ws):
        findings.extend(_check_file(ws_rel(ws, inc), inc.read_text(errors="replace"), role="include"))
    buf = ws.root / BUFFER_A
    if buf.is_file():
        findings.extend(_check_file(BUFFER_A, buf.read_text(errors="replace"), role="buffer_a"))
    contract = {SHADER, BUFFER_A, *(ws_rel(ws, p) for p in include_files(ws))}
    stray = [ws_rel(ws, p) for p in ws.src.rglob("*") if p.is_file()
             and ws_rel(ws, p) not in contract
             and p.suffix in (".frag", ".glsl", ".vert", ".py", ".js")]
    for rel in stray:
        findings.append(GateFinding(gate=GATE, severity=Severity.WARN, target=rel, message=f"{rel} is not part of the shader contract and is ignored",
                                    fix_hint="keep code in src/shader.frag, src/*.glsl include files and src/buffer_a.frag", data={"kind": "stray_file", "file": rel}))
    return GateReport.of(GATE, findings)


# ===================================================================== skeleton


# ===================================================================== runtime
class GlslShaderRuntime(RuntimeLayout):
    language = Language.GLSL_SHADER
    entry_globs: tuple[str, ...] = (ENTRY_FILE[Language.GLSL_SHADER], INCLUDES, BUFFER_A)

    def __init__(self, *, host: GlHost | None = None):
        self._host = host


    def lint(self, ws: Workspace) -> GateReport:
        return lint_workspace(ws)

    # ------------------------------------------------------------------ build
    def host(self, timeout_s: float | None = None) -> GlHost:
        return make_host(self._host, timeout_s)

    def compose_sources(self, ws: Workspace) -> tuple[Composed, Composed | None]:
        shader = (ws.root / SHADER).read_text(errors="replace")
        includes = [(ws_rel(ws, p), p.read_text(errors="replace")) for p in include_files(ws)]
        image = compose(shader, includes)
        buf_p = ws.root / BUFFER_A
        buffer_a = compose(buf_p.read_text(errors="replace"), includes, shader_file=BUFFER_A) if buf_p.is_file() else None
        return image, buffer_a

    def build(self, ws: Workspace, *, timeout_s: int | None = None, times: list[float] | None = None,
              preview: bool = True, width: int | None = None, height: int | None = None) -> BuildResult:
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        invalidate_stale_outputs(ws)
        if (missing := missing_entry(ws, self.language)) is not None:
            return missing
        plan = graphics_plan(ws)
        w, h = resolution_for(plan)
        if width and height:
            w, h = int(width), int(height)
        duration = plan.duration_s if plan else None
        image, buffer_a = self.compose_sources(ws)
        host = self.host(timeout_s)
        res = host.render_fragment_shader(
            image.source, ws.artifacts, width=w, height=h, times=times or judge_times(duration),
            buffer_a_src=buffer_a.source if buffer_a else None, feedback=image.uses_feedback or buffer_a is not None,
            extra_times=gif_times(duration or 8.0) if preview else (),
        )
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
                            error_message=err_msg, motion_expected=motion_expected)
