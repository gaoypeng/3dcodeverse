"""Per-skill deterministic readout: what each bundle claims to move, measured.

``bench/ab_gate_rates.py`` prints the gate numbers of an A/B run; it does not know which
*skill* owns which number.  This does.  ``codeverse/addons/skill_targets.py`` holds one
falsifiable claim per bundle; this file computes it over a recorded battery or an A/B
directory, so the same command answers both "what is the baseline" and "did the variant
move it".

Usage::

    python bench/skill_targets.py bench/out/static_v2_flash          # one battery
    python bench/skill_targets.py bench/out/ab_skills                # control vs variant
    python bench/skill_targets.py bench/out --per-run --skill cv3d-part-contact
    python bench/skill_targets.py bench/out --json

Nothing here reads the judge.  Every number comes from a gate report, a BuildResult, the
exported GLB or the sampled frames — the readouts that do not carry the planner's variance
(``docs/EVAL.md`` §8.1).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

for _p in (Path(__file__).resolve().parents[2] / "harness", Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(_p))  # this tree's codeverse (harness/) + the `bench` package (eval/)

from codeverse.addons.skill_targets import (  # noqa: E402
    SRC_ARTIFACT,
    SRC_BUILD,
    SRC_FRAMES,
    SRC_GATE,
    SRC_GLB,
    TARGETS,
    Target,
)
from codeverse.skills.registry import finding_kind  # noqa: E402

CACHE_NAME = ".skill_targets_cache.json"
_FRAME_T = re.compile(r"_t(\d+(?:\.\d+)?)\.png$")


# --------------------------------------------------------------------------- runs
@dataclass(frozen=True)
class Run:
    """One recorded run: the record, and the directory its artefacts sit in."""

    key: str
    label: str
    path: Path
    record: dict[str, Any]

    @property
    def dir(self) -> Path:
        return self.path.parent

    @property
    def language(self) -> str:
        return str((self.record.get("spec") or {}).get("language") or "")

    @property
    def rounds(self) -> list[dict]:
        return list(self.record.get("rounds") or [])

    def gated_round(self, which: str = "last") -> dict | None:
        gated = [r for r in self.rounds if r.get("gates")]
        if not gated:
            return None
        return gated[0] if which == "first" else gated[-1]

    def render_dir(self, rd: dict) -> Path | None:
        """The round's render directory, resolved inside THIS tree.

        ``record.json`` stores absolute paths from the machine that produced it, so a
        moved or copied corpus would read nothing; the canonical layout is authoritative
        and the stored path is only the fallback for a layout we have not seen.
        """
        cand = self.dir / "artifacts" / "renders" / f"r{int(rd.get('index', 0)):02d}"
        if cand.is_dir():
            return cand
        for v in (rd.get("renders") or {}).get("views") or []:
            p = Path(str(v.get("path") or ""))
            if p.is_file():
                return p.parent
        return None


def load_runs(root: Path) -> list[Run]:
    out: list[Run] = []
    for rec in sorted(root.rglob("record.json")):
        try:
            data = json.loads(rec.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append(Run(key=_key_of(rec, root), label=str(rec.parent.relative_to(root)),
                       path=rec, record=data))
    return out


def _key_of(rec: Path, root: Path) -> str:
    """A stable prompt id: the cell/run directory name, not the whole path."""
    parts = rec.relative_to(root).parts
    for anchor in ("cells", "runs"):
        if anchor in parts:
            return parts[parts.index(anchor) + 1]
    return parts[0] if parts else rec.parent.name


def ab_arms(root: Path) -> dict[str, Path] | None:
    """``{arm: dir}`` when *root* is an A/B directory, else None."""
    arms = root / "arms"
    if (arms / "control").is_dir() and (arms / "variant").is_dir():
        return {"control": arms / "control", "variant": arms / "variant"}
    return None


# --------------------------------------------------------------------------- metrics
def count_kinds(rd: dict, kinds: tuple[str, ...], severities: tuple[str, ...]) -> int:
    n = 0
    for g in rd.get("gates") or []:
        for f in g.get("findings") or []:
            sev = str(f.get("severity", "warn")).lower()
            if severities and sev not in severities:
                continue
            if finding_kind(str(g.get("gate", "")), str(f.get("message", "")), sev) in kinds:
                n += 1
    return n


def _frames_of(d: Path) -> list[tuple[float, Path]]:
    out = []
    for p in sorted(d.glob("frame_t*.png")):
        m = _FRAME_T.search(p.name)
        if m:
            out.append((float(m.group(1)), p))
    return sorted(out)


def _feature_density(glb: Path) -> float | None:
    from codeverse.spatial.complexity import complexity_of_glb

    try:
        return float(complexity_of_glb(glb).feature_density)
    except Exception:
        return None


def _mean_edge_density(d: Path) -> float | None:
    from codeverse.spatial.frame_stats import sequence_stats

    frames = _frames_of(d)
    if not frames:
        return None
    try:
        return float(sequence_stats([(t, p) for t, p in frames]).mean_edge_density)
    except Exception:
        return None


def _min_authored_changed_frac(d: Path) -> float | None:
    from codeverse.spatial.frame_motion import motion_from_dir

    try:
        rows = [r for r in motion_from_dir(d) if r.authored]
    except Exception:
        return None
    return min((float(r.changed_frac) for r in rows), default=None)


def _shader_preflight_findings(run: Run) -> float | None:
    """``artifacts/shader_preflight.json`` — written beside the run, not into the record."""
    p = run.dir / "artifacts" / "shader_preflight.json"
    if not p.is_file():
        return None
    try:
        rep = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return float(sum(1 for f in rep.get("findings") or []
                     if str(f.get("severity", "")).lower() in ("warn", "error")))


def measure(target: Target, run: Run, *, which: str = "last",
            cache: dict[str, Any] | None = None) -> float | None:
    """The target's value for one run, or None when it does not apply / has no artefact."""
    if target.languages and run.language not in target.languages:
        return None
    if target.source == SRC_GATE:
        rd = run.gated_round(which)
        return None if rd is None else float(count_kinds(rd, target.kinds, target.severities))
    if target.source == SRC_BUILD:
        builds = [r.get("build") for r in run.rounds if isinstance(r.get("build"), dict)]
        if not builds:
            return None
        return sum(0.0 if b.get("ok") else 1.0 for b in builds) / len(builds)
    if target.source == SRC_ARTIFACT:
        return None if run.gated_round(which) is None else _shader_preflight_findings(run)

    key = f"{target.metric}:{run.path}:{which}"
    if cache is not None and key in cache:
        v = cache[key]
        return None if v is None else float(v)
    val = _measure_heavy(target, run, which)
    if cache is not None:
        cache[key] = val
    return val


def _measure_heavy(target: Target, run: Run, which: str) -> float | None:
    # every source shares one population: a run the gates actually reported on.  Without
    # this a GLB-derived metric would also count concurrency probes and e2e smoke runs
    # that no gate graded, and its n would not compare with the gate-derived rows.
    rd = run.gated_round(which)
    if rd is None:
        return None
    if target.source == SRC_GLB:
        glb = run.dir / "artifacts" / "object.glb"
        return _feature_density(glb) if glb.is_file() else None
    if target.source == SRC_FRAMES:
        d = run.render_dir(rd)
        if d is None:
            return None
        if target.metric == "mean_edge_density":
            return _mean_edge_density(d)
        if target.metric == "min_authored_changed_frac":
            return _min_authored_changed_frac(d)
    return None


# --------------------------------------------------------------------------- reporting
def _stats(vals: list[float]) -> dict[str, float | int]:
    return {"n": len(vals), "mean": statistics.fmean(vals), "median": statistics.median(vals),
            "min": min(vals), "max": max(vals), "zero": sum(1 for v in vals if v == 0)}


def battery_rows(runs: list[Run], targets: list[Target], *, which: str,
                 cache: dict | None, languages: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    rows = []
    if languages:
        runs = [r for r in runs if r.language in languages]
    for t in targets:
        vals, per_run = [], {}
        for r in runs:
            v = measure(t, r, which=which, cache=cache)
            if v is not None:
                vals.append(v)
                per_run[r.label] = v
        row: dict[str, Any] = {"skill": t.skill, "metric": t.metric, "direction": t.direction,
                               "unit": t.unit, "measurable": t.measurable, "per_run": per_run}
        row.update(_stats(vals) if vals else {"n": 0})
        rows.append(row)
    return rows


def _best(runs: list[Run]) -> Run:
    """One cell can hold several attempts (``...flash.attempt1``) after a provider outage.

    The graded one is the one to pair: an attempt that never reached a gate is the
    provider failing, not the arm, and ``_infra`` already excludes those cells.
    """
    return max(runs, key=lambda r: (len(r.rounds), str(r.path)))


def _by_key(root: Path, languages: tuple[str, ...]) -> dict[str, Run]:
    out: dict[str, list[Run]] = {}
    for r in load_runs(root):
        if not languages or r.language in languages:
            out.setdefault(r.key, []).append(r)
    return {k: _best(v) for k, v in out.items()}


def ab_rows(arms: dict[str, Path], targets: list[Target], *, which: str,
            cache: dict | None, languages: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    by_arm = {arm: _by_key(d, languages) for arm, d in arms.items()}
    rows = []
    for t in targets:
        pairs, dropped = [], []
        for key in sorted(set(by_arm["control"]) | set(by_arm["variant"])):
            c = v = None
            if key in by_arm["control"]:
                c = measure(t, by_arm["control"][key], which=which, cache=cache)
            if key in by_arm["variant"]:
                v = measure(t, by_arm["variant"][key], which=which, cache=cache)
            if c is not None and v is not None:
                pairs.append((key, c, v))
            elif c is not None or v is not None:
                dropped.append(key)
        row: dict[str, Any] = {"skill": t.skill, "metric": t.metric, "direction": t.direction,
                               "unit": t.unit, "measurable": t.measurable,
                               "n": len(pairs), "half_paired": dropped,
                               "pairs": [{"prompt": k, "control": c, "variant": v}
                                         for k, c, v in pairs]}
        if pairs:
            deltas = [v - c for _, c, v in pairs]
            better = sum(1 for d in deltas if (d < 0) == (t.direction == "down") and d != 0)
            worse = sum(1 for d in deltas if d != 0) - better
            control_mean = statistics.fmean(c for _, c, _ in pairs)
            row.update({"control_mean": control_mean,
                        "variant_mean": statistics.fmean(v for _, _, v in pairs),
                        "mean_delta": statistics.fmean(deltas),
                        "better": better, "worse": worse, "tied": len(deltas) - better - worse})
            row.update(confidence(deltas, control_mean))
        rows.append(row)
    return rows


def confidence(deltas: list[float], control_mean: float, effect: float = 0.25) -> dict[str, Any]:
    """Paired sd, the 2 SE band, and how many pairs a given effect would need.

    Same shape as the Confidence block ``bench/ab_plan.py`` prints for the judged score
    (docs/EVAL.md §8), because the question is the same one and the answer is not: a
    deterministic count has its own sd and it is NOT zero.  ``effect`` is the fraction of
    the control mean that counts as a real move.
    """
    if len(deltas) < 2:
        return {"sd": None, "se": None, "ci95": None, "n_to_resolve": None}
    sd = statistics.stdev(deltas)
    se = sd / len(deltas) ** 0.5
    want = abs(control_mean) * effect
    n = None if not want or not sd else max(2, int(round((2 * sd / want) ** 2)))
    return {"sd": sd, "se": se, "ci95": 2 * se, "n_to_resolve": n,
            "resolvable_effect": want}


def _fmt(v: Any, width: int = 8) -> str:
    if v is None:
        return "-".rjust(width)
    return (f"{v:.3f}" if isinstance(v, float) else str(v)).rjust(width)


def print_battery(rows: list[dict], per_run: bool) -> None:
    print(f"{'skill':<28} {'metric':<28} {'dir':<5} {'n':>4} {'mean':>8} {'median':>8} "
          f"{'min':>8} {'max':>8} {'=0':>4}")
    for r in rows:
        flag = "" if r["measurable"] else "  (measurable=false: guard only)"
        print(f"{r['skill']:<28} {r['metric']:<28} {r['direction']:<5} {r['n']:>4} "
              f"{_fmt(r.get('mean'))} {_fmt(r.get('median'))} {_fmt(r.get('min'))} "
              f"{_fmt(r.get('max'))} {_fmt(r.get('zero'), 4)}{flag}")
        if per_run:
            for k, v in sorted(r.get("per_run", {}).items()):
                print(f"      {k:<40} {v:.3f}")


def print_ab(rows: list[dict], per_run: bool) -> None:
    print(f"{'skill':<28} {'metric':<28} {'dir':<5} {'n':>4} {'control':>9} {'variant':>9} "
          f"{'delta':>9}  b/w/t  {'sd':>8} {'+/-2SE':>8}  n to resolve 25%")
    for r in rows:
        if not r["n"]:
            print(f"{r['skill']:<28} {r['metric']:<28} {r['direction']:<5}    0   (no paired run)")
            continue
        flag = "" if r["measurable"] else "   (guard only)"
        print(f"{r['skill']:<28} {r['metric']:<28} {r['direction']:<5} {r['n']:>4} "
              f"{_fmt(r['control_mean'], 9)} {_fmt(r['variant_mean'], 9)} {_fmt(r['mean_delta'], 9)}"
              f"  {r['better']}/{r['worse']}/{r['tied']}  {_fmt(r.get('sd'))} {_fmt(r.get('ci95'))}"
              f"  {r.get('n_to_resolve') if r.get('n_to_resolve') is not None else '-':>6}{flag}")
        if r["half_paired"]:
            print(f"{'':<28} one arm only, dropped: {', '.join(r['half_paired'])}")
        if per_run:
            for p in r["pairs"]:
                print(f"      {p['prompt']:<40} {p['control']:>8.3f} -> {p['variant']:>8.3f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path, help="a battery dir, an A/B dir (arms/control+variant), or bench/out")
    ap.add_argument("--skill", action="append", default=[], help="limit to this bundle (repeatable)")
    ap.add_argument("--round", choices=("first", "last"), default="last",
                    help="which gated round to read (default last, as ab_gate_rates does)")
    ap.add_argument("--language", action="append", default=[],
                    help="limit to runs in this language (repeatable)")
    ap.add_argument("--per-run", action="store_true", help="also print every run / pair")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--no-cache", action="store_true", help="do not read/write the GLB+frame cache")
    a = ap.parse_args(argv)

    if not a.out.is_dir():
        ap.error(f"{a.out} is not a directory")
    targets = [t for t in TARGETS if not a.skill or t.skill in a.skill]
    if not targets:
        ap.error(f"no such skill: {a.skill}")

    cache_path = a.out / CACHE_NAME
    cache: dict[str, Any] | None = None
    if not a.no_cache:
        try:
            cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            cache = {}

    langs = tuple(a.language)
    arms = ab_arms(a.out)
    if arms:
        rows = ab_rows(arms, targets, which=a.round, cache=cache, languages=langs)
    else:
        rows = battery_rows(load_runs(a.out), targets, which=a.round, cache=cache, languages=langs)

    if cache is not None:
        with contextlib.suppress(OSError):
            cache_path.write_text(json.dumps(cache))

    if a.json:
        print(json.dumps({"out": str(a.out), "mode": "ab" if arms else "battery",
                          "round": a.round, "rows": rows}, indent=1))
    elif arms:
        print_ab(rows, a.per_run)
    else:
        print_battery(rows, a.per_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
