"""Real moderngl rendering (offline; GPU env if usable, llvmpipe otherwise).  Skipped when no GL context."""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from codeverse3d.contracts.plan import GraphicsPlan, PassPlan
from codeverse3d.languages.glsl_shader import GlslShaderRuntime
from codeverse3d.languages.opengl_python import OpenGLPythonRuntime
from codeverse3d.spatial import gl_render
from codeverse3d.spatial.gl_render import GlHost, GlHostError
from codeverse3d.spatial.registry import ToolContext, get_tool

pytest.importorskip("moderngl")

SMALL = dict(width=256, height=144, preview=False)
PLAN = GraphicsPlan(title="Test", summary="s", style="warm", passes=[PassPlan(name="Sky", description="gradient")],
                    motion="sun moves", key_visuals=["sun"], duration_s=6.0, resolution=(256, 144))


@pytest.fixture(scope="module")
def host(tmp_path_factory: pytest.TempPathFactory) -> GlHost:
    h = GlHost(timeout_s=120)
    try:
        r = h.render_fragment_shader("#version 330 core\nout vec4 f;\nvoid main(){ f = vec4(1.0); }\n",
                                     tmp_path_factory.mktemp("gl_smoke"),
                                     width=16, height=16, times=(0.0,))
    except GlHostError as e:  # pragma: no cover - machine without GL
        pytest.skip(f"no OpenGL context: {e}")
    if not r.ok:
        pytest.skip(f"GL smoke render failed: {r.error_message}")
    return h


def _ws_with_plan(tmp_ws):
    tmp_ws.write_json(tmp_ws.plan_path, PLAN)
    return tmp_ws


def test_shader_skeleton_builds_and_writes_artifacts(tmp_ws, host):
    ws = _ws_with_plan(tmp_ws)
    rt = GlslShaderRuntime(host=host)
    paths = rt.skeleton(ws, PLAN)
    assert {p.name for p in paths} == {"common.glsl", "shader.frag"}
    assert "TODO pass 1: Sky" in (ws.src / "shader.frag").read_text()
    assert rt.lint(ws).passed
    br = rt.build(ws, **SMALL)
    assert br.ok, br.error_message
    assert br.glb_path is None and br.language == "glsl_shader"
    for key in ("frames", "sheet", "metrics"):
        assert key in br.extra_paths
    frames = sorted((ws.artifacts / "frames").glob("f*_t*.png"))
    assert len(frames) == 5  # t = 0, 1, 2.5, 4, 6 (duration 6)
    metrics = json.loads((ws.artifacts / "metrics.json").read_text())
    assert metrics["stats"]["mean_lum"] > 0.1 and not metrics["stats"]["any_nan"] and not metrics["stats"]["static"]
    assert br.census["convention"] == "mainImage" and br.census["feedback"] is False
    assert (ws.artifacts / "build.json").is_file()


def test_feedback_and_nan_detection(tmp_ws, host):
    ws = _ws_with_plan(tmp_ws)
    (ws.src / "shader.frag").write_text(
        "void mainImage(out vec4 fragColor, in vec2 fragCoord) {\n    vec2 uv = fragCoord / u_resolution.xy;\n"
        "    vec3 prev = texture(u_prev, uv).rgb * 0.9;\n    float d = smoothstep(0.05, 0.0, length(uv - vec2(0.5 + 0.3*sin(u_time), 0.5)));\n"
        "    float bad = uv.x > 0.9 ? sqrt(-1.0) : 0.0;\n    fragColor = vec4(max(prev, vec3(d)) + bad, 1.0);\n}\n")
    rt = GlslShaderRuntime(host=host)
    br = rt.build(ws, **SMALL)
    assert br.ok and br.census["feedback"] is True
    metrics = json.loads((ws.artifacts / "metrics.json").read_text())
    assert metrics["stats"]["any_nan"] is True
    assert any(f["data"]["kind"] == "nan" for f in metrics["gate"]["findings"]) and not metrics["gate"]["passed"]


@pytest.mark.parametrize("buffer_a", [False, True], ids=["image", "buffer_a"])
def test_feedback_starts_black_even_when_allocated_texture_memory_is_dirty(tmp_path, monkeypatch, buffer_a):
    """Exercise real GL with nonzero allocation contents, not driver-provided zeros."""
    runner = tmp_path / "dirty_gl_runner.py"
    runner.write_text(f"""
import importlib.util
import sys
import moderngl
import numpy as np
original = moderngl.Context.texture
def dirty_texture(self, size, components, data=None, **kwargs):
    if data is None and kwargs.get('dtype') == 'f4':
        data = np.full((size[1], size[0], components), .375, dtype='f4').tobytes()
    return original(self, size, components, data=data, **kwargs)
moderngl.Context.texture = dirty_texture
spec = importlib.util.spec_from_file_location('real_runner', {str(gl_render.RUNNER)!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.exit(module.main())
""")
    monkeypatch.setattr(gl_render, "RUNNER", runner)
    previous = """#version 330 core
uniform vec2 u_resolution;
uniform sampler2D u_prev;
out vec4 fragColor;
void main() { fragColor = texture(u_prev, gl_FragCoord.xy / u_resolution); }
"""
    image = """#version 330 core
uniform vec2 u_resolution;
uniform sampler2D u_prev;
uniform sampler2D u_buffer_a;
out vec4 fragColor;
void main() {
 vec2 uv=gl_FragCoord.xy/u_resolution;
 vec4 a=texture(u_prev,uv), b=texture(u_buffer_a,uv);
 bool black=all(equal(a,vec4(0,0,0,1))) && all(equal(b,vec4(0,0,0,1)));
 fragColor=black?vec4(0,1,0,1):vec4(1,0,0,1);
}
""" if buffer_a else previous
    try:
        result = GlHost(timeout_s=90).render_fragment_shader(
            image, tmp_path / "frames", width=8, height=8, times=(0.0,),
            buffer_a_src=previous if buffer_a else None, feedback=True)
    except GlHostError as exc:
        pytest.skip(f"OpenGL unavailable: {exc}")
    assert result.ok, result.error_message
    pixels = np.asarray(Image.open(result.frames[0].path).convert("RGB"))
    assert np.all(pixels == ([0, 255, 0] if buffer_a else [0, 0, 0])), pixels.tolist()


def test_program_skeleton_builds_and_errors_map(tmp_ws, host):
    ws = _ws_with_plan(tmp_ws)
    rt = OpenGLPythonRuntime(host=host)
    rt.skeleton(ws, PLAN)
    assert rt.lint(ws).passed
    br = rt.build(ws, **SMALL)
    assert br.ok, br.error_message
    assert len(list((ws.artifacts / "frames").glob("*.png"))) == 5 and (ws.artifacts / "frames_sheet.png").is_file()
    metrics = json.loads((ws.artifacts / "metrics.json").read_text())
    assert not metrics["stats"]["static"] and metrics["stats"]["mean_lum"] > 0.1
    src = (ws.src / "program.py").read_text()
    (ws.src / "program.py").write_text(src.replace("fbo.use()\n    ctx.disable", "fbo.use()\n    raise ValueError('boom')\n    ctx.disable"))
    br = rt.build(ws, **SMALL)
    assert not br.ok and br.error_type == "ValueError" and br.error_file == "src/program.py" and br.error_line
    assert "boom" in br.error_message


def test_gl_tools_probe_and_frames(tmp_ws, host, monkeypatch):
    ws = _ws_with_plan(tmp_ws)
    GlslShaderRuntime(host=host).skeleton(ws, PLAN)
    monkeypatch.setattr("codeverse3d.languages.glsl_shader.GlslShaderRuntime.host", lambda self, timeout_s=None: host)
    ctx = ToolContext(workspace=ws, language="glsl_shader", track="graphics")
    obs = get_tool("gl_probe").call(ctx, {"t": 1.5})
    assert obs.ok and "PROBE OK" in obs.text and len(obs.images) == 1 and obs.numbers["n_frames"] == 1
    obs = get_tool("gl_frames").call(ctx, {"times": [0.0, 2.0], "width": 256, "height": 144})
    assert obs.ok and "FRAMES OK" in obs.text and obs.images and obs.numbers["n_frames"] == 2 and not obs.numbers["static"]
    (ws.src / "shader.frag").write_text("#version 330 core\nvoid mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(1.0); }\n")
    obs = get_tool("gl_probe").call(ctx, {})
    assert not obs.ok and "LINT FAILED" in obs.text and "#version" in obs.text


def test_gl_probe_compile_error_report(tmp_ws, host, monkeypatch):
    """Pin: the gl_probe failure report names the error type and the agent's
    (workspace-relative) file:line, from any cwd, without host paths."""
    ws = _ws_with_plan(tmp_ws)
    GlslShaderRuntime(host=host).skeleton(ws, PLAN)
    (ws.src / "shader.frag").write_text(
        "// line 1\nvoid mainImage(out vec4 fragColor, in vec2 fragCoord) {\n    vec2 uv = fragCoord / u_resolution.xy;\n"
        "    float v = twice(nope);\n    fragColor = vec4(uv, v, 1.0);\n}\n")
    monkeypatch.setattr("codeverse3d.languages.glsl_shader.GlslShaderRuntime.host", lambda self, timeout_s=None: host)
    ctx = ToolContext(workspace=ws, language="glsl_shader", track="graphics")
    obs = get_tool("gl_probe").call(ctx, {})
    assert not obs.ok and obs.numbers["stage"] == "build"
    first = obs.text.splitlines()[0]
    assert first.startswith("BUILD FAILED: GlslCompileError") and "at src/shader.frag:4" in first
    assert "nope" in obs.text and str(ws.root) not in obs.text
    assert obs.text.count("nope") == 1  # the compile log is the message — the stderr tail is not repeated
    assert obs.numbers["error_type"] == "GlslCompileError" and obs.numbers["error_line"] == 4
