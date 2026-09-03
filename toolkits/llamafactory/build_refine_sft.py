"""Harness refine transitions -> messages-jsonl for the SFT pipeline.

The harness exports what only it knows (`3dcv flywheel refine --with-code`): for every
refine round, the previous round's judge issues and gate errors, the tasks compiled for
the round, the src/ diff between the two recorded commits, the score delta, and the files
on both sides.  This turns the rounds that IMPROVED into the three-message shape
`to_llamafactory.py` consumes, which is the one supervision signal a one-shot corpus
cannot carry: given a judged, gated object and a list of what is wrong with it, write the
corrected files.

    3dcv flywheel refine bench/out refine.jsonl --with-code      # in the harness
    python build_refine_sft.py refine.jsonl --out sft/refine.jsonl --summary
    python to_llamafactory.py --data_dir sft --name 3dcv_refine

The answer is every file the round changed, in the `=== FILE: path ===` envelope the
harness's own parser reads, so a trained model's output can be fed straight back into it.
"""

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

#: an answer longer than this is dropped rather than truncated: half a file teaches a
#: model to write half a file
MAX_ANSWER_CHARS = 120_000
#: outcomes worth training on.  A regressed round is evidence about the loop, not an
#: answer to copy; it stays in the harness export.
KEEP_OUTCOMES = ("improved",)
SYSTEM = ("You are an expert {language} author. You are given a 3D artifact that already builds, the "
          "problems a judge and a set of deterministic geometric gates found in it, and the code as it "
          "stands. Return corrected files.")


def envelope(files):
    """`=== FILE: path === ... === END FILE ===`, the harness's multi-file answer format."""
    return "\n".join(f"=== FILE: {p} ===\n{files[p].rstrip()}\n=== END FILE ===" for p in sorted(files))


def bullets(items, keys, limit=12):
    out = []
    for it in items[:limit]:
        line = " — ".join(str(it.get(k, "")).strip() for k in keys if str(it.get(k, "")).strip())
        if line:
            out.append(f"- {line}")
    return out


def user_turn(row):
    """The brief: the request, what was wrong, what to change, and the code it started from."""
    lines = [f"Refine this {row['track'].replace('_', ' ')} written in {row['language']}.", "",
             "## Request", row["prompt"].strip(), ""]
    if row.get("gate_errors"):
        lines += ["## Gate errors (measured by the harness — fix these first)",
                  *bullets(row["gate_errors"], ("target", "message", "fix_hint")), ""]
    if row.get("issues"):
        lines += ["## What the judge saw", *bullets(row["issues"], ("target", "severity", "detail")), ""]
    if row.get("instructions"):
        lines += ["## Changes to make", *(f"{i}. {ln}" for i, ln in enumerate(row["instructions"], 1)), ""]
    lines += ["## Current files", envelope(row["before_files"]), "",
              "Return every file you change in full, in the same `=== FILE: path ===` envelope."]
    return "\n".join(lines)


def sample(row, max_answer_chars=MAX_ANSWER_CHARS):
    """One SFT row, or `(None, reason)`."""
    after = row.get("after_files") or {}
    if not after:
        return None, "no_code"          # exported without --with-code, or nothing readable changed
    if not row.get("before_files"):
        return None, "no_before_code"
    answer = envelope(after)
    if len(answer) > max_answer_chars:
        return None, "answer_too_long"
    return {"id": f"{row['battery']}/{row['run']}/r{row['round']:02d}",
            "track": row["track"], "language": row["language"], "prompt_id": row["prompt_id"],
            "score_before": row["score_before"], "score_after": row["score_after"],
            "score_delta": row["score_delta"], "changed_files": row["changed_files"],
            "messages": [{"role": "system", "content": SYSTEM.format(language=row["language"])},
                         {"role": "user", "content": user_turn(row)},
                         {"role": "assistant", "content": answer}]}, ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("transitions", type=Path, help="JSONL from `3dcv flywheel refine --with-code`")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--outcomes", default=",".join(KEEP_OUTCOMES))
    ap.add_argument("--max-answer-chars", type=int, default=MAX_ANSWER_CHARS)
    ap.add_argument("--summary", action="store_true")
    ns = ap.parse_args(argv)

    keep = {x for x in ns.outcomes.split(",") if x}
    rows, drops = [], Counter()
    for line in ns.transitions.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["outcome"] not in keep:
            drops[f"outcome:{row['outcome']}"] += 1
            continue
        s, reason = sample(row, ns.max_answer_chars)
        if s is None:
            drops[reason] += 1
        else:
            rows.append(s)
    if ns.out:
        ns.out.parent.mkdir(parents=True, exist_ok=True)
        tmp = ns.out.with_suffix(ns.out.suffix + ".part")
        tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
        tmp.replace(ns.out)
        print(f"wrote {ns.out}")
    print(f"{len(rows)} sample(s) from {ns.transitions}")
    if ns.summary:
        if drops:
            print("not exported: " + ", ".join(f"{k} {v}" for k, v in sorted(drops.items())))
        if rows:
            ans = [len(r["messages"][2]["content"]) for r in rows]
            brief = [len(r["messages"][1]["content"]) for r in rows]
            print(f"answer chars: median {int(statistics.median(ans))}, max {max(ans)}; "
                  f"brief chars: median {int(statistics.median(brief))}, max {max(brief)}")
            for field in ("track", "language"):
                print(f"by {field}: " + ", ".join(f"{k} {v}" for k, v in
                                                  sorted(Counter(r[field] for r in rows).items())))
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
