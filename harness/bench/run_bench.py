"""Run a prompt battery through a track with N parallel workers; resumable.

Layout of ``out_dir``::

    battery.json        the battery as loaded (+ overrides used)
    runs/<id>/          one workspace per prompt (slug = prompt id)
    results.jsonl       append-only, one line per finished prompt (resume source)
    results.csv / results.json   rewritten at the end from results.jsonl

Every prompt becomes a ``Spec`` with the battery's track/language, a FIXED judge
model (methodology: paired runs share the judge), and the generator under test.

Each prompt runs inside its own ``codeverse.cost.run_ledger``, exactly like a
``3dcv make``: the batteries are where most runs come from, so without it the
priced per-call rows of a whole battery went to the per-process fallback log and
``3dcv cost --runs-dir <out>/runs`` had to reconstruct them from trajectories.
The binding is context-local, so ``--parallel N`` keeps N ledgers apart.
"""

from __future__ import annotations

import csv
import json
import time
import traceback
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from bench._infra import is_infra_failure
from bench._jsonl import read_jsonl, seal_for_append
from codeverse.config import get_settings
from codeverse.contracts.common import Backends, Budget, Language, Track
from codeverse.contracts.run import RunRecord
from codeverse.contracts.spec import Constraints, ReferenceImage, Spec
from codeverse.cost import run_ledger
from codeverse.proc import exclusive
from codeverse.workspace import Workspace

RESULT_FIELDS = ("id", "tier", "category", "score_baseline", "score_final", "passed", "rounds", "cost_usd",
                 "minutes", "status", "errors", "workspace", "generator", "judge")


class BenchPrompt(BaseModel):
    id: str
    prompt: str
    tier: str = Field(description="easy | medium | hard")
    category: str = ""
    must_have: list[str] = Field(default_factory=list)
    dimensions_m: dict[str, float] | None = None
    tags: list[str] = Field(default_factory=list)
    language: Language | None = Field(
        default=None, description="per-prompt override of the battery language "
        "(e.g. the opengl_python rows of a glsl_shader battery)")
    references: list[str] = Field(
        default_factory=list,
        description="reference image paths (relative to the battery file); bench/refs/<id>/*.png|jpg are added automatically")


class Battery(BaseModel):
    name: str
    track: Track
    language: Language
    description: str = ""
    prompts: list[BenchPrompt] = Field(min_length=1)
    source_dir: Path | None = Field(default=None, exclude=True, description="directory of the yaml this battery was loaded from")

    @classmethod
    def load(cls, path: Path | str) -> Battery:
        data = yaml.safe_load(Path(path).read_text())
        return cls.model_validate(data).model_copy(update={"source_dir": Path(path).resolve().parent})


class BenchItemResult(BaseModel):
    id: str
    tier: str
    category: str = ""
    score_baseline: float | None = None
    score_final: float | None = None
    passed: bool | None = None
    rounds: int = 0
    cost_usd: float = 0.0
    minutes: float = 0.0
    status: str = ""
    errors: str = ""
    workspace: str = ""
    generator: str = ""
    judge: str = ""


class BenchOptions(BaseModel):
    generator: str | None = None
    planner: str | None = None
    judge: str | None = None
    rounds: int = 4
    max_minutes: float = 60.0
    #: measured (docs/COST.md Part III): one bench cell holds ~0.9 model calls
    #: in flight, so 8 cells sit near 7 — far inside the 64-call model knee — and
    #: their builds sit inside the 16-build subprocess knee.
    parallel: int = 8
    limit: int | None = None
    ids: list[str] = Field(default_factory=list)
    tiers: list[str] = Field(default_factory=list)
    # No "start over" switch: --redo-status is the ONE way to re-run a recorded prompt,
    # because it archives the old tree first (RUNBOOK 7.u).  The `resume` flag it replaced
    # skipped that archive and resumed the old workspace, spec and clock (dropped 2026-08-30).
    redo_status: list[str] = Field(default_factory=list,
                                   description="re-run prompts already recorded with one of these statuses "
                                               "(the point of `infra_failed`: retry what the weather lost)")


REFS_DIR = Path(__file__).resolve().parent / "refs"
REF_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def discover_references(item: BenchPrompt, track: Track, *, battery_dir: Path | None = None, refs_dir: Path = REFS_DIR) -> list[ReferenceImage]:
    """The prompt's reference images: its explicit ``references`` plus ``bench/refs/<id>/*``.

    One folder per prompt id, maintained by hand (photos of the real thing), so an A/B of
    "with references" against "without" is the same battery with the folder present or
    absent — nothing else changes.  Object tracks get role ``target`` for the first image
    and ``detail`` for the rest (the silhouette judge matches the target); graphics and
    scene get ``likeness`` (what the real thing looks like — LikenessJudge, no silhouette).
    The note is the file stem with underscores as spaces, so a name like
    ``aurora_green_spiral_sea.png`` tells the agent what it is looking at.
    """
    paths: list[Path] = []
    base = battery_dir or Path.cwd()
    for rel in item.references:
        p = Path(rel) if Path(rel).is_absolute() else base / rel
        if p.is_file():
            paths.append(p.resolve())
    folder = refs_dir / item.id
    if folder.is_dir():
        paths.extend(sorted(p.resolve() for p in folder.iterdir() if p.suffix.lower() in REF_SUFFIXES and p.is_file()))
    likeness = track in (Track.GRAPHICS, Track.SCENE)
    out: list[ReferenceImage] = []
    for i, p in enumerate(paths):
        role = "likeness" if likeness else ("target" if i == 0 else "detail")
        out.append(ReferenceImage(path=str(p), role=role, note=p.stem.replace("_", " ")))
    return out


def build_spec(
    battery: Battery, item: BenchPrompt, *, backends: Backends, rounds: int,
    max_minutes: float, tag0: str, extra_tags: Sequence[str] = (),
) -> Spec:
    """Shared Spec core for bench drivers (``run_bench`` / ``compare_backends``).
    Drivers resolve their own ``Backends`` and pass their leading tag."""
    return Spec(
        id=f"{battery.name}/{item.id}", track=battery.track, language=item.language or battery.language, prompt=item.prompt,
        constraints=Constraints(must_have=list(item.must_have), dimensions_m=item.dimensions_m),
        references=discover_references(item, battery.track, battery_dir=battery.source_dir),
        budget=Budget(max_rounds=rounds, max_minutes=max_minutes),
        backends=backends, tags=[tag0, battery.name, item.tier, item.category, *extra_tags, *item.tags],
    )


def spec_for(battery: Battery, item: BenchPrompt, opts: BenchOptions) -> Spec:
    backends = get_settings().backends(generator=opts.generator, planner=opts.planner, judge=opts.judge)
    return build_spec(battery, item, backends=backends, rounds=opts.rounds,
                      max_minutes=opts.max_minutes, tag0="bench")


def result_from_record(item: BenchPrompt, rec: RunRecord, minutes: float, ws: Workspace) -> BenchItemResult:
    best = next((r for r in rec.rounds if r.index == rec.best_round), None)
    # A run whose rounds all lost their verdict still reports a normal status (`plateau`
    # after three unjudged rounds), so a whole ARM can read as healthy and score nothing —
    # what a worktree without node_modules did on 2026-09-04: render_glb died, every round
    # skipped the judge, ten cells came back with score=None and status=plateau.  Say it
    # where the row is read.
    unjudged = bool(rec.rounds) and all(r.judgment is None for r in rec.rounds)
    error = rec.error
    if unjudged and not error:
        error = f"no verdict in any of {len(rec.rounds)} round(s) — the judge was skipped every time"
    return BenchItemResult(
        id=item.id, tier=item.tier, category=item.category, score_baseline=rec.baseline_score,
        score_final=rec.final_score, passed=None if best is None or best.judgment is None else best.judgment.passed,
        rounds=len(rec.rounds), cost_usd=rec.total_usage.cost_usd, minutes=round(minutes, 2),
        status=rec.status.value, errors=error, workspace=str(ws.root),
        generator=rec.spec.backends.generator, judge=rec.spec.backends.judge,
    )


def _load_done(results_jsonl: Path) -> dict[str, BenchItemResult]:
    """Rows already paid for, latest wins.  Tolerant by design: this file is the
    resume source and it is appended to a line at a time, so the run that a SIGKILL
    ended is precisely the one whose last line is half-written."""
    return {r.id: r for r in read_jsonl(results_jsonl, BenchItemResult)}


def select_prompts(
    battery: Battery, *, ids: Sequence[str] = (), tiers: Sequence[str] = (), limit: int | None = None
) -> list[BenchPrompt]:
    """Filter a battery's prompts by id list, tier list, then head-``limit``
    (single owner for the selection semantics of every bench driver)."""
    items = list(battery.prompts)
    if ids:
        items = [p for p in items if p.id in set(ids)]
    if tiers:
        items = [p for p in items if p.tier in set(tiers)]
    return items[:limit] if limit is not None else items


RunFn = Callable[[Spec, Workspace, bool], RunRecord]



def archive_attempt(root: Path) -> Path:
    """Move a run workspace aside as ``<root>.attempt<N>`` (N = first free) and return the new path."""
    n = 1
    while (root.parent / f"{root.name}.attempt{n}").exists():
        n += 1
    dest = root.parent / f"{root.name}.attempt{n}"
    root.rename(dest)
    return dest


def default_run_track(spec: Spec, ws: Workspace, resume: bool) -> RunRecord:
    """Run the spec's track (the real thing; tests inject a fake ``run_fn``)."""
    from codeverse.tracks import get_track

    return get_track(spec.track).run(spec, ws, resume=resume)


def run_battery(
    battery_path: Path | str, out_dir: Path | str, opts: BenchOptions | None = None, *,
    run_fn: RunFn | None = None, on_result: Callable[[BenchItemResult], None] | None = None,
) -> list[BenchItemResult]:
    """Run (or resume) a battery; returns every result (previous + new)."""
    opts = opts or BenchOptions()
    run_fn = run_fn or default_run_track
    battery = Battery.load(battery_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "battery.json").write_text(json.dumps({"battery": battery.model_dump(mode="json"),
                                                  "options": opts.model_dump(mode="json"),
                                                  "started_at": datetime.now(UTC).isoformat()}, indent=2))
    results_jsonl = out / "results.jsonl"
    done = _load_done(results_jsonl)
    # `--redo-status infra_failed` re-runs the cells the weather lost, once it clears
    redo_ids = {k for k, r in done.items() if r.status in set(opts.redo_status)}
    for pid in redo_ids:
        del done[pid]
    todo = [p for p in select_prompts(battery, ids=opts.ids, tiers=opts.tiers, limit=opts.limit) if p.id not in done]

    def _one(item: BenchPrompt) -> BenchItemResult:
        ws = Workspace(out / "runs" / item.id)
        if item.id in redo_ids and ws.exists():
            # A redo starts FRESH: resuming the old workspace keeps its spec (the old
            # max_minutes) and its clock, so a `budget` row redone with --max-minutes 120
            # was over budget before its first round (measured 2026-08-26: clock_q4 and
            # lighthouse_1 came back `budget`, 0 rounds, 60.3 / 76.7 min "elapsed").
            # The old tree is kept beside it as <id>.attempt<N>, the way ab_plan does.
            archive_attempt(ws.root)
        resume = ws.exists()
        if not resume:
            ws.create()
        spec = spec_for(battery, item, opts)
        if not resume:
            ws.write_json(ws.spec_path, spec)
        t0 = time.time()
        try:
            # one writer per run dir: --parallel runs these in threads of ONE process, so
            # the run mutex is what keeps two cells off the same workspace
            with exclusive(ws.root, what=f"bench {item.id}"), run_ledger(ws.root, run=item.id):
                rec = run_fn(spec, ws, resume)
        except Exception as e:  # one failing prompt must not kill the battery
            # A provider outage is not a result: an hour spent retrying a 503 is not model
            # latency and not a crash.  compare_backends has classified this since
            # 2026-08-24; this driver never adopted it, so a 60-minute storm cell was
            # averaged into min/run (12.8x inflation, measured) and counted in the same
            # `errors` column as a genuine failure.
            status = "infra_failed" if is_infra_failure(e) else "error"
            return BenchItemResult(id=item.id, tier=item.tier, category=item.category, status=status,
                                   errors=f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}",
                                   minutes=round((time.time() - t0) / 60, 2), workspace=str(ws.root),
                                   generator=spec.backends.generator, judge=spec.backends.judge)
        return result_from_record(item, rec, (time.time() - t0) / 60, ws)

    seal_for_append(results_jsonl)  # a kill left the last row unterminated; do not glue onto it
    with ThreadPoolExecutor(max_workers=max(1, opts.parallel)) as pool, results_jsonl.open("a") as fh:
        futs = {pool.submit(_one, item): item for item in todo}
        for fut in as_completed(futs):
            res = fut.result()
            done[res.id] = res
            fh.write(res.model_dump_json() + "\n")
            fh.flush()
            if on_result:
                on_result(res)
    ordered = [done[p.id] for p in battery.prompts if p.id in done]
    write_results(out, ordered)
    return ordered


def write_results(out_dir: Path, results: list[BenchItemResult]) -> None:
    rows: list[dict[str, Any]] = [r.model_dump(mode="json") for r in results]
    (out_dir / "results.json").write_text(json.dumps(rows, indent=2))
    with (out_dir / "results.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(RESULT_FIELDS))
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in RESULT_FIELDS})
