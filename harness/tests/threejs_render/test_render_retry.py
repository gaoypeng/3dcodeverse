"""A transient render failure is retried once with twice the time; a real one is not."""

from __future__ import annotations

import pytest

import codeverse3d.spatial.render as R
from codeverse3d.spatial.render import RenderError, render_glb


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    from codeverse3d.config import get_settings

    monkeypatch.setenv("C3D_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    monkeypatch.setattr(R, "RETRY_PAUSE_S", 0.0)
    yield
    get_settings.cache_clear()


# verbatim from a real run where Chrome reaped the render tab
DETACHED = "render_glb failed: Attempted to use detached Frame '10276B428E350BFA074A02AF37D52E6C'."


def test_retry_policy(tmp_path, monkeypatch):
    calls: list[tuple[float, bool]] = []
    fail_with: list[str] = []

    def fake(glb, out_dir, params, *, gpu, timeout_s, own_browser=False):
        calls.append((timeout_s, own_browser))
        if len(calls) == 1:
            raise RenderError(fail_with[0])
        return {"views": [], "renderer": "fake", "duration_ms": 1}

    monkeypatch.setattr(R, "_run_render", fake)
    glb = tmp_path / "x.glb"
    glb.write_bytes(b"glTF")
    # a transient failure is retried once with twice the time on the shared browser
    fail_with[:] = ["render_glb failed: node script render_glb.mjs exited 1: Waiting failed: 30000 ms exceeded"]
    rs = render_glb(glb, tmp_path / "out", use_cache=False, timeout_s=100)
    assert rs.renderer == "fake" and calls == [(100, False), (200, False)]
    calls.clear()
    fail_with[:] = [DETACHED]
    rs = render_glb(glb, tmp_path / "out", use_cache=False, timeout_s=100)
    assert rs.renderer == "fake"
    assert [own for _, own in calls] == [False, True], "the retry must not go back to the browser that just died"
