"""Small SQLite index over run records (runs / rounds / usage) for dashboards.

``build_index(runs_dir, out_sqlite)`` rebuilds the database from scratch;
query helpers return plain dicts so a dashboard / notebook needs no ORM.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from codeverse.contracts.run import RoundRecord, RunRecord
from codeverse.flywheel.quality import prompt_hash, quality_tier
from codeverse.flywheel.record import effective_judgment, iter_runs
from codeverse.workspace import Workspace

_SCHEMA = """
CREATE TABLE runs (
  slug TEXT PRIMARY KEY, workspace TEXT, track TEXT, language TEXT, prompt TEXT, prompt_hash TEXT,
  generator TEXT, planner TEXT, judge TEXT, status TEXT,
  baseline_score REAL, final_score REAL, best_round INTEGER, n_rounds INTEGER, passed INTEGER,
  cost_usd REAL, input_tokens INTEGER, output_tokens INTEGER,
  started_at TEXT, finished_at TEXT, duration_s REAL, error TEXT, has_captions INTEGER,
  gate_errors INTEGER, quality_tier TEXT, best_commit TEXT
);
CREATE TABLE rounds (
  slug TEXT, round_index INTEGER, kind TEXT, commit_sha TEXT, agent_backend TEXT,
  build_ok INTEGER, gate_errors INTEGER, score REAL, passed INTEGER, n_issues INTEGER,
  cost_usd REAL, duration_s REAL, started_at TEXT,
  PRIMARY KEY (slug, round_index)
);
CREATE TABLE usage (
  slug TEXT, round_index INTEGER, role TEXT, backend TEXT, model TEXT,  -- role: round (agent+judge) | judge
  input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER, thoughts_tokens INTEGER,
  tool_calls INTEGER, cost_usd REAL, latency_ms INTEGER
);
CREATE INDEX idx_runs_track ON runs(track, language);
CREATE INDEX idx_runs_prompt ON runs(prompt_hash);
CREATE INDEX idx_rounds_slug ON rounds(slug);
"""


def _run_row(ws: Workspace, rec: RunRecord) -> tuple:
    best = next((r for r in rec.rounds if r.index == rec.best_round), None)
    best_j = effective_judgment(best) if best is not None else None  # degraded → unjudged
    passed = None if best_j is None else int(best_j.passed)
    dur = (rec.finished_at - rec.started_at).total_seconds() if rec.finished_at else None
    n_err = sum(len(g.errors) for g in best.gates) if best is not None else 0
    score = best_j.overall if best_j is not None else None
    tier = quality_tier(passed=None if passed is None else bool(passed), gate_errors=n_err, score=score)
    return (
        ws.root.name, str(ws.root), rec.spec.track.value, rec.spec.language.value, rec.spec.prompt,
        prompt_hash(rec.spec.prompt), rec.spec.backends.generator, rec.spec.backends.planner,
        rec.spec.backends.judge, rec.status.value, rec.baseline_score, rec.final_score, rec.best_round,
        len(rec.rounds), passed, rec.total_usage.cost_usd, rec.total_usage.input_tokens,
        rec.total_usage.output_tokens, rec.started_at.isoformat(),
        rec.finished_at.isoformat() if rec.finished_at else None, dur, rec.error,
        int(bool((rec.extra.get("captions") or {}).get("detailed"))),
        n_err, tier, best.commit if best is not None else "",
    )


def _round_row(slug: str, r: RoundRecord) -> tuple:
    j = effective_judgment(r)  # degraded → unscored
    return (
        slug, r.index, r.kind, r.commit, r.agent_backend,
        None if r.build is None else int(r.build.ok), sum(len(g.errors) for g in r.gates),
        j.overall if j is not None else None,
        None if j is None else int(j.passed), 0 if j is None else len(j.issues),
        r.usage.cost_usd, r.duration_s, r.started_at.isoformat(),
    )


def _usage_rows(slug: str, r: RoundRecord) -> list[tuple]:
    """``round`` = the round's total (agent session + its judge call); ``judge`` = the judge part of it."""
    rows = [(slug, r.index, "round", r.usage.backend, r.usage.model, r.usage.input_tokens, r.usage.output_tokens,
             r.usage.cached_tokens, r.usage.thoughts_tokens, r.usage.tool_calls, r.usage.cost_usd, r.usage.latency_ms)]
    if r.judgment is not None:
        u = r.judgment.usage
        rows.append((slug, r.index, "judge", u.backend, u.model, u.input_tokens, u.output_tokens, u.cached_tokens,
                     u.thoughts_tokens, u.tool_calls, u.cost_usd, u.latency_ms))
    return rows


def build_index(runs_dir: Path | str, out_sqlite: Path | str) -> int:
    """(Re)build the SQLite index; returns the number of runs indexed."""
    out = Path(out_sqlite)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    n = 0
    con = sqlite3.connect(tmp)
    try:
        con.executescript(_SCHEMA)
        for ws, rec in iter_runs(runs_dir):
            slug = ws.root.name
            con.execute(f"INSERT INTO runs VALUES ({','.join('?' * 26)})", _run_row(ws, rec))
            con.executemany(f"INSERT INTO rounds VALUES ({','.join('?' * 13)})", [_round_row(slug, r) for r in rec.rounds])
            con.executemany(f"INSERT INTO usage VALUES ({','.join('?' * 12)})",
                            [row for r in rec.rounds for row in _usage_rows(slug, r)])
            n += 1
        con.commit()
    finally:
        con.close()
    tmp.replace(out)
    return n


# --------------------------------------------------------------------------- queries
def _connect(db: Path | str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def query(db: Path | str, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    con = _connect(db)
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def summary(db: Path | str) -> list[dict[str, Any]]:
    """Per (track, language, generator): n, pass rate, mean baseline/final, cost."""
    return query(
        db,
        "SELECT track, language, generator, COUNT(*) AS n, AVG(passed) AS pass_rate, "
        "AVG(baseline_score) AS baseline_mean, AVG(final_score) AS final_mean, "
        "AVG(final_score - baseline_score) AS delta_mean, SUM(cost_usd) AS cost_usd, "
        "AVG(n_rounds) AS rounds_mean, SUM(quality_tier = 'A') AS n_tier_a, SUM(quality_tier = 'B') AS n_tier_b "
        "FROM runs GROUP BY track, language, generator ORDER BY track, language",
    )


def top_runs(db: Path | str, n: int = 20, *, track: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE track = ?" if track else ""
    params: tuple = (track, n) if track else (n,)
    return query(db, f"SELECT slug, track, language, generator, final_score, passed, quality_tier, cost_usd, n_rounds "
                     f"FROM runs {where} ORDER BY final_score DESC LIMIT ?", params)


def round_curve(db: Path | str, slug: str) -> list[dict[str, Any]]:
    return query(db, "SELECT round_index, kind, score, passed, build_ok, gate_errors, cost_usd FROM rounds "
                     "WHERE slug = ? ORDER BY round_index", (slug,))
