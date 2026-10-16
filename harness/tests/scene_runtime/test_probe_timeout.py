"""D111: a scene probe that runs out of its budget is a verdict on the code, not a harness failure.

Argon loop iteration 4, r1_08 (2026-10-15): the refine's new 37 k-vertex terrain met the zones'
per-vertex raycast seating and the scene stopped booting inside the 120 s probe budget.  The probe
called it "a harness/driver failure, not your code", the agent stopped working, and the round spent
3 x 240 s rebuilding identical code and was never scored.  After the driver's one retry (on a browser
of its own) a timeout INSIDE the scene's own code — the watchdog's ``phase`` — is now a typed ERROR
against the entry file, so the round's repair session receives it; a timeout anywhere else (browser
launch, page ready, the shader preflight, node itself starved) and any census-GLB boot stay the box's.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import codeverse3d.spatial.probes as probes_mod
import codeverse3d.spatial.render_scene as RS
from codeverse3d.contracts.artifacts import GateReport
from codeverse3d.languages.scene_threejs import SceneThreeJsRuntime
from codeverse3d.spatial.node import OWN_BROWSER_ENV, NodeError, NodeResult
from codeverse3d.spatial.render_scene import SCENE_PHASES, SceneRenderError, run_scene_script
from codeverse3d.tracks import repair as R
from codeverse3d.tracks.generation import GenerationResult
from tests.scene_runtime.conftest import needs_browser

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


def test_the_process_clock_is_the_box(monkeypatch):
    """Node's own watchdog runs 20 s inside the process clock; when the process clock fires, node itself
    was starved — no phase, never the code's."""
    def starved(path, args, *, cwd, timeout_s, env_extra, check):
        raise NodeError("timed out", result=NodeResult(rc=-9, stdout="", stderr="", last_json=None, duration_ms=1,
                                                       timed_out=True))

    monkeypatch.setattr(RS, "run_node", starved)
    with pytest.raises(SceneRenderError) as ei:
        run_scene_script("probe_scene.mjs", ["--ws", "x"], timeout_s=10)
    assert ei.value.timed_out and ei.value.phase == "" and not ei.value.scene_timed_out


def test_a_driver_that_fails_otherwise_is_not_timed_out(monkeypatch):
    _feed(monkeypatch, [NodeResult(rc=2, stdout="", stderr="", last_json={"ok": False, "error": "--ws is required"},
                                   duration_ms=1)])
    with pytest.raises(SceneRenderError) as ei:
        run_scene_script("probe_scene.mjs", [], timeout_s=10)
    assert ei.value.timed_out is False


@pytest.mark.node
@needs_browser
def test_the_driver_watchdog_names_the_phase_it_fired_in(starter_ws):
    """A real ``probe_scene.mjs`` boot whose createScene() blocks the page: the watchdog fires inside
    the scene's code and says so.  (The block is bounded, so the page frees itself after the test.)"""
    from codeverse3d.spatial.node import run_node

    (starter_ws.src / "scene.js").write_text(
        "import * as THREE from 'three';\n"
        "export function createScene() { const end = Date.now() + 25000; while (Date.now() < end) {}\n"
        "  return { scene: new THREE.Scene(), cameras: [], update() {} }; }\n")
    r = run_node(RS.runtime_js_dir() / "probe_scene.mjs", ["--ws", str(starter_ws.root), "--timeout-ms", "12000"],
                 cwd=RS.runtime_js_dir(), timeout_s=60, env_extra=dict(OWN_BROWSER_ENV), check=False)
    assert r.rc == 3 and r.summary.get("timed_out") is True and r.summary.get("phase") == "scene", r.summary


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


def test_a_census_glb_that_never_boots_stays_the_harnesss(tmp_ws, monkeypatch):
    """scene_blender: the author's bpy already ran in Blender; the GLB the probe loads is the harness's
    export, and a three.js hint ("createScene, update") names nothing a bpy author has."""
    monkeypatch.setattr(probes_mod, "run_scene_script", lambda script, args, **kw: (_ for _ in ()).throw(_timed_out("scene")))
    probe, _, _ = probes_mod.run_probe(tmp_ws, timeout_s=120, glb=tmp_ws.artifacts / "census.glb")
    assert probe.findings[0].data.get("harness_failure") is True and probe.findings[0].data.get("kind") != "probe_timeout"


def test_the_probe_tool_and_the_build_share_one_budget(tmp_ws, monkeypatch):
    """The ``scene_probe`` tool probed at 60 s while the build allowed 120 s: with a timeout now the code's,
    the agent's own tool would condemn a scene the build accepts."""
    from codeverse3d.languages.scene_threejs import write_example

    write_example(tmp_ws)
    budgets: list[str] = []

    def capture(script, args, **kw):
        budgets.append(args[args.index("--timeout-ms") + 1])
        raise SceneRenderError("stop here")

    monkeypatch.setattr(probes_mod, "run_scene_script", capture)
    rt = SceneThreeJsRuntime()
    rt.probe(tmp_ws)
    rt.build(tmp_ws)
    assert len(budgets) == 2 and budgets[0] == budgets[1], budgets


def test_the_scene_build_hands_a_timeout_to_repair_not_to_rebuilds(tmp_ws, monkeypatch):
    """The scene_threejs build's BuildResult is not a harness failure, so ``build_with_repair`` runs a
    repair session on the first pass instead of MAX_HARNESS_REBUILDS identical rebuilds."""
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
