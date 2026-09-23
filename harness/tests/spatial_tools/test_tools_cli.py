"""`3dcode tools <name>`: the three-state panel and the exit code behind it."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from codeverse3d.cli._common import console, print_observation
from codeverse3d.spatial.registry import Observation, ToolContext


def _panel_title(obs: Observation) -> str:
    with console.capture() as cap:
        print_observation(obs, as_json=False)
    return cap.get().splitlines()[0]


def test_print_observation_shows_three_states() -> None:
    """ok / FAIL / error in the panel title — the split the MCP server reports as ``is_error``."""
    assert "ok" in _panel_title(Observation(ok=True, text="fine"))
    assert "FAIL" in _panel_title(Observation(ok=False, text="connectivity: FAIL — 1 error(s)"))
    assert "error" in _panel_title(Observation(ok=False, failed=True, text="measure: no GLB"))
    # --json-out carries both flags, so a script does not have to parse the panel
    with console.capture() as cap:
        print_observation(Observation(ok=False, text="connectivity: FAIL"), as_json=True)
    data = json.loads(cap.get())
    assert data["ok"] is False and data["failed"] is False


def test_cli_exit_code_follows_the_verdict_not_the_failure(stool_ctx: ToolContext) -> None:
    """`3dcode tools` exits 1 on ``not ok`` — a FAIL verdict included (docs/COST.md §30)."""
    from codeverse3d.cli.main import app

    runner = CliRunner()
    ws = str(stool_ctx.workspace.root)
    good = runner.invoke(app, ["tools", "measure", "--workspace", ws])
    assert good.exit_code == 0, good.output
    verdict = runner.invoke(app, ["tools", "check_connectivity", "--workspace", ws])
    assert verdict.exit_code == 1 and "Leg_3" in verdict.output          # the gate ran, and it FAILs
    broken = runner.invoke(app, ["tools", "isolate", "--json", '{"part": "Nope"}', "--workspace", ws])
    assert broken.exit_code == 1 and "unknown part" in broken.output
