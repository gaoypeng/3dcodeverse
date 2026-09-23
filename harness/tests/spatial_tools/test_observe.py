from __future__ import annotations

from pathlib import Path

from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse3d.spatial.observe import (
    build_failure_lines,
    fmt_numbers,
    gate_observation,
    image_budget,
    render_observation,
)


def test_fmt_numbers_compact() -> None:
    s = fmt_numbers({"a": 1.23456789, "b": [0.1, 0.2], "c": True, "d": {"x": 1, "y": {"z": {"deep": 1}}}, "e": "x" * 100})
    assert "a=1.2346" in s and "b=[0.1, 0.2]" in s and "c=yes" in s and "{1 keys}" in s and "…" in s


def test_image_budget_keeps_sheet_first() -> None:
    imgs = [f"/x/{i}.png" for i in range(10)]
    out = image_budget(imgs, max_n=3)
    assert out == imgs[:3]


def test_gate_observation_errors_first() -> None:
    rep = GateReport(gate="g", passed=False, findings=[
        GateFinding(gate="g", severity=Severity.INFO, message="fyi"),
        GateFinding(gate="g", severity=Severity.WARN, target="A", message="warnish", fix_hint="do x"),
        GateFinding(gate="g", severity=Severity.ERROR, target="B", message="bad", fix_hint="do y", data={"gap_m": 0.01}),
    ])
    obs = gate_observation(rep)
    assert not obs.ok
    lines = obs.text.splitlines()
    assert "FAIL" in lines[0] and "1 error(s), 1 warning(s)" in lines[0]
    assert lines[1].startswith("- ERROR [B]") and "fix: do y" in lines[2]
    assert obs.numbers["errors"] == 1 and obs.numbers["error_data"]["B"]["gap_m"] == 0.01


def test_render_with_no_views_is_a_failure_console_errors_are_a_verdict(tmp_path: Path) -> None:
    """No views and no log: the tool failed.  Console errors (even with zero views): a verdict the agent fixes."""
    empty = render_observation(RenderSet(views=[], renderer="fake"), tmp_path, note="shaded render")
    assert not empty.ok and empty.failed
    assert empty.text.startswith("RENDER PRODUCED NO VIEWS (fake)")
    assert empty.numbers["n_views"] == 0

    rs = RenderSet(views=[RenderView(name="front", path=str(tmp_path / "v.png"))], renderer="fake",
                   console_errors=["TypeError: x is undefined"])
    obs = render_observation(rs, tmp_path, note="scene views")
    assert not obs.ok and not obs.failed
    assert obs.text.startswith("RENDER: FAIL — 1 console error(s); the views below rendered anyway")
    assert "scene views" in obs.text and "TypeError" in obs.text

    booted = render_observation(RenderSet(views=[], renderer="fake", console_errors=["SyntaxError: scene.js:12"]),
                                tmp_path)
    assert not booted.ok and not booted.failed          # the scene told the agent what to fix
    assert booted.text.startswith("RENDER: FAIL — 1 console error(s) and no view rendered")
    assert "SyntaxError" in booted.text


def test_build_failure_lines_relative_file_and_tail_dedupe(tmp_path: Path) -> None:
    # (ii) an already-relative error_file is shown as-is from any cwd (rel_path would give the bare name)
    # (iii) a stderr tail that is already part of the message is not repeated
    log = "0:4: 'nope' undeclared"
    br = BuildResult(ok=False, language="glsl_shader", error_type="GlslCompileError", error_file="src/shader.frag",
                     error_line=4, error_message="GLSL compile error:\n" + log, stderr_tail=log)
    lines = build_failure_lines(br, tmp_path, ["- WARN: w"], tail_n=25)
    assert lines == ["BUILD FAILED: GlslCompileError: GLSL compile error: at src/shader.frag:4", log, "lint hints:\n- WARN: w"]
    br2 = br.model_copy(update={"error_file": str(tmp_path / "src" / "a.py"), "error_message": "boom", "stderr_tail": "l1\nl2"})
    lines = build_failure_lines(br2, tmp_path, [], tail_n=1)
    assert lines == ["BUILD FAILED: GlslCompileError: boom at src/a.py:4", "stderr (tail):\nl2"]
