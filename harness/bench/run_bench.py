"""Run a prompt battery through a track with N parallel workers; resumable.

Layout of ``out_dir``::

    battery.json        the battery as loaded (+ overrides used)
    runs/<id>/          one workspace per prompt (slug = prompt id)
    results.jsonl       append-only, one line per finished prompt (resume source)
    results.csv / results.json   rewritten at the end from results.jsonl

Every prompt becomes a ``Spec`` with the battery's track/language, a FIXED judge
model (methodology: paired runs share the judge), and the generator under test.
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

from codeverse.config import get_settings
from codeverse.contracts.common import Backends, Budget, Language, Track
from codeverse.contracts.run import RunRecord
from codeverse.contracts.spec import Constraints, Spec
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


class Battery(BaseModel):
    name: str
    track: Track
    language: Language
    description: str = ""
    prompts: list[BenchPrompt] = Field(min_length=1)

    @classmethod
    def load(cls, path: Path | str) -> Battery:
        data = yaml.safe_load(Path(path).read_text())
        return cls.model_validate(data)


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
    max_usd: float = 5.0
    max_minutes: float = 60.0
    parallel: int = 2
    limit: int | None = None
    ids: list[str] = Field(default_factory=list)
    tiers: list[str] = Field(default_factory=list)
    resume: bool = True


def build_spec(
    battery: Battery, item: BenchPrompt, *, backends: Backends, rounds: int, max_usd: float,
    max_minutes: float, tag0: str, extra_tags: Sequence[str] = (),
) -> Spec:
    """Shared Spec core for bench drivers (``run_bench`` / ``compare_backends``).
    Drivers resolve their own ``Backends`` and pass their leading tag."""
    return Spec(
        id=f"{battery.name}/{item.id}", track=battery.track, language=item.language or battery.language, prompt=item.prompt,
        constraints=Constraints(must_have=list(item.must_have), dimensions_m=item.dimensions_m),
        budget=Budget(max_rounds=rounds, max_usd=max_usd, max_minutes=max_minutes),
        backends=backends, tags=[tag0, battery.name, item.tier, item.category, *extra_tags, *item.tags],
    )


def spec_for(battery: Battery, item: BenchPrompt, opts: BenchOptions) -> Spec:
    backends = get_settings().backends(generator=opts.generator, planner=opts.planner, judge=opts.judge)
    return build_spec(battery, item, backends=backends, rounds=opts.rounds, max_usd=opts.max_usd,
                      max_minutes=opts.max_minutes, tag0="bench")


def result_from_record(item: BenchPrompt, rec: RunRecord, minutes: float, ws: Workspace) -> BenchItemResult:
    best = next((r for r in rec.rounds if r.index == rec.best_round), None)
    return BenchItemResult(
        id=item.id, tier=item.tier, category=item.category, score_baseline=rec.baseline_score,
        score_final=rec.final_score, passed=None if best is None or best.judgment is None else best.judgment.passed,
        rounds=len(rec.rounds), cost_usd=rec.total_usage.cost_usd, minutes=round(minutes, 2),
        status=rec.status.value, errors=rec.error, workspace=str(ws.root),
        generator=rec.spec.backends.generator, judge=rec.spec.backends.judge,
    )


def _load_done(results_jsonl: Path) -> dict[str, BenchItemResult]:
    done: dict[str, BenchItemResult] = {}
    if results_jsonl.is_file():
        for line in results_jsonl.read_text().splitlines():
            if line.strip():
                r = BenchItemResult.model_validate_json(line)
                done[r.id] = r
    return done


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


def _select(battery: Battery, opts: BenchOptions) -> list[BenchPrompt]:
    """Deprecated: use :func:`select_prompts`."""
    return select_prompts(battery, ids=opts.ids, tiers=opts.tiers, limit=opts.limit)


RunFn = Callable[[Spec, Workspace, bool], RunRecord]


def default_run_track(spec: Spec, ws: Workspace, resume: bool) -> RunRecord:
    """Run the spec's track (the real thing; tests inject a fake ``run_fn``)."""
    from codeverse.tracks import get_track

    return get_track(spec.track).run(spec, ws, resume=resume)


#: deprecated alias — use :func:`default_run_track`
_default_run = default_run_track


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
    done = _load_done(results_jsonl) if opts.resume else {}
    todo = [p for p in select_prompts(battery, ids=opts.ids, tiers=opts.tiers, limit=opts.limit) if p.id not in done]

    def _one(item: BenchPrompt) -> BenchItemResult:
        ws = Workspace(out / "runs" / item.id)
        resume = ws.exists()
        if not resume:
            ws.create()
        spec = spec_for(battery, item, opts)
        if not resume:
            ws.write_json(ws.spec_path, spec)
        t0 = time.time()
        try:
            rec = run_fn(spec, ws, resume)
        except Exception as e:  # one failing prompt must not kill the battery
            return BenchItemResult(id=item.id, tier=item.tier, category=item.category, status="error",
                                   errors=f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}",
                                   minutes=round((time.time() - t0) / 60, 2), workspace=str(ws.root),
                                   generator=spec.backends.generator, judge=spec.backends.judge)
        return result_from_record(item, rec, (time.time() - t0) / 60, ws)

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
