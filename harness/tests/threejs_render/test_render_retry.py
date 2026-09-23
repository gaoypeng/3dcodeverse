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


def test_transient_failure_is_retried_with_double_timeout_on_the_shared_browser(tmp_path, monkeypatch):
    calls: list[tuple[float, bool]] = []

    def fake(glb, out_dir, params, *, gpu, timeout_s, own_browser=False):
        calls.append((timeout_s, own_browser))
        if len(calls) == 1:
            raise RenderError("render_glb failed: node script render_glb.mjs exited 1: Waiting failed: 30000 ms exceeded")
        return {"views": [], "renderer": "fake", "duration_ms": 1}

    monkeypatch.setattr(R, "_run_render", fake)
    rs = render_glb(_glb(tmp_path), tmp_path / "out", use_cache=False, timeout_s=100)
    # a slow box is not a reason to give up browser reuse
    assert rs.renderer == "fake" and calls == [(100, False), (200, False)]


def test_real_failure_is_not_retried(tmp_path, monkeypatch):
    calls: list[float] = []

    def fake(glb, out_dir, params, *, gpu, timeout_s, own_browser=False):
        calls.append(timeout_s)
        raise RenderError("render_glb failed: Cannot find module 'puppeteer'")

    monkeypatch.setattr(R, "_run_render", fake)
    with pytest.raises(RenderError):
        render_glb(_glb(tmp_path), tmp_path / "out", use_cache=False, timeout_s=100)
    assert calls == [100]


# The string below is verbatim from eval/bench/out/scene_baseline (2026-09-05): with the box
# at load 93 and swap full, Chrome reaped the render tab and every driver reported it
# this way.  "detached frame" was not in TRANSIENT_MARKERS, so the object path did not
# retry it either — it only ever matched the "Target closed" spelling.
DETACHED = "render_glb failed: Attempted to use detached Frame '10276B428E350BFA074A02AF37D52E6C'."


def test_a_lost_browser_is_retried_on_a_browser_of_our_own(tmp_path, monkeypatch):
    seen: list[bool] = []

    def fake(glb, out_dir, params, *, gpu, timeout_s, own_browser=False):
        seen.append(own_browser)
        if len(seen) == 1:
            raise RenderError(DETACHED)
        return {"views": [], "renderer": "fake", "duration_ms": 1}

    monkeypatch.setattr(R, "_run_render", fake)
    rs = render_glb(_glb(tmp_path), tmp_path / "out", use_cache=False, timeout_s=100)
    assert rs.renderer == "fake"
    assert seen == [False, True], "the retry must not go back to the browser that just died"

