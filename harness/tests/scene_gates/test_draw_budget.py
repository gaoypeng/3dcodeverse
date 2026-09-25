"""The scene draw budget is a census count gated in code (D99), and the probe's harness failures stay harness failures."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.artifacts import GateReport, RenderSet, Severity
from codeverse3d.conventions import DRAWS_WARN_SCENE, MAX_DRAWS_SCENE


def _summary(draws: int) -> dict:
    groups = [{"name": "Market", "named": True, "kind": "content", "draws": draws - 10},
              {"name": "Harbour", "named": True, "kind": "content", "draws": 10}]
    return {"boot": {"ok": True, "stage": "ready"}, "update_ok": True,
            "census": {"totals": {"meshes": draws, "lights": 1, "draws": draws}, "groups": groups}}


@pytest.mark.parametrize("draws, severity, limit", [
    (DRAWS_WARN_SCENE, None, None),
    (DRAWS_WARN_SCENE + 1, Severity.WARN, DRAWS_WARN_SCENE),
    (MAX_DRAWS_SCENE, Severity.WARN, DRAWS_WARN_SCENE),
    (MAX_DRAWS_SCENE + 1, Severity.ERROR, MAX_DRAWS_SCENE),
])
def test_the_probe_gates_the_census_draw_count(draws: int, severity: Severity | None, limit: int | None) -> None:
    """The finding states the measured count, the threshold, where the draws are and the fix."""
    from codeverse3d.spatial.probes import probe_report

    report, _ = probe_report(_summary(draws))
    hits = [f for f in report.findings if f.data.get("kind") == "draw_calls"]
    if severity is None:
        assert hits == [] and report.passed
        return
    (f,) = hits
    assert f.severity == severity and report.passed is (severity != Severity.ERROR)
    assert f.data["draws"] == draws and f.data["limit"] == limit
    for text in (f"{draws:,}", f"{limit:,}", "Market", "InstancedMesh", "mergeGeometries", "lib/merge.js", "lib/instancing.js"):
        assert text in f.message, text


def test_a_draw_budget_error_fails_the_build_into_the_repair_report(tmp_path, monkeypatch) -> None:
    """The ERROR reaches the agent where round 0 fixes it: a failed build, whose repair report quotes it."""
    import codeverse3d.spatial.probes as probes
    from codeverse3d.contracts.common import Language
    from codeverse3d.languages.base import get_runtime
    from codeverse3d.tracks.repair import format_error_report
    from codeverse3d.workspace import Workspace

    ws = Workspace(tmp_path / "ws")
    (ws.root / "src").mkdir(parents=True, exist_ok=True)
    (ws.root / "src" / "scene.js").write_text("export function createScene() {}\n")
    draws = MAX_DRAWS_SCENE * 3

    def fake_run_probe(_ws, *, compile=False, timeout_s=None):
        probe, census = probes.probe_report(_summary(draws))
        return probe, GateReport(gate=probes.SHADER_GATE, passed=True, findings=[]), census

    monkeypatch.setattr(probes, "run_probe", fake_run_probe)
    build = get_runtime(Language.SCENE_THREEJS).build(ws)
    assert not build.ok and not build.harness_failure and build.error_type == "draw_calls"
    report = format_error_report(build, GateReport(gate="lint", passed=True, findings=[]))
    assert f"{draws:,} draw calls" in report and f"{MAX_DRAWS_SCENE:,} budget" in report and "InstancedMesh" in report


def test_fps_is_neither_gated_nor_judged() -> None:
    """A frame rate is a record only (D99): no render gate and no judge line reads it."""
    from types import SimpleNamespace

    from codeverse3d.judges.prompt_builder import view_rig_section
    from codeverse3d.tracks.scene import ScenePipeline

    ctx = SimpleNamespace(plan=None, services=SimpleNamespace(frame_gate=lambda r: GateReport(gate="scene_frames", passed=True, findings=[])))
    slow = RenderSet(renderer="ANGLE (NVIDIA RTX 6000 Ada Generation)", fps=0.5)
    assert [f for r in ScenePipeline().post_render_gates(ctx, 0, slow) for f in r.findings] == []  # type: ignore[arg-type]
    assert "fps" not in view_rig_section(slow, [], scene=True)


def test_a_probe_with_no_boot_record_is_a_harness_failure_not_a_verdict() -> None:
    """No `boot` in the driver summary means the driver output was lost, not that the scene failed to boot."""
    from codeverse3d.spatial.probes import probe_report

    report, census = probe_report({})
    assert not report.passed and census == {}
    assert len(report.findings) == 1
    f = report.findings[0]
    assert "no result" in f.message and "did not boot" not in f.message
    assert f.data.get("harness_failure") is True

    # a REAL boot failure still reads as one, with its stage
    real, _ = probe_report({"boot": {"ok": False, "stage": "createScene", "error": "TypeError: x"}})
    assert not real.passed and "[createScene] TypeError: x" in real.findings[0].message


def test_a_harness_failure_finding_makes_the_probe_result_not_ok() -> None:
    """`ok` is "the tool could run"; a harness failure is an error, never an agent-facing finding."""
    from codeverse3d.contracts.artifacts import GateFinding
    from codeverse3d.spatial.probes import probe_result

    harness = GateReport(gate="scene_probe", passed=False, findings=[GateFinding(
        gate="scene_probe", severity=Severity.ERROR, target="src/scene.js",
        message="scene probe produced no result (driver output lost)",
        fix_hint="retry or report", data={"harness_failure": True})])
    r = probe_result(harness, {})
    assert r.ok is False
    assert r.errors == ["scene probe produced no result (driver output lost)"]
    assert r.findings == [], "a harness failure is never handed to the agent as a defect"

    real = GateReport(gate="scene_probe", passed=False, findings=[GateFinding(
        gate="scene_probe", severity=Severity.ERROR, target="src/scene.js",
        message="[createScene] TypeError: x is not a function", fix_hint="fix it")])
    r2 = probe_result(real, {"totals": {}})
    assert r2.ok is True and r2.errors == [] and len(r2.findings) == 1
