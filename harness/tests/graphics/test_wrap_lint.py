"""Offline: shader wrapping / line maps / compiler-log parsing and the two lints."""

from __future__ import annotations

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.languages.glsl_shader import (
    HEADER,
    compose,
    first_error,
    lint_text,
    parse_glsl_log,
)
from codeverse3d.languages.opengl_python import lint_source

SHADER = "void mainImage(out vec4 fragColor, in vec2 fragCoord) {\n    vec2 uv = fragCoord / u_resolution.xy;\n    fragColor = vec4(uv, 0.5 + 0.5*sin(u_time), 1.0);\n}\n"
COMMON = "float hash(float n) { return fract(sin(n) * 43758.5453); }\n"


def test_parse_glsl_log_dialects_map_to_files():
    c = compose(SHADER, COMMON)
    off = HEADER.count("\n") + 1  # common.glsl starts here (1 line) → shader.frag starts at off+1
    mesa = f"0:{off + 2}(12): error: `foo' undeclared\n0:{off}(3): warning: unused\n"
    nvidia = f"0({off + 2}) : error C1008: undefined variable \"foo\"\n"
    for log in (mesa, nvidia):
        msgs = parse_glsl_log(log, c.line_map)
        err = first_error(msgs)
        assert err is not None and err.file == "src/shader.frag" and err.file_line == 2, log
    msgs = parse_glsl_log(mesa, c.line_map)
    assert [m.kind for m in msgs] == ["error", "warning"] and msgs[1].file == "src/common.glsl"
    assert parse_glsl_log("GLSL Compiler failed\n\nfragment_shader\n===========\n") == []


def test_glsl_lint_rules():
    bad = (
        "// comment: uniform float u_time; is provided\n#version 330 core\nuniform float u_time;\nuniform float u_speed;\n"
        "out vec4 fragColor;\nvoid mainImage(out vec4 fragColor, in vec2 fragCoord) {\n  vec3 c = texture(iChannel2, vec2(0)).rgb;\n"
        "  c += texture(u_tex, vec2(0)).rgb;\n  float m = 3.0 % 2.0;\n  gl_FragColor = vec4(c, 1.0);\n}\n"
    )
    kinds = {f.data["kind"] for f in lint_text(bad) if f.severity == Severity.ERROR}
    assert {"version_line", "redeclared_uniform", "custom_uniform", "redeclared_output", "missing_input", "undeclared_sampler",
            "float_modulo", "legacy_glsl"} <= kinds
    assert next(f for f in lint_text(bad) if f.data["kind"] == "version_line").data["line"] == 2   # the comment on line 1 is ignored
    assert not [f for f in lint_text(SHADER, COMMON) if f.severity == Severity.ERROR]
    assert any(f.data["kind"] == "no_entry" for f in lint_text("vec3 f() { return vec3(0); }"))
    assert any(f.data["kind"] == "buffer_self_ref" for f in lint_text(SHADER, None, SHADER.replace("u_resolution", "u_buffer_a") + "\n"))


PROGRAM_OK = """import math
import moderngl
import numpy as np
def setup(ctx, width, height):
    return {}
def render(ctx, state, t, frame, fbo):
    fbo.use()
"""


def test_program_lint_rules():
    assert not [f for f in lint_source(PROGRAM_OK) if f.severity == Severity.ERROR]
    bad = (
        "import glfw\nimport os\nimport time\nimport moderngl\nimport random\n"
        "SRC = '''#version 120\\nvoid main(){ gl_FragColor = vec4(1.0); }'''\n"
        "def setup(ctx, w):\n    ctx2 = moderngl.create_standalone_context()\n    f = open('x')\n    return random.random()\n"
        "def render(ctx, state, t, frame, fbo):\n    now = time.time()\n"
    )
    kinds = {f.data["kind"] for f in lint_source(bad)}
    assert {"window_lib", "forbidden_import", "own_context", "forbidden_call", "wall_clock", "bad_signature", "glsl_version",
            "legacy_glsl", "unseeded_random"} <= kinds
    assert any(f.data["kind"] == "missing_entry" for f in lint_source("import moderngl\n"))
