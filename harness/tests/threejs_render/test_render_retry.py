"""A transient render failure is retried once with twice the time; a real one is not."""

from __future__ import annotations

from pathlib import Path

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


def _glb(tmp_path: Path) -> Path:
    p = tmp_path / "x.glb"
    p.write_bytes(b"glTF")
    return p


# verbatim from a real run where Chrome reaped the render tab
DETACHED = "render_glb failed: Attempted to use detached Frame '10276B428E350BFA074A02AF37D52E6C'."


def test_retry_policy(tmp_path, monkeypatch):
    calls: list[tuple[float, bool]] = []
    fail_with: list[str] = []

    def fake(glb, out_dir, params, *, gpu, timeout_s, own_browser=False):
        calls.append((timeout_s, own_browser))
        if len(calls) == 1 or fail_with[0].startswith("render_glb failed: Cannot"):
            raise RenderError(fail_with[0])
        return {"views": [], "renderer": "fake", "duration_ms": 1}

    monkeypatch.setattr(R, "_run_render", fake)
    glb = _glb(tmp_path)
    # a transient failure is retried once with twice the time on the shared browser:
    # a slow box is not a reason to give up browser reuse
    fail_with[:] = ["render_glb failed: node script render_glb.mjs exited 1: Waiting failed: 30000 ms exceeded"]
    rs = render_glb(glb, tmp_path / "out", use_cache=False, timeout_s=100)
    assert rs.renderer == "fake" and calls == [(100, False), (200, False)]
    # a real failure is not retried
    calls.clear()
    fail_with[:] = ["render_glb failed: Cannot find module 'puppeteer'"]
    with pytest.raises(RenderError):
        render_glb(glb, tmp_path / "out", use_cache=False, timeout_s=100)
    assert calls == [(100, False)]
    # a lost browser is retried on a browser of our own
    calls.clear()
    fail_with[:] = [DETACHED]
    rs = render_glb(glb, tmp_path / "out", use_cache=False, timeout_s=100)
    assert rs.renderer == "fake"
    assert [own for _, own in calls] == [False, True], "the retry must not go back to the browser that just died"
