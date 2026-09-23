"""_gl_common.make_host (offline)."""

from __future__ import annotations

from codeverse3d.languages import _gl_common
from codeverse3d.spatial.gl_render import GlHost


def test_make_host_prefers_injected_host() -> None:
    injected = GlHost(gpu="off", timeout_s=1.0)
    assert _gl_common.make_host(injected, 99.0) is injected
    built = _gl_common.make_host(None, 7.5)
    assert isinstance(built, GlHost) and built is not injected
