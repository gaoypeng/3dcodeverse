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
import statistics
import sys
from collections import Counter
from pathlib import Path

for _p in (Path(__file__).resolve().parents[2] / "harness", Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(_p))  # this tree's codeverse3d (harness/) + the `bench` package (eval/)

from bench._records import records  # noqa: E402
from codeverse3d.proc import read_json_or_none  # noqa: E402
from codeverse3d.record.record import unique_files  # noqa: E402

#: gemini-cli names an MCP tool ``mcp_<server>_<tool>``; ours is the ``3dcode`` server
#: (``3dcv`` in every session recorded before the 2026-09-21 rename).
MCP_PREFIXES = ("mcp_3dcode_", "mcp_3dcv_")


def _stdouts(root: Path) -> list[Path]:
    """Every session's ``stdout.json`` under ``root``, once per file on disk — the walk is
    ``flywheel.record.unique_files`` (follows the ``telemetry/trajectories`` symlink and
    collapses it; skips sub-workspaces), so no count here can double by layout."""
    return [p for p in unique_files(root, "stdout.json") if "trajectories" in p.parts]


def _killed(root: Path) -> int:
    """Sessions whose ``stdout.json`` is empty: the CLI died or was killed before printing
    its stats block.  They carry no numbers, so they are not sessions for the rates above —
    but they are not nothing either, and a battery with many of them was a bad window."""
    return sum(1 for p in _stdouts(root) if p.stat().st_size <= 2)


def _sessions(root: Path) -> list[Path]:
    """The sessions with a stats block (a killed one is counted by :func:`_killed`)."""
    return [p for p in _stdouts(root) if p.stat().st_size > 2]


def tool_rates(sessions: list[Path]) -> tuple[Counter, Counter]:
    """(calls, reported-as-error) per MCP tool, over the CLI's own stats block."""
    calls: Counter = Counter()
    failed: Counter = Counter()
    for path in sessions:
        stats = (read_json_or_none(path) or {}).get("stats") or {}
        for name, row in ((stats.get("tools") or {}).get("byName") or {}).items():
            prefix = next((p for p in MCP_PREFIXES if name.startswith(p)), None)
            if prefix is None:
                continue
            calls[name[len(prefix):]] += int(row.get("count") or 0)
            failed[name[len(prefix):]] += int(row.get("fail") or 0)
    return calls, failed


def token_rates(sessions: list[Path]) -> dict[str, float]:
    """Prompt/cached tokens and requests for each session's MAIN model.

    ``agents.backends._model_usage`` owns the envelope — including the older
    ``tokens.input`` schema this file would otherwise read as zero — so the tokens come
    from it rather than from a second parser.  A session's ``models`` map also carries
    gemini-cli's own bookkeeping model (one request per session for the session title),
    and docs/COST.md §30 counts main-role requests: the model with the most requests IS
    the main role, and mixing the other in moves ``uncached per request`` by ~3 %.
    """
    from codeverse3d.agents.backends import _model_usage

    prompt = cached = requests = 0
    for path in sessions:
        models = (((read_json_or_none(path) or {}).get("stats") or {}).get("models")) or {}
        if not models:
            continue
        main = max(models, key=lambda n: int(((models[n] or {}).get("api") or {}).get("totalRequests") or 0))
        usage = _model_usage((models[main] or {}).get("tokens") or {}, main)
        prompt += usage.input_tokens
        cached += usage.cached_tokens
        requests += int(((models[main] or {}).get("api") or {}).get("totalRequests") or 0)
    return {"requests": requests, "prompt": prompt, "cached": cached,
            "cache_hit": cached / prompt if prompt else float("nan"),
            "uncached_per_request": (prompt - cached) / requests if requests else float("nan")}


def round_costs(root: Path) -> list[float]:
    """Generator dollars per round, from the run records (judging is billed elsewhere)."""
    out: list[float] = []
    for _, data in records(root):
        out += [float((r.get("usage") or {}).get("cost_usd") or 0.0) for r in data.get("rounds") or []
                if (r.get("usage") or {}).get("cost_usd")]
    return out


def report(roots: list[Path]) -> str:
    lines = ["| battery | sessions | killed | MCP calls | reported as errors | requests | cache hit | "
             "uncached/req | rounds | $ per round (median) |", "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    per_tool: dict[str, tuple[Counter, Counter]] = {}
    for root in roots:
        sessions = _sessions(root)
        calls, failed = tool_rates(sessions)
        per_tool[root.name] = (calls, failed)
        tok = token_rates(sessions)
        costs = round_costs(root)
        n_calls, n_failed = sum(calls.values()), sum(failed.values())
        lines.append(
            f"| {root.name} | {len(sessions)} | {_killed(root)} | {n_calls} | "
            f"{n_failed / n_calls:.3f} ({n_failed}) | {tok['requests']} | {tok['cache_hit']:.2f} | "
            f"{tok['uncached_per_request']:,.0f} | {len(costs)} | "
            f"{statistics.median(costs):.3f} |" if n_calls and costs else
            f"| {root.name} | {len(sessions)} | {_killed(root)} | {n_calls} | — | {tok['requests']} | — | — | "
            f"{len(costs)} | — |")
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
