"""What a battery's recorded agent sessions cost, and how many of their tool calls came
back as errors.

The MCP verdict fix (docs/COST.md §30) was measured through this view, not through the
judge: a tool that ran and answered FAIL used to arrive as a broken call, the vendor CLI
retried it, and its error path re-sent the attached images as base64 TEXT.  The readout is
per SESSION (``trajectories/*/stdout.json``, the CLI's own tally) and per ROUND
(``record.json``'s ``usage``), so it needs no judge and no re-run.

    python bench/session_stats.py bench/out/wave2_lean bench/out/compare_art_v4

Counts each session once even though ``run/telemetry/trajectories`` symlinks the same
directory (a glob that follows it doubles every number).
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

#: gemini-cli names an MCP tool ``mcp_<server>_<tool>``; ours is the ``3dcv`` server.
MCP_PREFIX = "mcp_3dcv_"


def _sessions(root: Path) -> list[Path]:
    """Every ``stdout.json`` under ``root``, deduplicated by resolved path."""
    seen: dict[Path, Path] = {}
    for p in root.rglob("stdout.json"):
        if "trajectories" not in p.parts:
            continue
        seen.setdefault(p.resolve(), p)
    return sorted(seen.values())


def tool_rates(sessions: list[Path]) -> tuple[Counter, Counter]:
    """(calls, reported-as-error) per MCP tool, over the CLI's own stats block."""
    calls: Counter = Counter()
    failed: Counter = Counter()
    for path in sessions:
        try:
            stats = (json.loads(path.read_text()) or {}).get("stats") or {}
        except (OSError, ValueError):
            continue
        for name, row in ((stats.get("tools") or {}).get("byName") or {}).items():
            if not name.startswith(MCP_PREFIX):
                continue
            calls[name[len(MCP_PREFIX):]] += int(row.get("count") or 0)
            failed[name[len(MCP_PREFIX):]] += int(row.get("fail") or 0)
    return calls, failed


def token_rates(sessions: list[Path]) -> dict[str, float]:
    """Prompt/cached tokens and requests summed over every model a session used."""
    prompt = cached = requests = 0
    for path in sessions:
        try:
            models = ((json.loads(path.read_text()) or {}).get("stats") or {}).get("models") or {}
        except (OSError, ValueError):
            continue
        for m in models.values():
            tok, api = m.get("tokens") or {}, m.get("api") or {}
            prompt += int(tok.get("prompt") or 0)
            cached += int(tok.get("cached") or 0)
            requests += int(api.get("totalRequests") or 0)
    return {"requests": requests, "prompt": prompt, "cached": cached,
            "cache_hit": cached / prompt if prompt else float("nan"),
            "uncached_per_request": (prompt - cached) / requests if requests else float("nan")}


def round_costs(root: Path) -> list[float]:
    """Generator dollars per round, from the run records (judging is billed elsewhere)."""
    out: list[float] = []
    seen: set[Path] = set()
    for rec in root.rglob("record.json"):
        if rec.resolve() in seen:
            continue
        seen.add(rec.resolve())
        try:
            rounds = (json.loads(rec.read_text()) or {}).get("rounds") or []
        except (OSError, ValueError):
            continue
        out += [float((r.get("usage") or {}).get("cost_usd") or 0.0) for r in rounds
                if (r.get("usage") or {}).get("cost_usd")]
    return out


def report(roots: list[Path]) -> str:
    lines = ["| battery | sessions | MCP calls | reported as errors | requests | cache hit | "
             "uncached/req | rounds | $ per round (median) |", "|---|--:|--:|--:|--:|--:|--:|--:|--:|"]
    per_tool: dict[str, tuple[Counter, Counter]] = {}
    for root in roots:
        sessions = _sessions(root)
        calls, failed = tool_rates(sessions)
        per_tool[root.name] = (calls, failed)
        tok = token_rates(sessions)
        costs = round_costs(root)
        n_calls, n_failed = sum(calls.values()), sum(failed.values())
        lines.append(
            f"| {root.name} | {len(sessions)} | {n_calls} | "
            f"{n_failed / n_calls:.3f} ({n_failed}) | {tok['requests']} | {tok['cache_hit']:.2f} | "
            f"{tok['uncached_per_request']:,.0f} | {len(costs)} | "
            f"{statistics.median(costs):.3f} |" if n_calls and costs else
            f"| {root.name} | {len(sessions)} | {n_calls} | — | {tok['requests']} | — | — | {len(costs)} | — |")
    for name, (calls, failed) in per_tool.items():
        if not calls:
            continue
        lines += ["", f"{name}, per tool:", "| tool | calls | reported as errors |", "|---|--:|--:|"]
        lines += [f"| {tool} | {n} | {failed[tool] / n:.3f} ({failed[tool]}) |"
                  for tool, n in calls.most_common()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("roots", nargs="+", type=Path, help="battery directories under bench/out")
    ns = ap.parse_args(argv)
    print(report([p for p in ns.roots]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
