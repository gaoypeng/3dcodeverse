"""Task 4: does any persisted telemetry row say WHICH Gemini key served a call?
Scans every field name in cost.jsonl / transcript.jsonl / result.json / rounds /
record.json for something key-like, and greps the raw text for the '…xxxx' redaction
that gemini.py puts into ChatResponse.raw."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from corpus import discover, read_jsonl  # noqa: E402

KEYLIKE = re.compile(r"key|api_key|credential", re.I)
REDACTED = re.compile(r'"key":\s*"…[^"]{0,8}"')


def walk(obj: object, prefix: str, found: Counter[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if KEYLIKE.search(str(k)):
                found[f"{prefix}.{k}"] += 1
            walk(v, f"{prefix}.{k}", found)
    elif isinstance(obj, list):
        for v in obj[:50]:
            walk(v, prefix + "[]", found)


def main() -> None:
    found: Counter[str] = Counter()
    redacted = 0
    files = 0
    for ref in discover():
        for p in [ref.run / "telemetry" / "cost.jsonl", ref.run / "record.json", *(ref.run / "rounds").glob("r*.json"),
                  *(ref.run / "trajectories").glob("*/result.json"), *(ref.run / "trajectories").glob("*/transcript.jsonl")]:
            if not p.is_file():
                continue
            files += 1
            text = p.read_text(errors="replace")
            redacted += len(REDACTED.findall(text))
            if p.suffix == ".jsonl":
                for row in read_jsonl(p):
                    walk(row, p.name, found)
            else:
                try:
                    walk(json.loads(text), p.name, found)
                except json.JSONDecodeError:
                    pass
    print(f"scanned {files} files; '\"key\": \"…xxxx\"' occurrences: {redacted}")
    print("key-like field names found (name -> count):")
    for k, v in found.most_common(20):
        print(f"  {v:7d} {k}")
    if not any("api" in k.lower() or k.endswith(".key") for k in found):
        print("=> no per-call key identity is persisted anywhere in the run telemetry.")


if __name__ == "__main__":
    main()
