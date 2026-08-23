"""_gl_common consolidation + ENTRY_FILE adoption across the language runtimes (offline)."""

from __future__ import annotations

from codeverse.contracts.common import ENTRY_FILE, Language
from codeverse.languages import _gl_common
from codeverse.languages.glsl_shader import gl_build, wrap
from codeverse.spatial.gl_render import GlHost


def test_gl_build_shim_reexports_gl_common() -> None:
    for name in ("finish_build", "judge_times", "load_plan", "preview_times", "resolution_for",
                 "traceback_location", "read_metrics", "SHEET_NAME", "JUDGE_TIMES"):
        assert getattr(gl_build, name) is getattr(_gl_common, name), name


def test_wrap_reexports_moved_glsl_log_types() -> None:
    assert wrap.parse_glsl_log is _gl_common.parse_glsl_log
    assert wrap.GlslMessage is _gl_common.GlslMessage
    assert wrap.LineMap is _gl_common.LineMap and wrap.Segment is _gl_common.Segment


def test_make_host_prefers_injected_host() -> None:
    injected = GlHost(gpu="off", timeout_s=1.0)
    assert _gl_common.make_host(injected, 99.0) is injected
    built = _gl_common.make_host(None, 7.5)
    assert isinstance(built, GlHost) and built is not injected


def test_runtimes_lead_with_the_entry_file_table() -> None:
    from codeverse.languages.blender.layout import ENTRY_REL
    from codeverse.languages.blender.runtime import BlenderRuntime
    from codeverse.languages.cadquery.runtime import CadQueryRuntime
    from codeverse.languages.glsl_shader.runtime import GlslShaderRuntime
    from codeverse.languages.opengl_python.runtime import OpenGLPythonRuntime
    from codeverse.languages.threejs.runtime import ThreeJsRuntime
    from codeverse.languages.urdf.runtime import UrdfBlenderRuntime

    assert ENTRY_FILE[Language.BLENDER] == ENTRY_REL
    for rt in (BlenderRuntime, CadQueryRuntime, GlslShaderRuntime, OpenGLPythonRuntime,
               ThreeJsRuntime, UrdfBlenderRuntime):
        assert rt.entry_globs[0] == ENTRY_FILE[rt.language], rt.__name__
