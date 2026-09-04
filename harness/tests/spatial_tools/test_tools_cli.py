"""`3dcv tools <name>`: the three-state panel and the exit code behind it."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from codeverse.cli._common import console, print_observation
from codeverse.spatial.registry import Observation, ToolContext


def _panel_title(obs: Observation) -> str:
    with console.capture() as cap:
        print_observation(obs, as_json=False)
    return cap.get().splitlines()[0]


def test_print_observation_shows_three_states() -> None:
    """ok / FAIL / error, the same split the MCP server reports as ``is_error``: a human
    reading `3dcv tools check_connectivity` must be able to tell a gate that answered FAIL
    from a tool that could not run, and the panel title is where they look."""
    assert "ok" in _panel_title(Observation(ok=True, text="fine"))
    assert "FAIL" in _panel_title(Observation(ok=False, text="connectivity: FAIL — 1 error(s)"))
    assert "error" in _panel_title(Observation(ok=False, failed=True, text="measure: no GLB"))
    # --json-out carries both flags, so a script does not have to parse the panel
    with console.capture() as cap:
        print_observation(Observation(ok=False, text="connectivity: FAIL"), as_json=True)
    data = json.loads(cap.get())
    assert data["ok"] is False and data["failed"] is False


def test_cli_exit_code_follows_the_verdict_not_the_failure(stool_ctx: ToolContext) -> None:
    """`3dcv tools` exits 1 on ``not ok`` — a FAIL verdict included.  Deliberate, and it
    DID change one command: `scene_probe` used ``ok`` for "the probe tool ran", so a failing
    scene gate now exits 1 where it exited 0 (docs/COST.md §30).  Every other gate tool
    already exited 1 on a FAIL; the exit code speaks to the human or script at the terminal,
    while the MCP boundary — the one that costs money when a verdict is called an error —
    reports ``failed`` alone."""
    from codeverse.cli.main import app

    runner = CliRunner()
    ws = str(stool_ctx.workspace.root)
    good = runner.invoke(app, ["tools", "measure", "--workspace", ws])
    assert good.exit_code == 0, good.output
    verdict = runner.invoke(app, ["tools", "check_connectivity", "--workspace", ws])
    assert verdict.exit_code == 1 and "Leg_3" in verdict.output          # the gate ran, and it FAILs
    broken = runner.invoke(app, ["tools", "isolate", "--json", '{"part": "Nope"}', "--workspace", ws])
    assert broken.exit_code == 1 and "unknown part" in broken.output
