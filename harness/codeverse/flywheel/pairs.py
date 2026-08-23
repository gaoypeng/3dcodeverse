"""Preference / repair / cross-backend pairs from run records (no model calls).

Output is JSONL, one object per pair.  All kinds share::

    {kind, run, prompt, prompt_hash, track, language, generator,
     chosen: {round, commit, files: {path: code}, score, truncated_files: [...]},
     rejected: {...}, delta, reason}

* ``preference``   rounds i < j of one run with judge Δ ≥ ``min_delta``; reason =
  the judge's improvement plan of round i (what the builder was told to fix).
* ``repair``       a failed build (round k-1) → the ``repair`` round k that builds;
  ``error`` carries the structured build failure.
* ``cross_backend`` the same (prompt, track, language) run under ≥ 2 generators;
  best vs each other candidate with Δ ≥ ``min_delta``.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from codeverse.contracts.run import RoundRecord, RunRecord
from codeverse.flywheel import _git
from codeverse.flywheel.record import iter_runs
from codeverse.workspace import Workspace

MAX_INLINE_CODE = 200_000


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.strip().encode()).hexdigest()[:16]


def _side(ws: Workspace, rnd: RoundRecord, *, cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Inline code (≤ 200 KB) for a round; cached per commit."""
    key = rnd.commit or f"wt:{rnd.index}"
    if key not in cache:
        try:
            raw = _git.read_tree_at(ws, rnd.commit) if rnd.commit else _git.read_working_tree(ws)
        except _git.GitReadError:
            raw = {}
        text, skipped = _git.decode_text_files(raw, max_total=MAX_INLINE_CODE)
        cache[key] = {"files": text, "truncated_files": skipped}
    c = cache[key]
    return {
        "round": rnd.index,
        "kind": rnd.kind,
        "commit": rnd.commit,
        "score": rnd.score,
        "passed": None if rnd.judgment is None else rnd.judgment.passed,
        "build_ok": None if rnd.build is None else rnd.build.ok,
        "files": c["files"],
        "truncated_files": c["truncated_files"],
    }


def _base(ws: Workspace, rec: RunRecord, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "run": ws.root.name,
        "workspace": str(ws.root),
        "prompt": rec.spec.prompt,
        "prompt_hash": prompt_hash(rec.spec.prompt),
        "track": rec.spec.track.value,
        "language": rec.spec.language.value,
        "generator": rec.spec.backends.generator,
    }


def preference_pairs(ws: Workspace, rec: RunRecord, *, min_delta: float) -> list[dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    judged = [r for r in rec.rounds if r.judgment is not None and r.commit]
    out = []
    for a_i, lo in enumerate(judged):
        for hi in judged[a_i + 1 :]:
            delta = hi.judgment.overall - lo.judgment.overall  # type: ignore[union-attr]
            if delta < min_delta or lo.commit == hi.commit:
                continue
            pair = _base(ws, rec, "preference")
            pair.update(
                chosen=_side(ws, hi, cache=cache),
                rejected=_side(ws, lo, cache=cache),
                delta=round(delta, 4),
                reason=[it.instruction for it in lo.judgment.improvement_plan],  # type: ignore[union-attr]
                issues=[i.model_dump(mode="json") for i in lo.judgment.issues],  # type: ignore[union-attr]
            )
            out.append(pair)
    return out


def _error_of(rnd: RoundRecord) -> dict[str, Any]:
    b = rnd.build
    if b is None:
        return {}
    return {
        "type": b.error_type,
        "message": b.error_message,
        "file": b.error_file,
        "line": b.error_line,
        "stderr_tail": b.stderr_tail[-4000:],
        "stdout_tail": b.stdout_tail[-2000:],
        "gate_errors": [f.message for g in rnd.gates for f in g.errors],
    }


def repair_pairs(ws: Workspace, rec: RunRecord) -> list[dict[str, Any]]:
    """(broken round, error) → (repair round that builds)."""
    cache: dict[str, dict[str, Any]] = {}
    out = []
    rounds = rec.rounds
    for k, rnd in enumerate(rounds):
        if not rnd.kind.startswith("repair") or rnd.build is None or not rnd.build.ok:
            continue
        broken = next((r for r in reversed(rounds[:k]) if r.build is not None and not r.build.ok), None)
        if broken is None or not broken.commit or not rnd.commit or broken.commit == rnd.commit:
            continue
        pair = _base(ws, rec, "repair")
        pair.update(
            chosen=_side(ws, rnd, cache=cache),
            rejected=_side(ws, broken, cache=cache),
            delta=None,
            error=_error_of(broken),
            reason=list(rnd.instructions),
        )
        out.append(pair)
    return out


def cross_backend_pairs(groups: dict[tuple[str, str, str], list[tuple[Workspace, RunRecord]]], *, min_delta: float) -> list[dict[str, Any]]:
    out = []
    for (_h, _t, _l), runs in groups.items():
        gens = {r.spec.backends.generator for _, r in runs}
        if len(gens) < 2:
            continue
        cands = []
        for ws, rec in runs:
            best = next((r for r in rec.rounds if r.index == rec.best_round), None)
            if best is None or best.judgment is None:
                continue
            cands.append((ws, rec, best))
        if len(cands) < 2:
            continue
        cands.sort(key=lambda c: c[2].judgment.overall, reverse=True)  # type: ignore[union-attr]
        w_ws, w_rec, w_rnd = cands[0]
        w_side = _side(w_ws, w_rnd, cache={})
        w_side["generator"] = w_rec.spec.backends.generator
        for ws, rec, rnd in cands[1:]:
            delta = w_rnd.judgment.overall - rnd.judgment.overall  # type: ignore[union-attr]
            if rec.spec.backends.generator == w_rec.spec.backends.generator or delta < min_delta:
                continue
            pair = _base(w_ws, w_rec, "cross_backend")
            l_side = _side(ws, rnd, cache={})
            l_side["generator"] = rec.spec.backends.generator
            l_side["run"] = ws.root.name
            pair.update(
                chosen=w_side, rejected=l_side, delta=round(delta, 4),
                reason=f"best-of across generators: {w_rec.spec.backends.generator} > {rec.spec.backends.generator}",
                candidates=[{"run": c[0].root.name, "generator": c[1].spec.backends.generator,
                             "score": c[2].judgment.overall} for c in cands],  # type: ignore[union-attr]
            )
            out.append(pair)
    return out


def build_pairs(runs_dir: Path | str, out_jsonl: Path | str, *, min_delta: float = 0.05) -> int:
    """Write all pair kinds for the runs under ``runs_dir``; returns the number written."""
    out = Path(out_jsonl)
    out.parent.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple[str, str, str], list[tuple[Workspace, RunRecord]]] = defaultdict(list)
    n = 0
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w") as fh:
        for ws, rec in iter_runs(runs_dir):
            groups[(prompt_hash(rec.spec.prompt), rec.spec.track.value, rec.spec.language.value)].append((ws, rec))
            for pair in preference_pairs(ws, rec, min_delta=min_delta) + repair_pairs(ws, rec):
                fh.write(json.dumps(pair, ensure_ascii=False) + "\n")
                n += 1
        for pair in cross_backend_pairs(groups, min_delta=min_delta):
            fh.write(json.dumps(pair, ensure_ascii=False) + "\n")
            n += 1
    tmp.replace(out)
    return n
