"""LIVE (needs gemini keys): the two measurements docs/COST.md quotes.

    python -m pytest tests/cost/test_live.py -q -m live
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse.contracts.chat import ChatMessage, ChatRequest
from codeverse.cost.guard import estimate_call

pytestmark = pytest.mark.live

REPO = Path(__file__).resolve().parents[2]


def _stable_text() -> str:
    from codeverse.judges.prompt_builder import build_system_prompt
    from codeverse.judges.rubrics import load_rubric

    return "\n".join([
        build_system_prompt(load_rubric("static_object_v1")),
        (REPO / "codeverse/prompts/blender/contract.md").read_text(),
        (REPO / "codeverse/prompts/blender/cookbook.md").read_text(),
    ])


def _send(model, text: str):
    return model.generate(ChatRequest(messages=[ChatMessage.user(text)], max_output_tokens=1100,
                                      temperature=0.0, thinking="low", label="cost-test")).usage


def test_estimate_is_within_15_percent_of_the_bill():
    """The pre-send estimate has to be good enough to route on."""
    from codeverse.models import get_chat_model

    text = _stable_text()[:20_000]
    est = estimate_call("gemini:gemini-3.7-flash", prompt=text, output_tokens=40)
    usage = _send(get_chat_model("gemini:gemini-3.7-flash"),
                  text + "\n\nReply with ONE JSON object {\"ok\": true}.")
    assert abs(est.input_tokens - usage.input_tokens) / usage.input_tokens < 0.15


# --------------------------------------------------------------- the metered run loop
def test_a_metered_run_reconciles_with_its_own_record(tmp_path: Path):
    """LIVE end-to-end: a real (cheap) run's ledger must equal record.total_usage.

    This is the invariant docs/COST.md §12 reports at 0.000% on two fresh runs;
    the threshold here is the 1% the wave asked for."""
    from typer.testing import CliRunner

    from codeverse.cli.main import app
    from codeverse.cost.ledger import load_ledger
    from codeverse.record.record import load_record
    from codeverse.workspace import Workspace

    runs = tmp_path / "runs"
    r = CliRunner().invoke(app, [
        "make", "a smooth grey ceramic bowl", "--track", "static_object", "--language", "blender",
        "--generator", "single-shot:gemini:gemini-3.7-flash", "--judge", "gemini:gemini-3.7-flash",
        "--rounds", "0", "--max-minutes", "15",
        "--runs-dir", str(runs), "--slug", "cost_live_bowl"])
    assert r.exit_code in (0, 1), r.output  # a failed judge/build is still a metered run
    ws = Workspace(runs / "cost_live_bowl")
    rows = load_ledger(ws.root)
    assert rows and (ws.root / "telemetry" / "cost.jsonl").is_file()
    assert all(row.run == "cost_live_bowl" for row in rows)
    assert {row.stage.value for row in rows} >= {"plan", "baseline"}
    recorded = load_record(ws).total_usage.cost_usd
    ledger = sum(row.cost_usd for row in rows)
    assert recorded > 0 and abs(ledger - recorded) / recorded < 0.01, f"{ledger} vs {recorded}"
