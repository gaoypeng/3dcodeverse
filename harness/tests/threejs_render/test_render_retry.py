"""A transient render failure is retried once with twice the time; a real one is not."""

from __future__ import annotations

from pathlib import Path

import pytest

import codeverse.spatial.render as R
from codeverse.spatial.render import RenderError, render_glb


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    from codeverse.config import get_settings

    monkeypatch.setenv("CV3D_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    monkeypatch.setattr(R, "RETRY_PAUSE_S", 0.0)
    yield
    get_settings.cache_clear()


def _glb(tmp_path: Path) -> Path:
    p = tmp_path / "x.glb"
    p.write_bytes(b"glTF")
    return p


def test_transient_failure_is_retried_with_double_timeout(tmp_path, monkeypatch):
    calls: list[float] = []

    def fake(glb, out_dir, params, *, gpu, timeout_s):
        calls.append(timeout_s)
        if len(calls) == 1:
            raise RenderError("render_glb failed: node script render_glb.mjs exited 1: Waiting failed")
        return {"views": [], "renderer": "fake", "duration_ms": 1}

    monkeypatch.setattr(R, "_run_render", fake)
    rs = render_glb(_glb(tmp_path), tmp_path / "out", use_cache=False, timeout_s=100)
    assert rs.renderer == "fake" and calls == [100, 200]


def test_real_failure_is_not_retried(tmp_path, monkeypatch):
    calls: list[float] = []

    def fake(glb, out_dir, params, *, gpu, timeout_s):
        calls.append(timeout_s)
        raise RenderError("render_glb failed: Cannot find module 'puppeteer'")

    monkeypatch.setattr(R, "_run_render", fake)
    with pytest.raises(RenderError):
        render_glb(_glb(tmp_path), tmp_path / "out", use_cache=False, timeout_s=100)
    assert calls == [100]
