"""Static checks for ``src/shader.frag`` (+ ``common.glsl`` / ``buffer_a.frag``).

Catches what the compiler reports confusingly (or too late): a ``#version`` line
(the harness prepends one), redeclared harness uniforms / outputs, Shadertoy
inputs that do not exist here (``iChannel2``, ``iDate`` …), ``texture()`` on
undeclared samplers, GLSL 1.x syntax (``gl_FragColor`` / ``varying`` /
``texture2D``), ``%`` on floats, a missing ``mainImage``/``main``.
Gate name ``lint:glsl_shader``; ``data["kind"]`` names the rule.
"""

from __future__ import annotations

import re

from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.languages.glsl_shader.wrap import (
    _MAIN_IMAGE,
    _PLAIN_MAIN,
    UNIFORM_NAMES,
    strip_comments,
)
from codeverse.workspace import Workspace

GATE = "lint:glsl_shader"
SHADER = "src/shader.frag"
COMMON = "src/common.glsl"
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
def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _check_file(rel: str, text: str, *, role: str) -> list[GateFinding]:
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


def lint_workspace(ws: Workspace) -> GateReport:
    findings: list[GateFinding] = []
    shader = ws.root / SHADER
    if not shader.is_file():
        findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, target=SHADER, message=f"{SHADER} is missing",
                                    fix_hint="create src/shader.frag with a mainImage(out vec4 fragColor, in vec2 fragCoord) function",
                                    data={"kind": "missing_entry", "file": SHADER}))
    else:
        findings.extend(_check_file(SHADER, shader.read_text(errors="replace"), role="shader"))
    for rel, role in ((COMMON, "common"), (BUFFER_A, "buffer_a")):
        p = ws.root / rel
        if p.is_file():
            findings.extend(_check_file(rel, p.read_text(errors="replace"), role=role))
    stray = [str(p.relative_to(ws.root)) for p in ws.src.rglob("*") if p.is_file()
             and str(p.relative_to(ws.root)) not in (SHADER, COMMON, BUFFER_A) and p.suffix in (".frag", ".glsl", ".vert", ".py", ".js")]
    for rel in stray:
        findings.append(GateFinding(gate=GATE, severity=Severity.WARN, target=rel, message=f"{rel} is not part of the shader contract and is ignored",
                                    fix_hint="keep code in src/shader.frag (+ src/common.glsl, src/buffer_a.frag)", data={"kind": "stray_file", "file": rel}))
    passed = not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=GATE, passed=passed, findings=findings)


def lint_text(shader_src: str, common_src: str | None = None, buffer_a_src: str | None = None) -> list[GateFinding]:
    """Lint in-memory sources (tools / tests)."""
    out = _check_file(SHADER, shader_src, role="shader")
    if common_src is not None:
        out += _check_file(COMMON, common_src, role="common")
    if buffer_a_src is not None:
        out += _check_file(BUFFER_A, buffer_a_src, role="buffer_a")
    return out


__all__ = ["GATE", "SHADER", "COMMON", "BUFFER_A", "lint_workspace", "lint_text", "strip_comments"]
