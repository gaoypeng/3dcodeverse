"""glsl_shader language: Shadertoy-style fragment shaders rendered by moderngl."""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from codeverse.contracts.artifacts import BuildResult, GateFinding, GateReport, Severity
from codeverse.contracts.common import ENTRY_FILE, Language
from codeverse.contracts.plan import GraphicsPlan, Plan
from codeverse.languages._gl_common import (  # noqa: F401 — re-exported
    GlslMessage,
    LineMap,
    Segment,
    finish_build,
    invalidate_stale_outputs,
    judge_times,
    load_plan,
    make_host,
    parse_glsl_log,
    preview_times,
    resolution_for,
)
from codeverse.prompts import PROMPTS_DIR, load_text
from codeverse.spatial.gl_render import GlHost, GlResult
from codeverse.workspace import Workspace

# ===================================================================== wrap
# (merged from codeverse/languages/glsl_shader/wrap.py, 2026-08-28)
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


# ===================================================================== lint
# (merged from codeverse/languages/glsl_shader/lint.py, 2026-08-28)
GATE = "lint:glsl_shader"
SHADER = "src/shader.frag"
COMMON = "src/common.glsl"
BUFFER_A = "src/buffer_a.frag"
#: harness-owned (``contracts.common.HARNESS_OWNED_SRC``): seeded cookbook recipes, pasted above common.glsl
RECIPES = "src/recipes.glsl"
#: a top-level GLSL function definition, ``group(1)`` = its name (match per line)
FUNC_DEF = re.compile(r"^[ \t]*(?:float|int|bool|void|[bi]?vec[234]|mat[234])\s+(\w+)\s*\(")
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
def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def defined_functions(text: str) -> dict[str, int]:
    """``{name: line}`` of every top-level function definition in a GLSL text (comments blanked)."""
    out: dict[str, int] = {}
    for i, line in enumerate(strip_comments(text).splitlines(), 1):
        if (m := FUNC_DEF.match(line)) and m.group(1) not in out:
            out[m.group(1)] = i
    return out


def _check_file(rel: str, text: str, *, role: str, recipe_names: Collection[str] = ()) -> list[GateFinding]:
    out: list[GateFinding] = []
    raw_len = len(text)
    text = strip_comments(text)

    def add(sev: Severity, kind: str, msg: str, hint: str, line: int | None = None) -> None:
        out.append(GateFinding(gate=GATE, severity=sev, target=rel, message=(f"{rel}:{line}: " if line else f"{rel}: ") + msg,
                               fix_hint=hint, data={"kind": kind, "line": line, "file": rel}))

    if raw_len > MAX_CHARS:
        add(Severity.WARN, "too_long", f"file is {raw_len} chars; keep shaders compact", "remove dead code; move helpers to src/common.glsl")
    for m in _VERSION.finditer(text):
        add(Severity.ERROR, "version_line", "contains a #version line; the harness prepends `#version 330 core`",
            "delete the #version line", _line_of(text, m.start()))
    for m in _INCLUDE.finditer(text):
        add(Severity.ERROR, "include", "#include is not GLSL; src/common.glsl is pasted in automatically",
            "delete the #include line; functions in src/common.glsl are already visible", _line_of(text, m.start()))
    for m in _UNIFORM_DECL.finditer(text):
        name = m.group(1)
        if name in UNIFORM_NAMES:
            add(Severity.ERROR, "redeclared_uniform", f"redeclares harness uniform `{name}`",
                f"delete the declaration; `{name}` is provided by the harness header", _line_of(text, m.start()))
        else:
            add(Severity.ERROR, "custom_uniform", f"declares uniform `{name}` which nothing will set",
                "only u_time / u_resolution / u_mouse / u_frame / u_prev / u_noise / u_buffer_a exist; turn it into a `const`",
                _line_of(text, m.start()))
    for m in _OUT_DECL.finditer(text):
        add(Severity.ERROR, "redeclared_output", f"declares output `{m.group(1)}`; the harness already declares `out vec4 fragColor`",
            "delete the `out vec4 ...;` line and write to fragColor", _line_of(text, m.start()))
    for m in _TEXTURE.finditer(text):
        name = m.group(1)
        if name not in SAMPLERS:
            add(Severity.ERROR, "undeclared_sampler", f"texture() reads sampler `{name}` which does not exist",
                "only u_prev (previous frame), u_noise (256² RGBA noise) and u_buffer_a (buffer pass) can be sampled; "
                "generate patterns procedurally (hash/noise/fbm from the cookbook)", _line_of(text, m.start()))
    for m in _MISSING_INPUTS.finditer(text):
        add(Severity.ERROR, "missing_input", f"`{m.group(1)}` is not available in this harness",
            "available: u_time/iTime, u_resolution/iResolution, u_frame/iFrame, u_mouse (always 0), u_prev/iChannel0, u_noise/iChannel1",
            _line_of(text, m.start()))
    for needle, hint in _LEGACY.items():
        pos = text.find(needle)
        if pos >= 0:
            add(Severity.ERROR, "legacy_glsl", f"uses `{needle.strip()}` (GLSL 1.x)", hint, _line_of(text, pos))
    for m in _FLOAT_LITERAL_MOD.finditer(text):
        add(Severity.ERROR, "float_modulo", "`%` on a float literal — GLSL `%` is integer-only", "use mod(x, y)", _line_of(text, m.start()))
    for name, line in defined_functions(text).items():
        if name in recipe_names:
            add(Severity.ERROR, "redefines_recipe", f"`{name}` is already provided by {RECIPES} — call it instead of redefining it",
                f"delete this `{name}` definition; {RECIPES} is harness-owned and pasted above your code, so its `{name}` is in scope", line)
    if role in ("shader", "buffer_a"):
        if not _MAIN_IMAGE.search(text) and not _PLAIN_MAIN.search(text):
            add(Severity.ERROR, "no_entry", "neither `void mainImage(out vec4 fragColor, in vec2 fragCoord)` nor `void main()` found",
                "define `void mainImage(out vec4 fragColor, in vec2 fragCoord) { ... fragColor = vec4(col, 1.0); }`")
        if "fragColor" not in text and "gl_FragColor" not in text:
            add(Severity.ERROR, "no_output", "never writes fragColor", "end mainImage()/main() with `fragColor = vec4(col, 1.0);`")
    if role == "common" and (_MAIN_IMAGE.search(text) or _PLAIN_MAIN.search(text)):
        add(Severity.ERROR, "entry_in_common", "src/common.glsl must not define main()/mainImage()",
            "move the entry point to src/shader.frag; keep only helper functions / constants in common.glsl")
    if role == "buffer_a" and "u_buffer_a" in text:
        add(Severity.WARN, "buffer_self_ref", "buffer_a.frag samples u_buffer_a; inside the buffer pass the previous buffer is `u_prev`",
            "use texture(u_prev, uv) for feedback inside buffer_a.frag")
    for m in _PRECISION.finditer(text):
        add(Severity.INFO, "precision", "precision qualifier is ignored on desktop GLSL 330", "safe to delete", _line_of(text, m.start()))
    if "u_mouse" in text or "iMouse" in text:
        add(Severity.INFO, "mouse", "u_mouse is always (0,0) when rendered headless", "do not depend on the mouse for the look")
    return out


def recipe_names(ws: Workspace) -> frozenset[str]:
    """The names the harness-owned ``src/recipes.glsl`` provides (empty when nothing was seeded)."""
    p = ws.root / RECIPES
    return frozenset(defined_functions(p.read_text(errors="replace"))) if p.is_file() else frozenset()


def lint_workspace(ws: Workspace) -> GateReport:
    findings: list[GateFinding] = []
    reserved = recipe_names(ws)
    shader = ws.root / SHADER
    if not shader.is_file():
        findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, target=SHADER, message=f"{SHADER} is missing",
                                    fix_hint="create src/shader.frag with a mainImage(out vec4 fragColor, in vec2 fragCoord) function",
                                    data={"kind": "missing_entry", "file": SHADER}))
    else:
        findings.extend(_check_file(SHADER, shader.read_text(errors="replace"), role="shader", recipe_names=reserved))
    for rel, role in ((COMMON, "common"), (BUFFER_A, "buffer_a")):
        p = ws.root / rel
        if p.is_file():
            findings.extend(_check_file(rel, p.read_text(errors="replace"), role=role, recipe_names=reserved))
    stray = [str(p.relative_to(ws.root)) for p in ws.src.rglob("*") if p.is_file()
             and str(p.relative_to(ws.root)) not in (SHADER, COMMON, BUFFER_A, RECIPES) and p.suffix in (".frag", ".glsl", ".vert", ".py", ".js")]
    for rel in stray:
        findings.append(GateFinding(gate=GATE, severity=Severity.WARN, target=rel, message=f"{rel} is not part of the shader contract and is ignored",
                                    fix_hint="keep code in src/shader.frag (+ src/common.glsl, src/buffer_a.frag)", data={"kind": "stray_file", "file": rel}))
    passed = not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=GATE, passed=passed, findings=findings)


def lint_text(shader_src: str, common_src: str | None = None, buffer_a_src: str | None = None, *,
              recipes_src: str | None = None) -> list[GateFinding]:
    """Lint in-memory sources (tools / tests); ``recipes_src`` is the harness-owned recipe file whose
    names the agent files may not redefine (it is not linted itself)."""
    reserved = frozenset(defined_functions(recipes_src)) if recipes_src else frozenset()
    out = _check_file(SHADER, shader_src, role="shader", recipe_names=reserved)
    if common_src is not None:
        out += _check_file(COMMON, common_src, role="common", recipe_names=reserved)
    if buffer_a_src is not None:
        out += _check_file(BUFFER_A, buffer_a_src, role="buffer_a", recipe_names=reserved)
    return out


# ===================================================================== skeleton
# (merged from codeverse/languages/glsl_shader/skeleton.py, 2026-08-28)
COMMON_GLSL = """// src/common.glsl — helpers pasted above shader.frag by the harness (no #include needed).
// Keep ONLY functions / constants here; no main(), no uniforms, no #version.
#define PI 3.14159265359
#define TAU 6.28318530718

float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash12(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
vec2  hash22(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973)); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.xx + p3.yz) * p3.zy); }

// value noise 2D (smooth, 0..1)
float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash12(i), hash12(i + vec2(1, 0)), u.x),
               mix(hash12(i + vec2(0, 1)), hash12(i + vec2(1, 1)), u.x), u.y);
}

// fractal brownian motion, 5 octaves (constant loop bound)
float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    mat2 rot = mat2(0.8, 0.6, -0.6, 0.8);
    for (int i = 0; i < 5; i++) { v += a * noise(p); p = rot * p * 2.02; a *= 0.5; }
    return v;
}

mat2 rot2(float a) { float c = cos(a), s = sin(a); return mat2(c, -s, s, c); }

// cosine palette (IQ): t in 0..1
vec3 palette(float t, vec3 a, vec3 b, vec3 c, vec3 d) { return a + b * cos(TAU * (c * t + d)); }

// filmic-ish tonemap + gamma
vec3 tonemap(vec3 c) { c = c / (1.0 + c); return pow(clamp(c, 0.0, 1.0), vec3(1.0 / 2.2)); }
"""

_SHADER_TEMPLATE = """// src/shader.frag — {title}
// CONTRACT (the harness prepends `#version 330 core` + these uniforms + `out vec4 fragColor`; DO NOT redeclare them):
//   uniform float u_time;  uniform vec2 u_resolution;  uniform vec2 u_mouse;  uniform int u_frame;
//   uniform sampler2D u_prev;  (previous frame of this pass)   uniform sampler2D u_noise;  (256x256 RGBA noise, repeat)
//   uniform sampler2D u_buffer_a;  (output of src/buffer_a.frag when that file exists)
//   iTime / iResolution / iFrame / iMouse / iChannel0(=u_prev) / iChannel1(=u_noise) are #defined for Shadertoy ports.
// Entry: void mainImage(out vec4 fragColor, in vec2 fragCoord) — fragCoord in pixels, origin BOTTOM-LEFT.
// Helpers available from src/common.glsl: hash11 hash12 hash22 noise fbm rot2 palette tonemap PI TAU.
// Duration {duration:g}s; judged frames at t = 0, 1, 2.5, 4, 6 s.  Everything must MOVE with u_time.
//
// PLAN — {summary}
// Style: {style}
{passes}
{visuals}
// Motion: {motion}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {{
    vec2 uv = fragCoord / u_resolution.xy;                 // 0..1
    vec2 p  = (fragCoord - 0.5 * u_resolution.xy) / u_resolution.y;  // aspect-correct, centred
    float t = u_time;

    // TODO replace this placeholder look with the plan's passes above
    vec3 sky = mix(vec3(0.95, 0.55, 0.25), vec3(0.10, 0.20, 0.45), smoothstep(-0.2, 0.6, p.y));
    vec2 sunPos = vec2(0.35 * cos(t * 0.3), 0.15 + 0.1 * sin(t * 0.3));
    float sun = exp(-40.0 * length(p - sunPos));
    float haze = fbm(p * 3.0 + vec2(t * 0.15, 0.0));
    vec3 col = sky + vec3(1.0, 0.8, 0.5) * sun + 0.25 * haze * vec3(0.9, 0.7, 0.6);
    col *= 1.0 - 0.35 * length(p);                        // vignette
    fragColor = vec4(tonemap(col), 1.0);
}}
"""


def _comment_lines(prefix: str, items: list[str]) -> str:
    if not items:
        return f"// {prefix}: (none)"
    return "\n".join(f"//   TODO {prefix} {i + 1}: {x}" for i, x in enumerate(items))


def shader_source(plan: GraphicsPlan | None) -> str:
    title = plan.title if plan else "untitled shader"
    summary = plan.summary if plan else ""
    style = plan.style if plan else ""
    motion = plan.motion if plan else "animate with u_time"
    duration = plan.duration_s if plan else 8.0
    passes = [f"{p.name} ({p.kind}): {p.description}" for p in plan.passes] if plan else []
    visuals = list(plan.key_visuals) if plan else []
    return _SHADER_TEMPLATE.format(
        title=title, summary=summary, style=style, motion=motion, duration=duration,
        passes=_comment_lines("pass", passes), visuals=_comment_lines("key visual", visuals),
    )


def write_skeleton(ws: Workspace, plan: Plan | None) -> list[Path]:
    gplan = plan if isinstance(plan, GraphicsPlan) else None
    ws.src.mkdir(parents=True, exist_ok=True)
    common = ws.src / "common.glsl"
    shader = ws.src / "shader.frag"
    common.write_text(COMMON_GLSL)
    shader.write_text(shader_source(gplan))
    return [common, shader]


# ===================================================================== runtime
# (merged from codeverse/languages/glsl_shader/runtime.py, 2026-08-28)
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
        invalidate_stale_outputs(ws)  # BEFORE the MissingEntry return, so it also clears
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
