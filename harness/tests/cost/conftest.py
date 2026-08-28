"""A tiny synthetic run directory — the shapes ``reconstruct`` must understand."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def _usage(**kw: object) -> dict[str, object]:
    base = {"backend": "gemini", "model": "gemini-3.7-flash", "input_tokens": 0, "output_tokens": 0,
            "cached_tokens": 0, "thoughts_tokens": 0, "tool_calls": 0, "cost_usd": 0.0, "latency_ms": 0}
    base.update(kw)
    return base


@pytest.fixture
def fake_run(tmp_path: Path) -> Path:
    """plan (event only) + one api-agent baseline session (2 turns) + one judge verdict."""
    ws = tmp_path / "fake_run"
    turns = [
        _usage(input_tokens=10_000, output_tokens=100, cost_usd=10_000 * 0.75e-6 + 100 * 3.75e-6),
        _usage(input_tokens=20_000, cached_tokens=10_000, output_tokens=200,
               cost_usd=10_000 * 0.75e-6 + 10_000 * 0.075e-6 + 200 * 3.75e-6),
    ]
    session = _usage(backend="api-agent", model="gemini:gemini-3.7-flash",
                     input_tokens=30_000, cached_tokens=10_000, output_tokens=300,
                     cost_usd=sum(float(t["cost_usd"]) for t in turns), latency_ms=1000)
    d = ws / "trajectories" / "baseline_r00"
    _write(d / "result.json", {"ok": True, "exit_reason": "completed", "label": "baseline", "round": 0,
                               "kind": "api-agent", "usage": session, "duration_s": 30.0, "turns": 2})
    (d / "transcript.jsonl").write_text("\n".join(
        json.dumps({"t": 100.0 + i, "kind": "assistant", "turn": i, "usage": u}) for i, u in enumerate(turns)))

    judge_usage = _usage(model="gemini-3.1-pro-preview", input_tokens=12_000, output_tokens=1_000,
                         cost_usd=12_000 * 2e-6 + 1_000 * 12e-6, latency_ms=5000)
    plan_cost = 0.01
    total = _usage(input_tokens=42_000, cached_tokens=10_000, output_tokens=1_300,
                   cost_usd=float(session["cost_usd"]) + float(judge_usage["cost_usd"]) + plan_cost)
    _write(ws / "record.json", {
        "spec": {"track": "static_object", "language": "blender",
                 "backends": {"generator": "gemini-cli:gemini-3.6-flash",
                              "judge": "gemini:gemini-3.1-pro-preview"},
                 "budget": {"max_usd": 5.0}},
        "status": "passed", "baseline_score": 0.6, "final_score": 0.8, "best_round": 0,
        "total_usage": total, "extra": {"stop_reason": "pass", "budget": {"elapsed_min": 2.0}},
        "rounds": [{"index": 0, "kind": "baseline", "usage": session, "gates": [],
                    "judgment": {"rubric": "static_object_v1", "judge_backend": "gemini:gemini-3.1-pro-preview",
                                 "overall": 0.8, "passed": True, "n_samples": 1, "usage": judge_usage}}],
    })
    (ws / "events.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"t": 1.0, "event": "run.start", "track": "static_object"},
        {"t": 2.0, "event": "plan.done", "model": "gemini:gemini-3.7-flash", "cost_usd": plan_cost},
        {"t": 3.0, "event": "stage.done", "stage": "plan", "duration_s": 2.0},
        {"t": 40.0, "event": "generate.done", "label": "baseline", "cost_usd": round(float(session["cost_usd"]), 4)},
        {"t": 50.0, "event": "judge.done", "round": 0, "cost_usd": round(float(judge_usage["cost_usd"]), 4),
         "duration_s": 5.0},
        {"t": 51.0, "event": "round.done", "round": 0, "kind": "baseline", "duration_s": 48.0,
         "cost_usd": round(float(session["cost_usd"]) + float(judge_usage["cost_usd"]), 4)},
        {"t": 52.0, "event": "run.done", "cost_usd": round(float(total["cost_usd"]), 4)},
    ]))
    return ws
