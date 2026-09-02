"""Refine rounds as training pairs: what the round was TOLD, what it CHANGED, what it SCORED.

``codeverse/flywheel/pairs.py`` exports whole code trees per round and never looks at
what asked for the change, so a refine round arrives as two 30 KB files with no cause.
This exports the transition instead — one row per refine round::

    (previous round's judge issues + improvement plan + gate ERROR findings
     + the instruction lines actually sent)   ->   the src/ diff   ->   score delta

Both sides come from the round's OWN recorded commit, never from ``HEAD``: only 54 of the
239 recorded runs have HEAD at their last round, 152 end on a "restore best round rNN"
commit, and looking a commit up by message is ambiguous (``r01 refine`` also matches
``r01 refine: generated``).

    python bench/refine_pairs.py bench/out --out refine_pairs.jsonl --summary
    python bench/refine_pairs.py bench/out/aa_articulated --summary

``root`` is a directory OF batteries (``bench/out``), one battery, a runs directory or a
single run — every layout the flywheel scanner knows.  Runs reachable twice through the
symlinked cells of a battery are exported once (identity is the resolved path), and every
transition that cannot be exported is counted under a named reason: the scan report is
printed whether or not rows were written.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import statistics
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codeverse.contracts.run import RoundRecord, RunId, RunRecord  # noqa: E402
from codeverse.flywheel import _git  # noqa: E402
from codeverse.flywheel.quality import prompt_hash  # noqa: E402
from codeverse.flywheel.record import (  # noqa: E402
    BATTERY_MARKERS,
    effective_judgment,
    is_run_dir,
    iter_runs,
)
from codeverse.workspace import Workspace  # noqa: E402

SCHEMA_VERSION = 1

#: round kinds that are a refine transition — a round handed instructions derived from
#: the previous round's verdict, and the only three the recorded corpus holds.
#: ``baseline`` has no predecessor, and ``texture`` / ``asset:*`` rounds are driven by
#: the texture pass rather than by a judgment.
REFINE_KINDS: tuple[str, ...] = ("refine", "rewrite", "detail")

#: cap on the inlined diff.  The corpus's largest transition diff is ~62 KB, so nothing
#: recorded today is truncated; the cap only stops a pathological row from eating a
#: dataset, and every row records it next to the pre-cap size.
MAX_DIFF_BYTES = 200_000

#: |Δscore| below which a round counts as ``unchanged``.  Same 0.05 the flywheel's
#: preference pairs already use as "a real difference" (``flywheel/pairs.build_pairs``).
OUTCOME_THRESHOLD = 0.05

DROP_REASONS: tuple[str, ...] = (
    "empty_battery",
    "unreadable_record",
    "duplicate_run",
    "missing_predecessor",
    "missing_commit",
    "missing_prev_judgment",
    "no_instructions",
    "git_read_failed",
    "empty_diff",
)


# --------------------------------------------------------------------------- instructions
_FILES_RE = re.compile(r"\s*\(files:\s*(?P<files>[^)]*)\)\s*$")
_HEAD_RE = re.compile(r"^\[(?P<source>[^/\]]+)/(?P<kind>[^\]]+)\]\s*(?P<rest>.*)$", re.S)
_TARGET_RE = re.compile(r"[A-Za-z0-9_.:+-]{1,64}")
_BULLET_SPLIT_RE = re.compile(r";\s+(?=\([a-z]\)\s)")
# the optional instance override is ``[Leg_1]``; the ``/`` keeps it from swallowing the
# ``[source/kind]`` head that follows it
_BULLET_RE = re.compile(r"^\((?P<letter>[a-z])\)\s*(?:\[(?P<target>[^\]/]+)\]\s*)?(?P<rest>.*)$", re.S)


def _task(source: str, kind: str, target: str, instruction: str, files: list[str]) -> dict[str, Any]:
    return {"source": source, "kind": kind, "target": target.strip(),
            "instruction": instruction.strip(), "files": files}


def parse_instruction(line: str) -> list[dict[str, Any]]:
    """The task(s) encoded in one recorded instruction line.

    ``orchestrator.compact_instructions`` emits three shapes and the corpus holds all
    three: a single ``RefineTask.line()`` (``[source/kind] target: text (files: a, b)``),
    the grouped form (``target: (a) [source/kind] text; (b) …``) and — detail rounds — a
    bare sentence.  A line that matches none of them still yields one task carrying its
    whole text, so no instruction is silently lost; the target prefix is only honoured
    when it is a single bare token, or a sentence's own colon would be read as one.
    """
    raw = line.strip()
    files: list[str] = []
    if (m := _FILES_RE.search(raw)) is not None:
        files = [f.strip() for f in m.group("files").split(",") if f.strip()]
        raw = raw[: m.start()].rstrip()
    if (head := _HEAD_RE.match(raw)) is not None:
        target, sep, text = head.group("rest").partition(": ")
        if not sep:
            target, text = "overall", head.group("rest")
        return [_task(head.group("source"), head.group("kind"), target, text, files)]
    target, sep, rest = raw.partition(": ")
    if not sep or _TARGET_RE.fullmatch(target) is None:
        return [_task("", "", "", raw, files)]
    bullets = _BULLET_SPLIT_RE.split(rest)
    if _BULLET_RE.match(bullets[0]) is None:
        return [_task("", "", target, rest, files)]
    out: list[dict[str, Any]] = []
    for b in bullets:
        bm = _BULLET_RE.match(b)
        if bm is None:
            out.append(_task("", "", target, b, files))
            continue
        who, inner = bm.group("target") or target, bm.group("rest")
        if (hm := _HEAD_RE.match(inner)) is not None:
            out.append(_task(hm.group("source"), hm.group("kind"), who, hm.group("rest"), files))
        else:
            out.append(_task("", "", who, inner, files))
    return out


# --------------------------------------------------------------------------- rows
def outcome_label(delta: float | None, *, threshold: float = OUTCOME_THRESHOLD) -> str:
    """``improved`` / ``regressed`` / ``unchanged`` by ``threshold``; ``unscored`` when
    either side has no usable verdict (a degraded judge outage is *not* a 0.0)."""
    if delta is None:
        return "unscored"
    if delta > threshold:
        return "improved"
    if delta < -threshold:
        return "regressed"
    return "unchanged"


def _gate_errors(rnd: RoundRecord) -> list[dict[str, Any]]:
    """The ERROR findings of a round's gates — the half of the refine brief the judge
    did not write.  ``fix_hint`` is what ``build_refine_instructions`` copies verbatim."""
    return [{"gate": g.gate, "target": f.target or "", "message": f.message,
             "fix_hint": f.fix_hint, "data": f.data}
            for g in rnd.gates for f in g.errors]


def _side(rnd: RoundRecord, files: list[str]) -> dict[str, Any]:
    j = effective_judgment(rnd)
    return {
        "round": rnd.index,
        "kind": rnd.kind,
        "commit": rnd.commit,
        "score": j.overall if j else None,
        "passed": j.passed if j else None,
        "build_ok": None if rnd.build is None else rnd.build.ok,
        "gate_errors": sum(len(g.errors) for g in rnd.gates),
        "files": files,
    }


def _diff_lines(text: str) -> tuple[int, int]:
    """(added, removed) body lines of a unified diff — the ``+++``/``---`` headers are
    file markers, not content, and would otherwise count once per changed file."""
    added = removed = 0
    for ln in text.splitlines():
        if ln.startswith("+") and not ln.startswith("+++"):
            added += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            removed += 1
    return added, removed


def transition_row(
    ws: Workspace, rec: RunRecord, run_id: RunId, prev: RoundRecord, cur: RoundRecord,
    *, threshold: float = OUTCOME_THRESHOLD, max_diff_bytes: int = MAX_DIFF_BYTES,
    files_cache: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """One training row for the transition ``prev`` → ``cur``.  Raises
    :class:`~codeverse.flywheel._git.GitReadError` when either commit is unreadable."""
    cache = files_cache if files_cache is not None else {}
    for c in (prev.commit, cur.commit):
        if c not in cache:
            cache[c] = _git.list_files_at(ws, c)
    diff, diff_bytes, truncated = _git.diff_between(ws, prev.commit, cur.commit, max_bytes=max_diff_bytes)
    added, removed = _diff_lines(diff)
    pj, cj = effective_judgment(prev), effective_judgment(cur)
    delta = None if pj is None or cj is None else round(cj.overall - pj.overall, 4)
    return {
        "schema_version": SCHEMA_VERSION,
        "battery": run_id.battery,
        "run": run_id.slug,
        "run_dir": str(ws.root),
        "cell": run_id.cell,
        "arm": run_id.arm,
        "prompt_id": rec.spec.id,
        "prompt": rec.spec.prompt,
        "prompt_hash": prompt_hash(rec.spec.prompt),
        "track": rec.spec.track.value,
        "language": rec.spec.language.value,
        "generator": rec.spec.backends.generator,
        "agent_backend": cur.agent_backend,
        "round": cur.index,
        "round_kind": cur.kind,
        "issues": [i.model_dump(mode="json") for i in pj.issues] if pj else [],
        "improvement_plan": [i.model_dump(mode="json") for i in pj.improvement_plan] if pj else [],
        "gate_findings": _gate_errors(prev),
        "instructions": list(cur.instructions),
        "instruction_tasks": [t for line in cur.instructions for t in parse_instruction(line)],
        "diff": diff,
        "diff_bytes": diff_bytes,
        "diff_added": added,
        "diff_removed": removed,
        "diff_truncated": truncated,
        "diff_max_bytes": max_diff_bytes,
        "changed_files": _git.changed_files_between(ws, prev.commit, cur.commit),
        "before": _side(prev, cache[prev.commit]),
        "after": _side(cur, cache[cur.commit]),
        "score_before": pj.overall if pj else None,
        "score_after": cj.overall if cj else None,
        "score_delta": delta,
        "outcome": outcome_label(delta, threshold=threshold),
        "outcome_threshold": threshold,
    }


# --------------------------------------------------------------------------- scan
@dataclass
class Report:
    """What one export saw: totals, named drops, and a digest per written row."""

    roots: list[str] = field(default_factory=list)
    runs: int = 0
    kinds: Counter[str] = field(default_factory=Counter)
    drops: Counter[str] = field(default_factory=Counter)
    stats: list[dict[str, Any]] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return len(self.stats)

    @property
    def transitions(self) -> int:
        """Refine-kind rounds seen — rows plus every per-transition drop."""
        return sum(self.kinds[k] for k in REFINE_KINDS)


def scan_roots(root: Path) -> list[Path]:
    """The scan roots under ``root`` — one per battery, so ``RunId.battery`` stays a
    battery name.  ``bench/out`` holds runs 5 levels down, and scanning it whole would
    label every run ``out`` and collapse the per-battery summary."""
    if is_run_dir(root) or any((root / m).is_dir() for m in BATTERY_MARKERS):
        return [root]
    kids = sorted(p for p in root.iterdir() if p.is_dir())
    if any(is_run_dir(k) for k in kids):
        return [root]
    return [k for k in kids if is_run_dir(k) or any((k / m).is_dir() for m in BATTERY_MARKERS)] or [root]


def iter_rows(
    root: Path, report: Report, *, threshold: float = OUTCOME_THRESHOLD,
    max_diff_bytes: int = MAX_DIFF_BYTES,
) -> Iterator[dict[str, Any]]:
    """Yield one row per exportable refine transition under ``root``, filling ``report``.

    Every run and every round is accounted for: a run is exported once per resolved path
    (the symlinked cells of the recorded batteries make 54 runs reachable twice) and a
    transition that cannot be exported increments a named reason instead of vanishing.
    """
    seen: set[Path] = set()
    for scan in scan_roots(root):
        report.roots.append(str(scan))
        found = iter_runs(scan, on_error=lambda _p, _e: report.drops.update(["unreadable_record"]))
        try:  # iter_runs is a generator: it raises for an empty battery on the first step
            found = itertools.chain([next(found)], found)
        except FileNotFoundError:
            report.drops["empty_battery"] += 1
            continue
        except StopIteration:
            continue
        for ws, rec, run_id in found:
            resolved = ws.root.resolve()
            if resolved in seen:
                report.drops["duplicate_run"] += 1
                continue
            seen.add(resolved)
            report.runs += 1
            yield from _run_rows(ws, rec, run_id, report, threshold=threshold, max_diff_bytes=max_diff_bytes)


def _run_rows(
    ws: Workspace, rec: RunRecord, run_id: RunId, report: Report, *, threshold: float, max_diff_bytes: int
) -> Iterator[dict[str, Any]]:
    by_index = {r.index: r for r in rec.rounds}
    files_cache: dict[str, list[str]] = {}
    for cur in rec.rounds:
        report.kinds[cur.kind] += 1
        if cur.kind not in REFINE_KINDS:
            continue
        prev = by_index.get(cur.index - 1)
        if prev is None:
            report.drops["missing_predecessor"] += 1
            continue
        if not prev.commit or not cur.commit:
            report.drops["missing_commit"] += 1
            continue
        if effective_judgment(prev) is None:
            report.drops["missing_prev_judgment"] += 1
            continue
        if not cur.instructions:
            report.drops["no_instructions"] += 1
            continue
        try:
            row = transition_row(ws, rec, run_id, prev, cur, threshold=threshold,
                                 max_diff_bytes=max_diff_bytes, files_cache=files_cache)
        except _git.GitReadError:
            report.drops["git_read_failed"] += 1
            continue
        if not row["diff"].strip():
            report.drops["empty_diff"] += 1
            continue
        report.stats.append({"battery": row["battery"], "track": row["track"], "outcome": row["outcome"],
                             "diff_bytes": row["diff_bytes"], "diff_added": row["diff_added"],
                             "diff_removed": row["diff_removed"]})
        yield row


def export(
    root: Path, out: Path | None, *, threshold: float = OUTCOME_THRESHOLD,
    max_diff_bytes: int = MAX_DIFF_BYTES,
) -> Report:
    """Write the rows under ``root`` as JSONL to ``out`` (``None`` = count only)."""
    report = Report()
    rows = iter_rows(root, report, threshold=threshold, max_diff_bytes=max_diff_bytes)
    if out is None:
        for _ in rows:
            pass
        return report
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(out)
    return report


# --------------------------------------------------------------------------- report
def _table(title: str, counts: Counter[str], *, by_count: bool = True) -> list[str]:
    if not counts:
        return []
    items = counts.most_common() if by_count else sorted(counts.items())
    width = max(len(k) for k, _ in items)
    return [f"{title}:"] + [f"  {k:<{width}}  {n:>5}" for k, n in items]


def scan_report(report: Report) -> str:
    """Totals + every drop reason.  Printed always: a truncated export must never look
    like a small corpus."""
    by_kind = ", ".join(f"{n} {k}" for k, n in sorted(report.kinds.items())) or "none"
    lines = [f"{report.rows} row(s) from {report.runs} run(s) in {len(report.roots)} root(s)",
             f"rounds seen: {sum(report.kinds.values())} "
             f"({report.transitions} refine-kind; {by_kind})"]
    lines += _table("dropped", report.drops, by_count=False) or ["dropped: none"]
    return "\n".join(lines)


def summary_report(report: Report, *, threshold: float = OUTCOME_THRESHOLD) -> str:
    """Counts per battery / track / outcome, and how big the diffs are."""
    if not report.stats:
        return "no rows to summarise"
    lines = _table("rows per battery", Counter(s["battery"] for s in report.stats))
    lines += [""] + _table("rows per track", Counter(s["track"] for s in report.stats))
    lines += [""] + _table(f"rows per outcome (|delta| > {threshold})",
                           Counter(s["outcome"] for s in report.stats))
    sizes = sorted(s["diff_bytes"] for s in report.stats)
    churn = sorted(s["diff_added"] + s["diff_removed"] for s in report.stats)
    lines += ["", f"diff size: median {int(statistics.median(sizes))} bytes "
                  f"(min {sizes[0]}, max {sizes[-1]}); "
                  f"median {int(statistics.median(churn))} changed lines"]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="directory of batteries, a battery, a runs dir or one run")
    ap.add_argument("--out", type=Path, help="write the rows here as JSONL")
    ap.add_argument("--summary", action="store_true", help="also print per-battery/track/outcome counts")
    ap.add_argument("--threshold", type=float, default=OUTCOME_THRESHOLD,
                    help=f"|delta| above which a round improved/regressed (default {OUTCOME_THRESHOLD})")
    ap.add_argument("--max-diff-bytes", type=int, default=MAX_DIFF_BYTES,
                    help=f"cap on the inlined diff, recorded on every row (default {MAX_DIFF_BYTES})")
    args = ap.parse_args(argv)

    if not args.root.is_dir():
        ap.error(f"{args.root} is not a directory")
    if args.out is None and not args.summary:
        ap.error("nothing to do: pass --out to write rows, --summary to print counts, or both")

    report = export(args.root, args.out, threshold=args.threshold, max_diff_bytes=args.max_diff_bytes)
    if args.out is not None:
        print(f"wrote {args.out}")
    print(scan_report(report))
    if args.summary:
        print()
        print(summary_report(report, threshold=args.threshold))
    return 0 if report.rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
