"""Small SQLite index over run records (runs / rounds / usage) for dashboards.

``build_index(runs_dir, out_sqlite)`` rebuilds the database from scratch;
query helpers return plain dicts so a dashboard / notebook needs no ORM.  A run's
row describes the round ``addons/select`` picks (``picked_round``); a run has no
pass/fail — the ``rounds`` table carries the judge's own verdict per round.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from codeverse3d.addons import select
from codeverse3d.addons.dataset.quality import prompt_hash, quality_tier
from codeverse3d.addons.dataset.sample import gate_error_summary
from codeverse3d.contracts.run import RoundRecord, RunId, RunRecord
from codeverse3d.record.record import effective_judgment, iter_runs, skip_unreadable
from codeverse3d.workspace import Workspace

_SCHEMA = """
CREATE TABLE runs (
  slug TEXT PRIMARY KEY, workspace TEXT, track TEXT, language TEXT, prompt TEXT, prompt_hash TEXT,
  generator TEXT, planner TEXT, judge TEXT, status TEXT,  -- status: why the run stopped
  baseline_score REAL, picked_score REAL, picked_round INTEGER, n_rounds INTEGER,
  cost_usd REAL, input_tokens INTEGER, output_tokens INTEGER,
  started_at TEXT, finished_at TEXT, minutes REAL, error TEXT, has_captions INTEGER,
  gate_errors INTEGER, quality_tier TEXT, picked_commit TEXT,
  rel TEXT, arm TEXT, cell TEXT  -- run dir relative to the scan root + battery layout segments
);
CREATE TABLE rounds (
  slug TEXT, round_index INTEGER, kind TEXT, commit_sha TEXT, agent_backend TEXT,
  build_ok INTEGER, gate_errors INTEGER, score REAL, passed INTEGER, n_issues INTEGER,
  cost_usd REAL, minutes REAL, started_at TEXT,
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


def _run_row(ws: Workspace, rec: RunRecord, rid: RunId) -> tuple:
    s = select.summarise(ws.root, record=rec)
    picked = next((r for r in rec.rounds if r.index == s.picked_round), None)
    j = effective_judgment(picked) if picked is not None else None  # degraded → unjudged
    n_err = sum(gate_error_summary(picked).values())
    tier = quality_tier(passed=j.passed if j else None, gate_errors=n_err, score=j.overall if j else None)
    return (
        rid.slug, str(ws.root), rec.spec.track.value, rec.spec.language.value, rec.spec.prompt,
        prompt_hash(rec.spec.prompt), rec.spec.backends.generator, rec.spec.backends.planner,
        rec.spec.backends.judge, s.stop_reason, s.baseline_score, s.picked_score, s.picked_round,
        len(rec.rounds), rec.total_usage.cost_usd, rec.total_usage.input_tokens,
        rec.total_usage.output_tokens, rec.started_at.isoformat(),
        rec.finished_at.isoformat() if rec.finished_at else None, rec.minutes, rec.error,
        int(bool((rec.extra.get("captions") or {}).get("detailed"))),
        n_err, tier, picked.commit if picked is not None else "",
        rid.rel, rid.arm, rid.cell,
    )


def _round_row(slug: str, r: RoundRecord) -> tuple:
    j = effective_judgment(r)  # degraded → unscored
    return (
        slug, r.index, r.kind, r.commit, r.agent_backend,
        None if r.build is None else int(r.build.ok), sum(len(g.errors) for g in r.gates),
        j.overall if j is not None else None,
        None if j is None else int(j.passed), 0 if j is None else len(j.issues),
        r.usage.cost_usd, r.minutes, r.started_at.isoformat(),
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
        seen: dict[str, str] = {}
        for ws, rec, rid in iter_runs(runs_dir, on_error=skip_unreadable):
            slug = rid.slug
            try:
                con.execute(f"INSERT INTO runs VALUES ({','.join('?' * 28)})", _run_row(ws, rec, rid))
            except sqlite3.IntegrityError as e:
                # never INSERT OR REPLACE: a silent overwrite is the bug this guards
                raise sqlite3.IntegrityError(
                    f"duplicate run slug {slug!r}: {seen.get(slug, '<unknown>')} and {ws.root}"
                ) from e
            seen[slug] = str(ws.root)
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
    """Per (track, language, generator): n, mean baseline / picked score and their delta, cost."""
    return query(
        db,
        "SELECT track, language, generator, COUNT(*) AS n, "
        "AVG(baseline_score) AS baseline_mean, AVG(picked_score) AS picked_mean, "
        "AVG(picked_score - baseline_score) AS delta_mean, SUM(cost_usd) AS cost_usd, "
        "AVG(n_rounds) AS rounds_mean, SUM(quality_tier = 'A') AS n_tier_a, SUM(quality_tier = 'B') AS n_tier_b "
        "FROM runs GROUP BY track, language, generator ORDER BY track, language",
    )
