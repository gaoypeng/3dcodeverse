"""A frame rate measured on a CPU rasteriser is the box's, not the scene's.

Measured on `bench/out/scene_baseline` (2026-09-05).  With the machine's eight GPUs at
~100 % from other work, the GPU probe's negative verdict is cached for 20 minutes, so
cells fell back to SwiftShader one at a time.  Across four scored cells `fps` was
measured on TWO DIFFERENT RENDERERS:

    japanese_garden  11.5 fps   ANGLE (NVIDIA Corporation, NVIDIA RTX 6000 Ada …)
    rooftop_garden    5.1 fps   ANGLE (Google, … SwiftShader driver)
    medieval_market   7.1 fps   ANGLE (Google, … SwiftShader driver)
    snowy_hut         2.0 fps   ANGLE (Google, … SwiftShader driver)  — 4 frames in 2 s

`render_console` raised "low frame rate" below 20 for all of them and the judge was told
"PROBE: measured N fps" as a fact.  Two of the four judge issues marked **critical** in
that battery were frame-rate complaints, so the box was being scored as the model.
"""

from __future__ import annotations

import pytest

from codeverse.contracts.artifacts import RenderSet

GPU = "ANGLE (NVIDIA Corporation, NVIDIA RTX 6000 Ada Generation, Vulkan)"
CPU = "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)"


@pytest.mark.parametrize("renderer,software", [
    (GPU, False),
    (CPU, True),
    ("Mesa/X.org llvmpipe (LLVM 15.0.7, 256 bits)", True),
    ("ANGLE (Intel, Mesa Intel(R) UHD Graphics)", False),
    ("", False),   # unknown: not claimable as software
])
def test_the_renderer_string_decides(renderer: str, software: bool) -> None:
    assert RenderSet(renderer=renderer, fps=9.0).software_rendered is software


def test_a_cpu_measurement_is_not_offered_as_a_frame_rate() -> None:
    assert RenderSet(renderer=CPU, fps=2.0).hardware_fps is None
    assert RenderSet(renderer=GPU, fps=11.5).hardware_fps == 11.5
    assert RenderSet(renderer=GPU, fps=None).hardware_fps is None


def test_the_gate_does_not_fire_on_a_cpu_frame_rate() -> None:
    from types import SimpleNamespace

    from codeverse.contracts.artifacts import GateReport
    from codeverse.contracts.plan import BBox, CameraPlan, ScenePlan, ZonePlan
    from codeverse.tracks.scene import ScenePipeline

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
    """Dropping it silently would hide a genuinely heavy scene from a reader, so the
    line stays — labelled, with an instruction not to raise an issue from it."""
    from codeverse.judges.prompt_builder import view_rig_section

    cpu = view_rig_section(RenderSet(renderer=CPU, fps=2.0), [], scene=True)
    gpu = view_rig_section(RenderSet(renderer=GPU, fps=11.5), [], scene=True)
    assert "CPU rasteriser" in cpu and "do not raise a performance issue" in cpu
    assert "measured 12 fps." in gpu and "CPU rasteriser" not in gpu
