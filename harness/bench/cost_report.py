"""Battery cost report: ``python bench/cost_report.py bench/out/<battery> [...]``.

Writes ``cost_report.md`` next to the battery (or ``--out``) and prints the
summary.  Same audit as ``3dcv cost``; this entry point exists so a battery can
be costed from a script without the CLI, and so the report lands beside
``report.md`` / ``report.html``.

    python bench/cost_report.py bench/out/static_v1_flash
    python bench/cost_report.py runs bench/out/*_flash --out bench/out/cost_all.md --recheck
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from codeverse.cost import audit_runs
from codeverse.cost.report import console as text_report
from codeverse.cost.report import markdown, runs_table


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cost report for one or more run trees.")
    ap.add_argument("paths", nargs="+", type=Path, help="run dirs or trees (bench/out/<battery>, runs/)")
    ap.add_argument("--out", type=Path, default=None,
                    help="markdown output (default: <first path>/cost_report.md)")
    ap.add_argument("--recheck", action="store_true",
                    help="re-price every call with today's table instead of what was billed")
    ap.add_argument("--title", default="", help="report title")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    audit = audit_runs(args.paths, recheck=args.recheck)
    if not audit.runs:
        print(f"no runs found under {', '.join(str(p) for p in args.paths)}", file=sys.stderr)
        return 2
    title = args.title or f"Cost report — {', '.join(p.name for p in args.paths)}"
    out = args.out or (args.paths[0] / "cost_report.md" if args.paths[0].is_dir() else Path("cost_report.md"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown(audit, title=title))
    if not args.quiet:
        print(text_report(audit))
        print()
        print(runs_table(audit, limit=15))
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
