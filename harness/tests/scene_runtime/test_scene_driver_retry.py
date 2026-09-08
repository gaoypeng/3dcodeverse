"""A scene driver that LOST ITS BROWSER is retried once on a browser of its own.

Measured 2026-09-05 on `bench/out/scene_baseline`: with the box at load 93 and swap
full, Chrome reaped the render tab of four cells.  Each driver exited 2 with
"Attempted to use detached Frame …", `run_scene_script` raised, the round kept zero
renders and the judge was skipped — after the generator had already been paid for.
The object path had retried a transient render since 2026-08-28; the scene path,
which is the only one whose renders the scene judge reads, had no retry at all.
"""

from __future__ import annotations

import pytest

import codeverse.spatial.render_scene as RS
from codeverse.spatial.node import NodeResult
from codeverse.spatial.render_scene import SceneRenderError, run_scene_script

DETACHED = "render failed: Attempted to use detached Frame '10276B428E350BFA074A02AF37D52E6C'."


def _result(rc: int, summary: dict | None) -> NodeResult:
    return NodeResult(rc=rc, stdout="", stderr="", last_json=summary, duration_ms=1)


def _record(monkeypatch, results: list[NodeResult]) -> list[dict[str, str]]:
    """Feed `results` to run_scene_script in order; collect each call's env_extra."""
    envs: list[dict[str, str]] = []

    def fake(path, args, *, cwd, timeout_s, env_extra, check):
        envs.append(dict(env_extra or {}))
        return results[len(envs) - 1]

    monkeypatch.setattr(RS, "run_node", fake)
    return envs


def test_a_lost_browser_is_retried_on_an_owned_browser(monkeypatch):
    envs = _record(monkeypatch, [
        _result(2, {"ok": False, "error": DETACHED}),
        _result(0, {"ok": True, "n_views": 8}),
    ])
    r = run_scene_script("render_scene.mjs", ["--ws", "x"], timeout_s=10)
    assert r.summary["n_views"] == 8, "the second attempt's result is the one returned"
    assert len(envs) == 2, "a browser loss must be retried"
    assert envs[0].get("CV3D_BROWSER_REUSE") is None
    assert envs[1]["CV3D_BROWSER_REUSE"] == "off", "the retry must not reuse the browser that died"


def test_a_scene_that_failed_is_not_retried(monkeypatch):
    """Exit 1 is a verdict about the scene — console errors, a scene that did not
    boot — not a driver failure.  Re-rendering it would only spend the time again."""
    envs = _record(monkeypatch, [_result(1, {"ok": False, "error": "scene did not boot"})])
    r = run_scene_script("render_scene.mjs", ["--ws", "x"], timeout_s=10)
    assert r.rc == 1 and len(envs) == 1


def test_a_driver_error_that_is_not_a_browser_loss_is_not_retried(monkeypatch):
    envs = _record(monkeypatch, [_result(2, {"ok": False, "error": "--ws and --out are required"})])
    with pytest.raises(SceneRenderError, match="--ws and --out are required"):
        run_scene_script("render_scene.mjs", [], timeout_s=10)
    assert len(envs) == 1


def test_a_retry_that_loses_the_browser_again_raises(monkeypatch):
    envs = _record(monkeypatch, [
        _result(2, {"ok": False, "error": DETACHED}),
        _result(2, {"ok": False, "error": DETACHED}),
    ])
    with pytest.raises(SceneRenderError, match="detached Frame"):
        run_scene_script("render_scene.mjs", ["--ws", "x"], timeout_s=10)
    assert len(envs) == 2, "exactly one retry — a box out of memory stays out of memory"


def test_a_driver_that_exits_0_with_no_summary_is_retried(monkeypatch):
    """Every scene driver ends with a JSON summary line, so a run that produced none had
    its stdout tail dropped (`proc._ABANDONED`) — transient, and worth one more attempt.
    Without the retry the empty summary reached `probes.probe_report`, which rendered it
    as "[?] scene did not boot"; desert_canyon then spent all three repair attempts on a
    defect that was never there, while its own artifacts recorded `boot.ok: true`."""
    envs = _record(monkeypatch, [
        _result(0, None),
        _result(0, {"ok": True, "boot": {"ok": True, "stage": "ready"}}),
    ])
    r = run_scene_script("probe_scene.mjs", ["--ws", "x"], timeout_s=10)
    assert r.summary["boot"]["ok"] is True
    assert len(envs) == 2 and envs[1]["CV3D_BROWSER_REUSE"] == "off"


def test_a_driver_that_answered_is_not_retried_for_a_failing_verdict(monkeypatch):
    """Exit 1 WITH a summary is a scene that failed — a verdict, and complete."""
    envs = _record(monkeypatch, [_result(1, {"ok": False, "boot": {"ok": False, "stage": "createScene",
                                                                  "error": "TypeError: x is not a function"}})])
    r = run_scene_script("probe_scene.mjs", ["--ws", "x"], timeout_s=10)
    assert r.summary["boot"]["stage"] == "createScene" and len(envs) == 1


def test_a_transient_host_failure_is_retried_on_the_same_browser(monkeypatch):
    """`node.TRANSIENT_MARKERS` is one vocabulary for both render paths (2026-09-07): a host
    that timed out waiting for the page under contention is the box, not the scene, and it
    keeps the shared browser — only a browser LOSS takes an owned one."""
    envs = _record(monkeypatch, [
        _result(2, {"ok": False, "error": "host failed: Waiting failed: 60000ms exceeded"}),
        _result(0, {"ok": True, "n_views": 8}),
    ])
    r = run_scene_script("render_scene.mjs", ["--ws", "x"], timeout_s=10)
    assert r.summary["n_views"] == 8 and len(envs) == 2
    assert envs[1].get("CV3D_BROWSER_REUSE") is None, "a timeout is not a browser loss"
