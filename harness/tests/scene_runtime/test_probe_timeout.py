"""D111: a scene probe that times out inside the scene's own code is a typed ERROR against the code (repair gets it); a timeout anywhere else stays the box's."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import codeverse3d.spatial.probes as probes_mod
import codeverse3d.spatial.render_scene as RS
from codeverse3d.contracts.artifacts import GateReport
from codeverse3d.languages.scene_threejs import SceneThreeJsRuntime
from codeverse3d.spatial.node import OWN_BROWSER_ENV, NodeResult
from codeverse3d.spatial.render_scene import SCENE_PHASES, SceneRenderError, run_scene_script
from codeverse3d.tracks import repair as R
from codeverse3d.tracks.generation import GenerationResult

TIMEOUT = {"ok": False, "error": "timeout after 120000 ms", "timed_out": True, "phase": "scene"}


def _feed(monkeypatch, results: list[NodeResult]) -> list[dict[str, str]]:
    seen: list[dict[str, str]] = []

    def fake(path, args, *, cwd, timeout_s, env_extra, check):
        seen.append(dict(env_extra))
        return results[len(seen) - 1]

    monkeypatch.setattr(RS, "run_node", fake)
    return seen


def _timed_out(phase: str) -> SceneRenderError:
    return SceneRenderError("probe_scene.mjs failed (exit 3): timeout after 120000 ms", timed_out=True, phase=phase)


def test_a_driver_timeout_is_retried_once_on_its_own_browser_then_raised_typed(monkeypatch):
    seen = _feed(monkeypatch, [NodeResult(rc=3, stdout="", stderr="", last_json=TIMEOUT, duration_ms=1)] * 2)
    with pytest.raises(SceneRenderError) as ei:
        run_scene_script("probe_scene.mjs", ["--ws", "x"], timeout_s=10)
    assert ei.value.timed_out is True and ei.value.phase == "scene" and ei.value.scene_timed_out
    assert len(seen) == 2, "one harness retry, then the verdict"
    # a wedged SHARED browser times out the same way: the retry must not reuse it
    assert not set(OWN_BROWSER_ENV.items()) <= set(seen[0].items()) and set(OWN_BROWSER_ENV.items()) <= set(seen[1].items())


@pytest.mark.parametrize("phase", ["host", "compile", "report", "close", ""])
def test_a_timeout_outside_the_scenes_code_is_not_the_scenes(monkeypatch, phase):
    _feed(monkeypatch, [NodeResult(rc=3, stdout="", stderr="", last_json={**TIMEOUT, "phase": phase}, duration_ms=1)] * 2)
    with pytest.raises(SceneRenderError) as ei:
        run_scene_script("probe_scene.mjs", ["--ws", "x"], timeout_s=10)
    assert ei.value.timed_out and not ei.value.scene_timed_out and phase not in SCENE_PHASES


@pytest.mark.parametrize("phase", sorted(SCENE_PHASES))
def test_a_timed_out_probe_is_an_error_against_the_code(tmp_ws, monkeypatch, phase):
    monkeypatch.setattr(probes_mod, "run_scene_script", lambda script, args, **kw: (_ for _ in ()).throw(_timed_out(phase)))
    probe, _, census = probes_mod.run_probe(tmp_ws, timeout_s=120)
    (f,) = probe.findings
    assert f.severity.value == "error" and f.data["kind"] == "probe_timeout" and f.data["timed_out"] is True
    assert f.data["phase"] == phase
    assert not f.data.get("harness_failure") and "not your code" not in f.fix_hint
    assert "120 s probe budget" in f.message and f.target == "src/scene.js" and census == {}
    # the build tool shows it as a finding, not as "the tool could not run"
    res = probes_mod.probe_result(probe, census)
    assert res.ok and res.errors == [] and any("probe budget" in line for line in res.findings)


@pytest.mark.parametrize("err", [SceneRenderError("chrome went away"), _timed_out("host"), _timed_out("compile"),
                                 SceneRenderError("probe_scene.mjs timed out after 140s", timed_out=True)],
                         ids=["crash", "host", "compile", "process-clock"])
def test_a_probe_that_could_not_run_is_still_the_harnesss(tmp_ws, monkeypatch, err):
    monkeypatch.setattr(probes_mod, "run_scene_script", lambda script, args, **kw: (_ for _ in ()).throw(err))
    probe, _, _ = probes_mod.run_probe(tmp_ws, timeout_s=120)
    assert probe.findings[0].data.get("harness_failure") is True


def test_the_scene_build_hands_a_timeout_to_repair_not_to_rebuilds(tmp_ws, monkeypatch):
    """The scene_threejs build's BuildResult is not a harness failure, so ``build_with_repair`` runs a repair session on the first pass instead of MAX_HAR..."""
    from codeverse3d.languages.scene_threejs import write_example

    write_example(tmp_ws)

    monkeypatch.setattr(probes_mod, "run_scene_script", lambda script, args, **kw: (_ for _ in ()).throw(_timed_out("scene")))
    build = SceneThreeJsRuntime().build(tmp_ws)
    assert not build.ok and build.harness_failure is False and build.error_type == "probe_timeout"

    events: list[str] = []
    ctx = SimpleNamespace(spec=SimpleNamespace(budget=SimpleNamespace(max_repair_attempts=1)),
                          events=SimpleNamespace(emit=lambda name, **k: events.append(name)))
    monkeypatch.setattr(R, "build_once", lambda c: (build, GateReport(gate="lint", passed=True, findings=[])))
    monkeypatch.setattr(R, "make_repair_task", lambda *a, **k: SimpleNamespace(label="repair"))
    monkeypatch.setattr(R, "generate_for", lambda c, t: GenerationResult(ok=True, label="repair"))
    out = R.build_with_repair(ctx, round_index=1, label="r01")
    assert "build.harness_retry" not in events and "repair.attempt" in events and len(out.attempts) == 1, events
