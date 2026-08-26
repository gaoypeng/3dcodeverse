"""Per-turn reconstruction of an api-agent transcript.

The ledger's ``latency_ms`` is the stopwatch of the SUCCESSFUL attempt only
(``gemini.py:_once``); backoff sleeps, failed attempts and KeyPool waits are not
recorded anywhere.  Transcript rows are stamped when the model returns
(``assistant``) and when a tool finishes (``tool_result``), so
``model_wall = t(assistant) - t(previous row)`` and ``wait = model_wall - latency``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Turn:
    turn: int
    model_wall_s: float
    latency_s: float
    wait_s: float
    tools: list[tuple[str, float]] = field(default_factory=list)
    error_s: float = 0.0  # time spent in a call that ended in a model_error row


def is_gemini_api_session(rows: list[dict[str, Any]]) -> bool:
    """Only in-process api-agent sessions on Gemini stamp a per-turn latency."""
    return any(r.get("kind") == "assistant" and (r.get("usage") or {}).get("backend") == "gemini" for r in rows)


def parse_turns(rows: list[dict[str, Any]]) -> list[Turn]:
    turns: list[Turn] = []
    if not rows or not is_gemini_api_session(rows):
        return turns
    prev_end = float(rows[0]["t"])
    cur: Turn | None = None
    tool_start = prev_end
    pending_err = 0.0
    for r in rows:
        t = float(r["t"])
        kind = r.get("kind")
        if kind == "assistant":
            wall = max(0.0, t - prev_end)
            lat = float((r.get("usage") or {}).get("latency_ms") or 0) / 1000.0
            cur = Turn(int(r.get("turn", len(turns))), wall, lat, max(0.0, wall - lat), error_s=pending_err)
            pending_err = 0.0
            turns.append(cur)
            prev_end = tool_start = t
        elif kind == "tool_result":
            if cur is not None:
                cur.tools.append((str(r.get("name", "?")), max(0.0, t - tool_start)))
            tool_start = prev_end = t
        elif kind == "model_error":
            pending_err += max(0.0, t - prev_end)
            prev_end = t
        elif kind in ("nudge", "compact", "system"):
            prev_end = t
    if pending_err > 0:  # session died in a model_error after the last good turn
        turns.append(Turn(len(turns), 0.0, 0.0, 0.0, error_s=pending_err))
    return turns


def split(turns: list[Turn]) -> tuple[float, float, float]:
    """(model thinking s, tool execution s, waiting s) over a session."""
    think = sum(t.latency_s for t in turns)
    tools = sum(d for t in turns for _, d in t.tools)
    wait = sum(t.wait_s + t.error_s for t in turns)
    return think, tools, wait
