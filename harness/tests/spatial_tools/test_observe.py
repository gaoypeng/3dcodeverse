from __future__ import annotations

from pathlib import Path

from codeverse.contracts.artifacts import GateFinding, GateReport, RenderSet, RenderView, Severity
from codeverse.spatial.observe import (
    fmt_numbers,
    gate_observation,
    image_budget,
    rel_path,
    render_observation,
    sanitize_text,
    tail_lines,
    truncate,
)


def test_fmt_numbers_compact() -> None:
    s = fmt_numbers({"a": 1.23456789, "b": [0.1, 0.2], "c": True, "d": {"x": 1, "y": {"z": {"deep": 1}}}, "e": "x" * 100})
    assert "a=1.2346" in s and "b=[0.1, 0.2]" in s and "c=yes" in s and "{1 keys}" in s and "…" in s


def test_truncate_keeps_head_and_tail() -> None:
    text = "\n".join(f"line {i}" for i in range(500))
    t = truncate(text, 400)
    assert t.startswith("line 0") and t.endswith("line 499") and "chars omitted" in t
    assert truncate("short") == "short"
    assert tail_lines("a\nb\nc\nd", 2) == "c\nd"


def test_image_budget_keeps_sheet_first() -> None:
    imgs = [f"/x/{i}.png" for i in range(10)]
    out = image_budget(imgs, max_n=3)
    assert out == imgs[:3]


def test_paths_are_workspace_relative(tmp_path: Path) -> None:
    p = tmp_path / "artifacts" / "object.glb"
    assert rel_path(p, tmp_path) == "artifacts/object.glb"
    assert rel_path("/elsewhere/file.py", tmp_path) == "file.py"
    assert sanitize_text(f"error in {tmp_path}/src/model.py:3", tmp_path) == "error in src/model.py:3"


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


def test_render_observation(tmp_path: Path) -> None:
    rs = RenderSet(views=[RenderView(name="front", path=str(tmp_path / "a/view_front.png"), camera_position=(0, 1, 2), fov=35)],
                   contact_sheet=str(tmp_path / "a/sheet.png"), renderer="swiftshader")
    obs = render_observation(rs, tmp_path, note="hello")
    assert obs.ok and obs.images[0].endswith("sheet.png") and len(obs.images) == 2
    assert "a/view_front.png" in obs.text and str(tmp_path) not in obs.text
    assert "cam=(0, 1, 2)" in obs.text
