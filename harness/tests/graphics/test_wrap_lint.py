"""Offline: shader wrapping / line maps / compiler-log parsing and the two lints."""

from __future__ import annotations

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.languages.glsl_shader import (
    HEADER,
    compose,
    defined_functions,
    detect_convention,
    first_error,
    lint_text,
    lint_workspace,
    parse_glsl_log,
    strip_comments,
)
from codeverse3d.languages.opengl_python import lint_source
from codeverse3d.languages.opengl_python import lint_workspace as lint_program_ws

SHADER = "void mainImage(out vec4 fragColor, in vec2 fragCoord) {\n    vec2 uv = fragCoord / u_resolution.xy;\n    fragColor = vec4(uv, 0.5 + 0.5*sin(u_time), 1.0);\n}\n"
COMMON = "float hash(float n) { return fract(sin(n) * 43758.5453); }\n"


def test_compose_line_map_with_common_and_trailer():
    c = compose(SHADER, COMMON)
    assert c.convention == "mainImage" and not c.uses_feedback
    header_lines = HEADER.count("\n")
    assert c.source.startswith("#version 330 core")
    # common.glsl line 1 sits right after the header; shader.frag line 1 right after common
    assert c.line_map.locate(header_lines + 1) == ("src/common.glsl", 1)
    assert c.line_map.locate(header_lines + 2) == ("src/shader.frag", 1)
    assert c.line_map.locate(header_lines + 4) == ("src/shader.frag", 3)
    assert c.source.rstrip().endswith("void main() { mainImage(fragColor, gl_FragCoord.xy); }")


def test_plain_main_convention_has_no_trailer_and_comments_ignored():
    src = "// mainImage is mentioned here only in a comment; u_prev too\nvoid main() { fragColor = vec4(1.0); }\n"
    c = compose(src)
    assert c.convention == "main" and not c.uses_feedback
    assert "mainImage(fragColor" not in c.source
    assert detect_convention("void mainImage(out vec4 c, in vec2 f) {}") == "mainImage"
    assert compose("void mainImage(out vec4 c, in vec2 f) { c = texture(u_prev, f); }").uses_feedback


def test_parse_glsl_log_dialects_map_to_files():
    c = compose(SHADER, COMMON)
    off = HEADER.count("\n") + 1  # common.glsl starts here (1 line) → shader.frag starts at off+1
    mesa = f"0:{off + 2}(12): error: `foo' undeclared\n0:{off}(3): warning: unused\n"
    nvidia = f"0({off + 2}) : error C1008: undefined variable \"foo\"\n"
    angle = f"ERROR: 0:{off + 2}: 'foo' : undeclared identifier\n"
    for log in (mesa, nvidia, angle):
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
    # the comment on line 1 must not trigger anything; the #version is reported on line 2
    ver = next(f for f in lint_text(bad) if f.data["kind"] == "version_line")
    assert ver.data["line"] == 2
    assert not [f for f in lint_text(SHADER, COMMON) if f.severity == Severity.ERROR]
    # common.glsl must not carry an entry point; no entry at all is an error
    assert any(f.data["kind"] == "entry_in_common" for f in lint_text(SHADER, "void main(){}"))
    assert any(f.data["kind"] == "no_entry" for f in lint_text("vec3 f() { return vec3(0); }"))
    assert any(f.data["kind"] == "buffer_self_ref" for f in lint_text(SHADER, None, SHADER.replace("u_resolution", "u_buffer_a") + "\n"))


def test_mixed_comments_blank_only_the_comments():
    """A `/*` inside a `//` comment opens nothing: the two-pass stripper (blocks, then lines)
    blanked everything up to a later `*/`, so the lint never saw the code in between."""
    src = ("// tweak /* the fog here\nfloat fogAmt(float d) { return d; }\nuniform float u_extra;\n// end */\n"
           "/* a // inside a block */ float x() { return 1.0; }\n")
    stripped = strip_comments(src)
    assert stripped.count("\n") == src.count("\n") and "uniform float u_extra;" in stripped
    assert "tweak" not in stripped and "inside" not in stripped
    assert defined_functions(src) == {"fogAmt": 2, "x": 5}
    shader = src + SHADER
    assert any(f.data["kind"] == "custom_uniform" for f in lint_text(shader))


def test_glsl_lint_workspace_missing_and_stray(tmp_ws):
    rep = lint_workspace(tmp_ws)
    assert not rep.passed and rep.findings[0].data["kind"] == "missing_entry"
    (tmp_ws.src / "shader.frag").write_text(SHADER)
    (tmp_ws.src / "extra.frag").write_text("// stray")
    rep = lint_workspace(tmp_ws)
    assert rep.passed and any(f.data["kind"] == "stray_file" for f in rep.findings)


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
    assert any(f.data["kind"] == "syntax" for f in lint_source("def setup(:\n"))
    assert any(f.data["kind"] == "missing_entry" for f in lint_source("import moderngl\n"))


def test_program_lint_workspace(tmp_ws):
    assert not lint_program_ws(tmp_ws).passed
    (tmp_ws.src / "program.py").write_text(PROGRAM_OK)
    (tmp_ws.src / "helpers.py").write_text("x = 1\n")
    rep = lint_program_ws(tmp_ws)
    assert rep.passed and any(f.data["kind"] == "stray_file" for f in rep.findings)
