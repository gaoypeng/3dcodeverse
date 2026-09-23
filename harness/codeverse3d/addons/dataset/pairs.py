"""Preference / repair / cross-backend pairs from run records (no model calls).

Output is JSONL, one object per pair.  All kinds share::

    {kind, run, prompt, prompt_hash, track, language, generator,
     chosen: {round, commit, files: {path: code}, score, truncated_files: [...]},
     rejected: {...}, delta, reason}

* ``preference``   rounds i < j of one run with judge Δ ≥ ``min_delta``; reason =
  the judge's improvement plan of round i (what the builder was told to fix).
  Degraded (judge-outage) verdicts are treated as *no score*, never as a 0.0.
* ``repair``       a failed build (round k) → the next round that builds, matched
  structurally (no track ever emits a ``kind='repair'`` round — lifecycle labels
  the fixing round ``refine``); ``error`` carries the structured build failure.
  ``source: "round"``.
* ``repair`` (``source: "in_round"``) — a round whose build failed right after
  generation and was fixed by ``build_with_repair`` inside the same round
  (``notes`` say "repair attempts: N (fixed)"): the ``rNN <kind>: generated``
  commit is the rejected state, the round's final commit the chosen one; the
  error message comes from the round's failing ``build.done`` events.
* ``cross_backend`` the same (prompt, track, language) run under ≥ 2 generators; each
  run is its picked round (``addons/select``), the top one against each other candidate
  with Δ ≥ ``min_delta``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from codeverse3d.addons import select
from codeverse3d.addons.dataset.quality import prompt_id
from codeverse3d.contracts.run import RoundRecord, RunRecord
from codeverse3d.proc import read_jsonl_lenient
from codeverse3d.record import _git
from codeverse3d.record.record import effective_judgment, iter_runs, skip_unreadable
from codeverse3d.workspace import Workspace

__all__ = ["build_pairs", "preference_pairs", "repair_pairs", "in_round_repair_pairs",
           "cross_backend_pairs"]

MAX_INLINE_CODE = 200_000


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
    j = effective_judgment(rnd)
    return {
        "round": rnd.index,
        "kind": rnd.kind,
        "commit": rnd.commit,
        "score": j.overall if j else None,
        "passed": j.passed if j else None,
        "build_ok": None if rnd.build is None else rnd.build.ok,
        "files": c["files"],
        "truncated_files": c["truncated_files"],
    }


def _base(ws: Workspace, rec: RunRecord, kind: str, *, slug: str | None = None) -> dict[str, Any]:
    return {
        "kind": kind,
        "run": slug or ws.root.name,
        "workspace": str(ws.root),
        "prompt": rec.spec.prompt,
        "prompt_hash": prompt_id(rec.spec.prompt),
        "track": rec.spec.track.value,
        "language": rec.spec.language.value,
        "generator": rec.spec.backends.generator,
    }


#: the judge Δ at which two rounds are a preference pair — and, in
#: :mod:`codeverse3d.addons.dataset.refine`, the Δ at which a transition counts as improved.  One
#: constant, so a Δ that is a preference pair here is never "unchanged" there.
MIN_PREFERENCE_DELTA = 0.05


def preference_pairs(ws: Workspace, rec: RunRecord, *, min_delta: float,
                     slug: str | None = None) -> list[dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    # degraded (judge-outage) verdicts are glitches, not 0.0 scores — leave those rounds out
    judged = [r for r in rec.rounds if effective_judgment(r) is not None and r.commit]
    out = []
    for a_i, lo in enumerate(judged):
        for hi in judged[a_i + 1 :]:
            lo_j, hi_j = effective_judgment(lo), effective_judgment(hi)
            delta = hi_j.overall - lo_j.overall  # type: ignore[union-attr]
            if delta < min_delta or lo.commit == hi.commit:
                continue
            pair = _base(ws, rec, "preference", slug=slug)
            pair.update(
                chosen=_side(ws, hi, cache=cache),
                rejected=_side(ws, lo, cache=cache),
                delta=round(delta, 4),
                reason=[it.instruction for it in lo_j.improvement_plan],  # type: ignore[union-attr]
                issues=[i.model_dump(mode="json") for i in lo_j.issues],  # type: ignore[union-attr]
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


def repair_pairs(ws: Workspace, rec: RunRecord, *, slug: str | None = None) -> list[dict[str, Any]]:
    """(broken round, error) → (the next round that builds).

    Matched structurally: the tracks never emit a ``kind='repair'`` round
    (lifecycle labels every post-baseline round ``refine``), so any round whose
    build succeeds directly repairs the nearest earlier failed build."""
    cache: dict[str, dict[str, Any]] = {}
    out = []
    rounds = rec.rounds
    for k, broken in enumerate(rounds):
        if broken.build is None or broken.build.ok or not broken.commit:
            continue
        fixed = next((r for r in rounds[k + 1 :] if r.build is not None and r.build.ok and r.commit), None)
        if fixed is None or broken.commit == fixed.commit:
            continue
        pair = _base(ws, rec, "repair", slug=slug)
        pair.update(
            source="round",
            chosen=_side(ws, fixed, cache=cache),
            rejected=_side(ws, broken, cache=cache),
            delta=None,
            error=_error_of(broken),
            reason=list(fixed.instructions),
        )
        out.append(pair)
    return out


def _round_build_errors(ws: Workspace, index: int) -> list[str]:
    """Failing ``build.done`` error messages of one round, from events.jsonl."""
    out: list[str] = []
    for ev in read_jsonl_lenient(ws.events_path, dicts_only=True):
        if ev.get("event") == "build.done" and ev.get("round") == index and ev.get("ok") is False:
            msg = str(ev.get("error") or "").strip()
            if msg:
                out.append(msg)
    return out


def in_round_repair_pairs(ws: Workspace, rec: RunRecord, *, slug: str | None = None) -> list[dict[str, Any]]:
    """Builds fixed by ``build_with_repair`` *inside* a round.

    ``tracks/steps.py`` commits ``rNN <kind>: generated`` right before the
    build+repair loop and notes "repair attempts: N (fixed)" when the loop
    repaired a failing build; the generated tree is the rejected state."""
    cache: dict[str, dict[str, Any]] = {}
    out = []
    for rnd in rec.rounds:
        if rnd.build is None or not rnd.build.ok or not rnd.commit:
            continue
        if "repair attempts:" not in rnd.notes or "(fixed)" not in rnd.notes:
            continue
        generated = _git.commit_by_subject(ws, f"r{rnd.index:02d} {rnd.kind}: generated")
        if not generated or generated == rnd.commit or not ws.has_commit(generated):
            continue
        try:
            raw = _git.read_tree_at(ws, generated)
        except _git.GitReadError:
            continue
        rej_files, rej_skipped = _git.decode_text_files(raw, max_total=MAX_INLINE_CODE)
        chosen = _side(ws, rnd, cache=cache)
        if rej_files == chosen["files"]:
            continue  # the repair changed nothing under the code roots
        errors = _round_build_errors(ws, rnd.index)
        pair = _base(ws, rec, "repair", slug=slug)
        pair.update(
            source="in_round",
            chosen=chosen,
            rejected={"round": rnd.index, "kind": rnd.kind, "commit": generated, "score": None, "passed": None,
                      "build_ok": False, "files": rej_files, "truncated_files": rej_skipped},
            delta=None,
            error={"type": "", "message": errors[-1] if errors else "", "file": "", "line": None,
                   "stderr_tail": "", "stdout_tail": "", "gate_errors": [], "messages": errors},
            reason=[f"in-round build repair ({rnd.notes})"],
        )
        out.append(pair)
    return out


def cross_backend_pairs(
    groups: dict[tuple[str, str, str], list[tuple[Workspace, RunRecord, str]]], *, min_delta: float
) -> list[dict[str, Any]]:
    """``groups`` values are ``(ws, record, slug)`` — the slug is the RunId slug the
    scan minted (``ws.root.name`` collides across nested battery layouts)."""
    out = []
    for (_h, _t, _l), runs in groups.items():
        gens = {r.spec.backends.generator for _, r, _s in runs}
        if len(gens) < 2:
            continue
        cands = []
        for ws, rec, slug in runs:
            picked = select.summarise(ws.root, record=rec).picked_round  # None = no judged round
            rnd = next((r for r in rec.rounds if r.index == picked), None)
            if rnd is not None:
                cands.append((ws, rec, slug, rnd))
        if len(cands) < 2:
            continue
        cands.sort(key=lambda c: effective_judgment(c[3]).overall, reverse=True)  # type: ignore[union-attr]
        w_ws, w_rec, w_slug, w_rnd = cands[0]
        w_side = _side(w_ws, w_rnd, cache={})
        w_side["generator"] = w_rec.spec.backends.generator
        for ws, rec, slug, rnd in cands[1:]:
            delta = effective_judgment(w_rnd).overall - effective_judgment(rnd).overall  # type: ignore[union-attr]
            if rec.spec.backends.generator == w_rec.spec.backends.generator or delta < min_delta:
                continue
            pair = _base(w_ws, w_rec, "cross_backend", slug=w_slug)
            l_side = _side(ws, rnd, cache={})
            l_side["generator"] = rec.spec.backends.generator
            l_side["run"] = slug
            pair.update(
                chosen=w_side, rejected=l_side, delta=round(delta, 4),
                reason=f"best-of across generators: {w_rec.spec.backends.generator} > {rec.spec.backends.generator}",
                candidates=[{"run": c[2], "generator": c[1].spec.backends.generator,
                             "score": effective_judgment(c[3]).overall} for c in cands],  # type: ignore[union-attr]
            )
            out.append(pair)
    return out


def build_pairs(runs_dir: Path | str, out_jsonl: Path | str, *, min_delta: float = MIN_PREFERENCE_DELTA) -> int:
    """Write all pair kinds for the runs under ``runs_dir``; returns the number written."""
    out = Path(out_jsonl)
    out.parent.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple[str, str, str], list[tuple[Workspace, RunRecord, str]]] = defaultdict(list)
    n = 0
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w") as fh:
        for ws, rec, rid in iter_runs(runs_dir, on_error=skip_unreadable):
            slug = rid.slug
            groups[(prompt_id(rec.spec.prompt), rec.spec.track.value, rec.spec.language.value)].append((ws, rec, slug))
            pairs = (preference_pairs(ws, rec, min_delta=min_delta, slug=slug)
                     + repair_pairs(ws, rec, slug=slug) + in_round_repair_pairs(ws, rec, slug=slug))
            for pair in pairs:
                fh.write(json.dumps(pair, ensure_ascii=False) + "\n")
                n += 1
        for pair in cross_backend_pairs(groups, min_delta=min_delta):
            fh.write(json.dumps(pair, ensure_ascii=False) + "\n")
            n += 1
    tmp.replace(out)
    return n
