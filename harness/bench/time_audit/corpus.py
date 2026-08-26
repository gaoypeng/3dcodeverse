"""Read-only corpus discovery + loaders for the time audit (no side effects)."""
from __future__ import annotations

import glob
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OUT = Path("/home/yipeng/3dcodeverse/harness/bench/out")
STORM_DAY = "storm(08-26)"
BASELINE = "base(08-23/25)"

# corpus name -> (glob under bench/out, corpus bucket)
CORPORA: dict[str, tuple[str, str]] = {
    "fancy_v1": ("fancy_v1/*/arms/*/cells/*/harness_api-agent_gemini_gemini-3.7-flash/run", STORM_DAY),
    "h2h_brilliana_v1": ("h2h_brilliana_v1/*/runs/*", STORM_DAY),
    "h2h_scene_v1": ("h2h_scene_v1/runs/*", STORM_DAY),
    "refs_v1_graphics": ("refs_v1_graphics/runs/*", STORM_DAY),
    "teaser": ("teaser/runs/*", BASELINE),
    "static_v2_flash": ("static_v2_flash/runs/*", BASELINE),
    "scenes_v1_flash": ("scenes_v1_flash/runs/*", BASELINE),
}


@dataclass
class RunRef:
    corpus: str
    bucket: str
    run: Path
    track: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def cell_dir(self) -> Path | None:
        return self.run.parent if (self.run.parent / "worker.log").is_file() else None

    @property
    def name(self) -> str:
        return self.run.parent.parent.name if self.cell_dir else self.run.name


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def track_of(events: list[dict[str, Any]]) -> str:
    start = next((e for e in events if e.get("event") == "run.start"), None)
    if start is None:
        return "?"
    tr, lang = start.get("track", "?"), start.get("language", "?")
    if tr == "static_object":
        return f"static/{lang}"
    if tr == "articulated_object":
        return "articulated"
    return tr


def discover(corpora: dict[str, tuple[str, str]] = CORPORA) -> list[RunRef]:
    refs: list[RunRef] = []
    for corpus, (pat, bucket) in corpora.items():
        for d in sorted(glob.glob(str(OUT / pat))):
            run = Path(d)
            ev = read_jsonl(run / "events.jsonl")
            if not ev:
                continue
            refs.append(RunRef(corpus=corpus, bucket=bucket, run=run, track=track_of(ev), events=ev))
    return refs


def cost_rows(ref: RunRef) -> list[dict[str, Any]]:
    return read_jsonl(ref.run / "telemetry" / "cost.jsonl")


def trajectories(ref: RunRef) -> list[tuple[str, dict[str, Any], list[dict[str, Any]]]]:
    """(session label, result.json, transcript rows) for every trajectory dir."""
    out: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
    tdir = ref.run / "trajectories"
    if not tdir.is_dir():
        return out
    for sd in sorted(p for p in tdir.iterdir() if p.is_dir()):
        res: dict[str, Any] = {}
        rp = sd / "result.json"
        if rp.is_file():
            try:
                res = json.loads(rp.read_text())
            except json.JSONDecodeError:
                res = {}
        out.append((sd.name, res, read_jsonl(sd / "transcript.jsonl")))
    return out


def rounds(ref: RunRef) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in sorted((ref.run / "rounds").glob("r*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def med_p90(xs: list[float]) -> tuple[float, float, int]:
    """(median, p90, n); p90 by nearest-rank."""
    if not xs:
        return (float("nan"), float("nan"), 0)
    s = sorted(xs)
    p90 = s[min(len(s) - 1, int(round(0.9 * (len(s) - 1))))]
    return (statistics.median(s), p90, len(s))


def fmt(x: float, nd: int = 0) -> str:
    return "-" if x != x else f"{x:.{nd}f}"
