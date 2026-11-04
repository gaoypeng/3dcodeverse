"""`3dcode tools <name>`: the three-state panel and the exit code behind it."""

from __future__ import annotations

from typer.testing import CliRunner

from codeverse3d.spatial.registry import ToolContext


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
