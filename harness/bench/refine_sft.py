"""Refine rounds as SFT samples: the brief the round was given, answered with the code it produced.

``bench/refine_pairs.py`` exports the transition as data (issues, findings, instructions,
diff, delta).  This turns the rows that IMPROVED into the message shape the finetune
pipeline consumes (``{"id", "messages": [system, user, assistant]}`` — see
``toolkits/llamafactory/to_llamafactory.py``), so the corpus teaches the one thing the
harness produces and a one-shot dataset cannot: given a judged, gated object and a list of
what is wrong with it, write the corrected files.

    python bench/refine_sft.py bench/out --out refine_sft.jsonl --summary

The assistant turn is the FULL text of every file the round changed, in the same
``=== FILE: path ===`` envelope ``tracks/generation`` parses, because that is the answer
shape a trained model has to produce.  Rows are dropped, and counted, when the answer
would not fit ``--max-answer-chars`` or a side cannot be read back out of the run's repo.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.refine_pairs import Report, iter_rows  # noqa: E402
from codeverse.contracts.common import Language  # noqa: E402
from codeverse.flywheel._git import GitReadError, read_tree_at  # noqa: E402
from codeverse.tracks.prompting import language_system_prompt  # noqa: E402
from codeverse.workspace import Workspace  # noqa: E402

#: a sample whose answer is longer than this is dropped rather than truncated: half a
#: file teaches a model to write half a file
MAX_ANSWER_CHARS = 120_000
#: outcomes worth training on.  A regressed round is a real transition and stays in
#: refine_pairs.jsonl (it is evidence about the loop), but it is not an answer to copy.
DEFAULT_OUTCOMES = ("improved",)
SCHEMA_VERSION = 1


def envelope(files: dict[str, str]) -> str:
    """The ``=== FILE: path === … === END FILE ===`` blocks ``tracks.generation.parse_multifile``
    reads — the same envelope a single-shot answer must produce, terminator included."""
    return "\n".join(f"=== FILE: {p} ===\n{files[p].rstrip()}\n=== END FILE ===" for p in sorted(files))


def _bullets(items: list[dict], *keys: str, limit: int = 12) -> list[str]:
    out = []
    for it in items[:limit]:
        parts = [str(it.get(k, "")).strip() for k in keys]
        line = " — ".join(p for p in parts if p)
        if line:
            out.append(f"- {line}")
    return out


def user_turn(row: dict, before: dict[str, str]) -> str:
    """The brief: what was asked for, what was wrong, and the code as it stood."""
    lines = [f"Refine this {row['track'].replace('_', ' ')} written in {row['language']}.", "",
             "## Request", row["prompt"].strip(), ""]
    if row.get("gate_findings"):
        lines += ["## Gate errors (measured by the harness — fix these first)"]
        lines += _bullets(row["gate_findings"], "target", "message", "fix_hint")
        lines += [""]
    if row.get("issues"):
        lines += ["## What the judge saw"]
        lines += _bullets(row["issues"], "target", "severity", "detail")
        lines += [""]
    if row.get("instruction_lines"):
        lines += ["## Changes to make", *(f"{i}. {ln}" for i, ln in enumerate(row["instruction_lines"], 1)), ""]
    lines += ["## Current files", envelope(before), "",
              "Return every file you change in full, in the same `=== FILE: path ===` envelope."]
    return "\n".join(lines)


def sample(row: dict, *, max_answer_chars: int = MAX_ANSWER_CHARS) -> dict | None:
    """One SFT row, or ``None`` with the reason recorded on ``row['_drop']``."""
    changed = [f for f in row.get("changed_files") or [] if f.startswith("src/")]
    if not changed:
        row["_drop"] = "no_src_change"
        return None
    ws = Workspace(Path(row["run_dir"]))
    try:
        before_tree = read_tree_at(ws, row["before"]["commit"])
        after_tree = read_tree_at(ws, row["after"]["commit"])
    except (GitReadError, OSError) as e:
        row["_drop"] = f"git_read_failed: {type(e).__name__}"
        return None

    def _text(tree: dict[str, bytes], paths: list[str]) -> dict[str, str] | None:
        out = {}
        for p in paths:
            raw = tree.get(p)
            if raw is None:
                return None
            try:
                out[p] = raw.decode("utf-8")
            except UnicodeDecodeError:
                return None
        return out

    after = _text(after_tree, changed)
    if after is None:
        row["_drop"] = "answer_unreadable"
        return None
    # the brief carries the files the round STARTED from: whatever it changed, plus the
    # rest of src/ it had to stay consistent with
    before = _text(before_tree, sorted(p for p in before_tree if p.startswith("src/"))) or {}
    answer = envelope(after)
    if len(answer) > max_answer_chars:
        row["_drop"] = "answer_too_long"
        return None
    try:
        system = language_system_prompt(Language(row["language"]), tools=False)
    except ValueError:
        row["_drop"] = "unknown_language"
        return None
    return {
        "id": f"{row['battery']}/{row['run']}/r{row['round']:02d}",
        "schema_version": SCHEMA_VERSION,
        "track": row["track"], "language": row["language"], "prompt_id": row["prompt_id"],
        "score_before": row["score_before"], "score_after": row["score_after"],
        "score_delta": row["score_delta"], "changed_files": changed,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user_turn(row, before)},
                     {"role": "assistant", "content": answer}],
    }


def build(root: Path, *, outcomes: tuple[str, ...] = DEFAULT_OUTCOMES,
          max_answer_chars: int = MAX_ANSWER_CHARS) -> tuple[list[dict], Report, Counter]:
    report = Report()
    drops: Counter = Counter()
    out: list[dict] = []
    for row in iter_rows(root, report):
        if row["outcome"] not in outcomes:
            drops[f"outcome:{row['outcome']}"] += 1
            continue
        s = sample(row, max_answer_chars=max_answer_chars)
        if s is None:
            drops[row.get("_drop", "unknown")] += 1
            continue
        out.append(s)
    return out, report, drops


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("root", type=Path, help="a corpus of batteries, one battery, a runs dir or one run")
    ap.add_argument("--out", type=Path, default=None, help="write the samples here (JSONL)")
    ap.add_argument("--outcomes", default=",".join(DEFAULT_OUTCOMES),
                    help=f"comma list of refine_pairs outcomes to keep (default {DEFAULT_OUTCOMES[0]})")
    ap.add_argument("--max-answer-chars", type=int, default=MAX_ANSWER_CHARS)
    ap.add_argument("--summary", action="store_true", help="print the counts")
    ns = ap.parse_args(argv)
    outcomes = tuple(x for x in ns.outcomes.split(",") if x)
    rows, report, drops = build(ns.root, outcomes=outcomes, max_answer_chars=ns.max_answer_chars)
    if ns.out:
        ns.out.parent.mkdir(parents=True, exist_ok=True)
        ns.out.write_text("".join(json.dumps(r) + "\n" for r in rows))
        print(f"wrote {ns.out}")
    print(f"{len(rows)} sample(s) from {report.runs} run(s)")
    if ns.summary:
        print(f"rounds seen: {sum(report.kinds.values())} (" +
              ", ".join(f"{k} {v}" for k, v in sorted(report.kinds.items())) + ")")
        if report.drops:
            print("dropped by the scan: " + ", ".join(f"{k} {v}" for k, v in sorted(report.drops.items())))
        if drops:
            print("not exported: " + ", ".join(f"{k} {v}" for k, v in sorted(drops.items())))
        if rows:
            ans = [len(r["messages"][2]["content"]) for r in rows]
            usr = [len(r["messages"][1]["content"]) for r in rows]
            print(f"answer chars: median {int(median(ans))}, max {max(ans)}; "
                  f"brief chars: median {int(median(usr))}, max {max(usr)}")
            for field in ("track", "language"):
                print(f"by {field}: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(r[field] for r in rows).items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
