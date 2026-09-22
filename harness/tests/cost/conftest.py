"""A tiny synthetic run directory: a record and the ledger its calls wrote."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.contracts.artifacts import Judgment
from codeverse3d.contracts.common import Language, Track, Usage
from codeverse3d.contracts.run import RoundRecord, RunRecord, RunStatus, StepTime
from codeverse3d.contracts.spec import Spec
from codeverse3d.cost.ledger import record_call


@pytest.fixture
def fake_run(tmp_path: Path) -> Path:
    """plan + one gemini-cli baseline session + one judge verdict, each a ledger row."""
    ws = tmp_path / "fake_run"
    ledger = ws / "telemetry" / "cost.jsonl"
    flash = {"backend": "gemini", "model": "gemini-3.7-flash"}
    record_call(Usage(**flash, input_tokens=4_000, output_tokens=900, latency_ms=12_000), run=ws.name,
                stage="plan", label="planner", ledger=ledger)
    record_call(Usage(backend="gemini-cli", model="gemini-3.7-flash", input_tokens=30_000, cached_tokens=10_000,
                      output_tokens=300, latency_ms=30_000), run=ws.name, round=0, stage="baseline",
                label="baseline", source="session", n_calls=2, ledger=ledger)
    record_call(Usage(backend="gemini", model="gemini-3.1-pro-preview", input_tokens=12_000, output_tokens=1_000,
                      latency_ms=5_000), run=ws.name, label="judge:static_object_v1:r00:s0", ledger=ledger)
    spec = Spec(id="fake", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="a chair")
    judgment = Judgment(rubric="static_object_v1", scores={}, overall=0.8, passed=True)
    rec = RunRecord(spec=spec, workspace=str(ws), status=RunStatus.MAX_ROUNDS, extra={"stop_reason": "max_rounds"},
                    steps=[StepTime(step="plan", wall_s=20.0)],
                    rounds=[RoundRecord(index=0, kind="baseline", judgment=judgment,
                                        steps=[StepTime(step="generate", round=0, wall_s=100.0, lost_s=40.0)])])
    (ws / "record.json").write_text(rec.model_dump_json())
    return ws
