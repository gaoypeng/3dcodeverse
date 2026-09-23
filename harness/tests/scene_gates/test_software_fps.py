"""A frame rate measured on a CPU rasteriser is the box's, not the scene's."""

from __future__ import annotations

from codeverse3d.contracts.artifacts import RenderSet

GPU = "ANGLE (NVIDIA Corporation, NVIDIA RTX 6000 Ada Generation, Vulkan)"
CPU = "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)"


def test_the_gate_does_not_fire_on_a_cpu_frame_rate() -> None:
    from types import SimpleNamespace

    from codeverse3d.contracts.artifacts import GateReport
    from codeverse3d.contracts.plan import BBox, CameraPlan, ScenePlan, ZonePlan
    from codeverse3d.tracks.scene import ScenePipeline

    bb = BBox(center=(0, 0, 0), extents=(10, 5, 10))
    plan = ScenePlan(title="t", summary="s", setting="meadow", mood="calm", bounds=bb, environment="sunny",
                     zones=[ZonePlan(name="Yard", description="d", bbox=bb, contents=[])], assets=[],
                     cameras=[CameraPlan(name="overview", position=(1, 1, 1), look_at=(0, 0, 0), fov=50, purpose="p")],
                     animation=[], effects=[])
    # frame_gate reads metrics.json from the render dir; with no views there is none,
    # so a report with no findings is the right stand-in and the events sink is a no-op
    ctx = SimpleNamespace(
        plan=plan, ws=None, extra={},
        services=SimpleNamespace(frame_gate=lambda r: GateReport(gate='scene_frames', passed=True, findings=[])),
        events=SimpleNamespace(emit=lambda *a, **k: None),
    )

    def _fps_findings(renders: RenderSet) -> list[str]:
        reports = ScenePipeline().post_render_gates(ctx, 0, renders)  # type: ignore[arg-type]
        return [f.message for r in reports for f in r.findings if "frame rate" in f.message]

    assert _fps_findings(RenderSet(renderer=CPU, fps=2.0)) == [], "SwiftShader's 2 fps is the box"
    assert _fps_findings(RenderSet(renderer=GPU, fps=11.5)), "a real GPU measurement still gates"


def test_the_judge_is_told_a_cpu_number_is_not_the_scene() -> None:
    """The line stays (a heavy scene must stay visible), labelled, with an instruction not to raise an issue."""
    from codeverse3d.judges.prompt_builder import view_rig_section

    cpu = view_rig_section(RenderSet(renderer=CPU, fps=2.0), [], scene=True)
    gpu = view_rig_section(RenderSet(renderer=GPU, fps=11.5), [], scene=True)
    assert "CPU rasteriser" in cpu and "do not raise a performance issue" in cpu
    assert "measured 12 fps." in gpu and "CPU rasteriser" not in gpu


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
    from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
    from codeverse3d.spatial.probes import _result

    harness = GateReport(gate="scene_probe", passed=False, findings=[GateFinding(
        gate="scene_probe", severity=Severity.ERROR, target="src/scene.js",
        message="scene probe produced no result (driver output lost)",
        fix_hint="retry or report", data={"harness_failure": True})])
    r = _result(harness, {})
    assert r.ok is False
    assert r.errors == ["scene probe produced no result (driver output lost)"]
    assert r.findings == [], "a harness failure is never handed to the agent as a defect"

    real = GateReport(gate="scene_probe", passed=False, findings=[GateFinding(
        gate="scene_probe", severity=Severity.ERROR, target="src/scene.js",
        message="[createScene] TypeError: x is not a function", fix_hint="fix it")])
    r2 = _result(real, {"totals": {}})
    assert r2.ok is True and r2.errors == [] and len(r2.findings) == 1
