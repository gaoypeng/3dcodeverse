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
    """The glsl_shader stub (D107): entry point + plan comments + one flat write; builds the judged frames, gate says `static`."""
    ws = _ws_with_plan(tmp_ws)
    rt = GlslShaderRuntime(host=host)
    paths = rt.skeleton(ws, PLAN)
    assert {p.name for p in paths} == {"common.glsl", "shader.frag"}
    assert rt.lint(ws).passed
    br = rt.build(ws, **SMALL)
    assert br.ok, br.error_message
    assert br.glb_path is None and {"frames", "sheet", "metrics"} <= set(br.extra_paths)
    frames = sorted((ws.artifacts / "frames").glob("f*_t*.png"))
    assert len(frames) == 5  # t = 0, 1, 2.5, 4, 6 (duration 6)
    metrics = json.loads((ws.artifacts / "metrics.json").read_text())
    assert metrics["stats"]["mean_lum"] > 0.1 and not metrics["stats"]["any_nan"] and metrics["stats"]["static"]
    assert any(f["data"]["kind"] == "static" for f in metrics["gate"]["findings"])
    assert br.census["convention"] == "mainImage" and br.census["feedback"] is False


def test_feedback_starts_black_even_when_allocated_texture_memory_is_dirty(tmp_path, monkeypatch):
    """Real GL with nonzero allocation contents, not driver-provided zeros: u_prev must still start black."""
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
    frag = """#version 330 core
uniform vec2 u_resolution;
uniform sampler2D u_prev;
out vec4 fragColor;
void main() { fragColor = texture(u_prev, gl_FragCoord.xy / u_resolution); }
"""
    try:
        result = GlHost(timeout_s=90).render_fragment_shader(frag, tmp_path / "frames", width=8, height=8, times=(0.0,), feedback=True)
    except GlHostError as exc:
        pytest.skip(f"OpenGL unavailable: {exc}")
    assert result.ok, result.error_message
    assert np.all(np.asarray(Image.open(result.frames[0].path).convert("RGB")) == 0)


def test_program_skeleton_builds_and_errors_map(tmp_ws, host):
    """The opengl_python stub (D107) builds the judged frames; a runtime error in render() maps back to src/program.py:LINE."""
    ws = _ws_with_plan(tmp_ws)
    rt = OpenGLPythonRuntime(host=host)
    rt.skeleton(ws, PLAN)
    assert rt.lint(ws).passed
    src = (ws.src / "program.py").read_text()
    br = rt.build(ws, **SMALL)
    assert br.ok, br.error_message
    (ws.src / "program.py").write_text(src.replace("fbo.use()\n    ctx.disable", "fbo.use()\n    raise ValueError('boom')\n    ctx.disable"))
    br = rt.build(ws, **SMALL)
    assert not br.ok and br.error_type == "ValueError" and br.error_file == "src/program.py" and br.error_line


def test_gl_tools_probe_and_frames(tmp_ws, host, monkeypatch):
    ws = _ws_with_plan(tmp_ws)
    GlslShaderRuntime(host=host).skeleton(ws, PLAN)
    monkeypatch.setattr("codeverse3d.languages.glsl_shader.GlslShaderRuntime.host", lambda self, timeout_s=None: host)
    ctx = ToolContext(workspace=ws, language="glsl_shader", track="graphics")
    obs = get_tool("gl_probe").call(ctx, {"t": 1.5})
    assert obs.ok and "PROBE OK" in obs.text and len(obs.images) == 1 and obs.numbers["n_frames"] == 1
    obs = get_tool("gl_frames").call(ctx, {"times": [0.0, 2.0], "width": 256, "height": 144})
    assert obs.ok and "FRAMES OK" in obs.text and obs.images and obs.numbers["n_frames"] == 2 and obs.numbers["static"]
    (ws.src / "shader.frag").write_text("#version 330 core\nvoid mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(1.0); }\n")
    obs = get_tool("gl_probe").call(ctx, {})
    assert not obs.ok and "LINT FAILED" in obs.text and "#version" in obs.text


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
