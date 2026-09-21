"""Refine rounds as transitions: what the round was told, what it changed, what it scored.

:mod:`codeverse.addons.dataset.pairs` exports whole code trees per round and never looks at what
asked for the change, so a refine round arrives as two 30 kB files with no cause.  This
exports the transition instead — one row per refine round::

    (the previous round's judge issues + improvement plan + gate ERRORs with their
     fix hints + the refine tasks compiled for the round)  ->  src/ diff  ->  score delta

Both sides come from the round's OWN recorded commit, never ``HEAD``: only 54 of 239
recorded runs have HEAD at their last round, and 152 end on a "restore best round rNN"
commit.  ``instructions`` is ``RoundRecord.instructions``, the task list
``build_refine_instructions`` compiled — not the per-group prompt text the sessions were
handed, which is a rendering of it.

The rows are data, not a training format: ``toolkits/llamafactory/build_refine_sft.py``
turns them into the message shape the finetune pipeline consumes.  ``--with-code`` inlines
the changed files so that conversion needs no access to the runs.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.addons.dataset.pairs import MIN_PREFERENCE_DELTA
from codeverse.contracts.artifacts import Severity
from codeverse.contracts.run import RoundRecord, RunRecord
from codeverse.record._git import GitReadError, changed_files_between, diff_between, read_tree_at
from codeverse.record.record import (
    BATTERY_MARKERS,
    effective_judgment,
    effective_score,
    iter_runs,
)
from codeverse.workspace import Workspace

#: rounds that answer a judgment.  A texture or asset round is driven by its own pass, and
#: a repair round by a build error the judge never saw.
REFINE_KINDS: tuple[str, ...] = ("refine", "rewrite", "detail")
#: a diff longer than this is truncated, with the untruncated size kept on the row
MAX_DIFF_BYTES = 200_000


class RefineTransition(BaseModel):
    """One refine round: its brief, its diff, its score delta."""

    battery: str
    run: str
    run_dir: str
    prompt_id: str
    prompt: str
    track: str
    language: str
    generator: str
    round: int
    round_kind: str
    issues: list[dict[str, Any]] = Field(description="the PREVIOUS round's judge issues")
    improvement_plan: list[dict[str, Any]]
    gate_errors: list[dict[str, Any]] = Field(description="the previous round's gate ERRORs, with fix hints")
    instructions: list[str] = Field(description="RoundRecord.instructions — the tasks compiled for this round")
    before_commit: str
    after_commit: str
    changed_files: list[str]
    diff: str
    diff_bytes: int = Field(description="the UNTRUNCATED size, so a capped row still records what it was")
    diff_truncated: bool
    score_before: float | None
    score_after: float | None
    score_delta: float | None
    outcome: str = Field(description="improved | unchanged | regressed | unscored")
    #: --with-code: the WHOLE tree before (the state the brief describes) and only the
    #: files the round changed after (the answer), so a converter needs no run access
    before_files: dict[str, str] | None = None
    after_files: dict[str, str] | None = None


def _issues(rnd: RoundRecord) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    j = effective_judgment(rnd)
    if j is None:
        return [], []
    return ([i.model_dump(mode="json") for i in j.issues],
            [i.model_dump(mode="json") for i in j.improvement_plan])


def _gate_errors(rnd: RoundRecord) -> list[dict[str, Any]]:
    return [{"gate": g.gate, "target": f.target, "message": f.message, "fix_hint": f.fix_hint}
            for g in rnd.gates for f in g.findings if f.severity == Severity.ERROR]


def outcome_of(delta: float | None, threshold: float) -> str:
    """The label.  One rule, shared with ``pairs.MIN_PREFERENCE_DELTA``, so a Δ that is a
    preference pair there is never "unchanged" here."""
    if delta is None:
        return "unscored"
    if delta >= threshold:
        return "improved"
    return "regressed" if delta <= -threshold else "unchanged"


def _transition(ws: Workspace, rec: RunRecord, prev: RoundRecord, cur: RoundRecord, *, battery: str, run: str,
                threshold: float, max_diff_bytes: int, with_code: bool) -> RefineTransition:
    diff, total, truncated = diff_between(ws, prev.commit, cur.commit, max_bytes=max_diff_bytes)
    changed = changed_files_between(ws, prev.commit, cur.commit)
    before_s, after_s = effective_score(prev), effective_score(cur)
    delta = None if before_s is None or after_s is None else round(after_s - before_s, 4)
    issues, plan = _issues(prev)
    files_before = files_after = None
    if with_code:
        def _text(commit: str, keep: list[str] | None) -> dict[str, str]:
            out = {}
            for path, raw in read_tree_at(ws, commit, paths=keep).items():
                try:
                    out[path] = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue  # a binary file under src/: the diff already says it changed
            return out
        files_before, files_after = _text(prev.commit, None), _text(cur.commit, changed)
    return RefineTransition(
        battery=battery, run=run, run_dir=str(ws.root), prompt_id=rec.spec.id, prompt=rec.spec.prompt,
        track=rec.spec.track.value, language=rec.spec.language.value, generator=rec.spec.backends.generator,
        round=cur.index, round_kind=cur.kind, issues=issues, improvement_plan=plan,
        gate_errors=_gate_errors(prev), instructions=list(cur.instructions),
        before_commit=prev.commit, after_commit=cur.commit, changed_files=changed,
        diff=diff, diff_bytes=total, diff_truncated=truncated,
        score_before=before_s, score_after=after_s, score_delta=delta,
        outcome=outcome_of(delta, threshold), before_files=files_before, after_files=files_after)


#: path segments that are run LAYOUT, not a battery name — the same markers ``iter_runs``
#: discovers batteries by
_LAYOUT_DIRS = frozenset(BATTERY_MARKERS)


def _identity(root: Path, found: Any) -> tuple[str, str]:
    """``(battery, run)`` for a run, taken from where it PHYSICALLY lives.

    ``bench/out``'s batteries symlink each other's cells (54 of 239 runs are reachable
    twice), so labelling by the path a scan happened to reach first credits a run to a
    battery it never ran in."""
    try:
        rel = found.ws.root.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return found.run_id.battery, found.run_id.rel
    head, _, rest = rel.partition("/")
    if not rest or head in _LAYOUT_DIRS:
        return found.run_id.battery, rel
    return head, rest


def transitions(runs_dir: Path | str, *, threshold: float = MIN_PREFERENCE_DELTA,
                max_diff_bytes: int = MAX_DIFF_BYTES, with_code: bool = False,
                drops: Counter[str] | None = None) -> Iterator[RefineTransition]:
    """Every exportable refine transition under ``runs_dir``; every one that is not is counted.

    A run reachable through more than one path (54 of 239 under ``bench/out``, where the
    batteries symlink each other's cells) is exported once, under the battery it physically
    lives in."""
    drops = drops if drops is not None else Counter()
    root = Path(runs_dir)
    seen: set[Path] = set()
    for found in iter_runs(root, on_error=lambda p, e: drops.update([f"unreadable_record: {type(e).__name__}"])):
        physical = found.ws.root.resolve()
        if physical in seen:
            drops["duplicate_run"] += 1
            continue
        seen.add(physical)
        rounds = {r.index: r for r in found.record.rounds}
        for cur in found.record.rounds:
            if cur.kind not in REFINE_KINDS:
                continue
            prev = rounds.get(cur.index - 1)
            if prev is None:
                drops["no_predecessor"] += 1
            elif not (prev.commit and cur.commit):
                drops["no_commit"] += 1
            elif prev.build is None or not prev.build.ok:
                drops["predecessor_build_failed"] += 1
            elif effective_judgment(prev) is None:
                drops["predecessor_unjudged"] += 1
            else:
                try:
                    battery, run = _identity(root, found)
                    yield _transition(found.ws, found.record, prev, cur, battery=battery, run=run,
                                      threshold=threshold, max_diff_bytes=max_diff_bytes, with_code=with_code)
                except GitReadError as e:
                    drops[f"git_read_failed: {str(e)[:60]}"] += 1


def build_refine(runs_dir: Path | str, out_jsonl: Path | str, **kw: Any) -> tuple[int, Counter[str]]:
    """Write the transitions as JSONL; return ``(rows, drops)``."""
    drops: Counter[str] = Counter()
    out = Path(out_jsonl)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    tmp = out.with_suffix(out.suffix + ".part")
    with tmp.open("w") as fh:
        for row in transitions(runs_dir, drops=drops, **kw):
            fh.write(row.model_dump_json(exclude_none=True) + "\n")
            n += 1
    tmp.replace(out)
    return n, drops


__all__ = ["MAX_DIFF_BYTES", "REFINE_KINDS", "RefineTransition", "build_refine", "outcome_of", "transitions"]
