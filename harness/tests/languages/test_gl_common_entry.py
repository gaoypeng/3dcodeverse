"""_gl_common consolidation + ENTRY_FILE adoption across the language runtimes (offline)."""

from __future__ import annotations

from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.languages import _gl_common
from codeverse3d.spatial.gl_render import GlHost


def test_make_host_prefers_injected_host() -> None:
    injected = GlHost(gpu="off", timeout_s=1.0)
    assert _gl_common.make_host(injected, 99.0) is injected
    built = _gl_common.make_host(None, 7.5)
    assert isinstance(built, GlHost) and built is not injected


def test_runtimes_lead_with_the_entry_file_table() -> None:
    from codeverse3d.languages.blender import ENTRY_REL, BlenderRuntime
    from codeverse3d.languages.cadquery import CadQueryRuntime
    from codeverse3d.languages.glsl_shader import GlslShaderRuntime
    from codeverse3d.languages.opengl_python import OpenGLPythonRuntime
    from codeverse3d.languages.threejs import ThreeJsRuntime
    from codeverse3d.languages.urdf import UrdfBlenderRuntime

    assert ENTRY_FILE[Language.BLENDER] == ENTRY_REL
    for rt in (BlenderRuntime, CadQueryRuntime, GlslShaderRuntime, OpenGLPythonRuntime,
               ThreeJsRuntime, UrdfBlenderRuntime):
        assert rt.entry_globs[0] == ENTRY_FILE[rt.language], rt.__name__
