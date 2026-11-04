from __future__ import annotations

from pathlib import Path

from codeverse3d.contracts.artifacts import RenderSet, RenderView
from codeverse3d.spatial.observe import render_observation


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
