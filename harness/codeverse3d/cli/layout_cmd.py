"""``3dcode show`` — THE single-run view, in four sections (docs/RUN_LAYOUT.md):

* **STATUS** — where the run stands: the run, the rounds, the process holding the run
  lock, run_state, best-of-N candidates, a texture pass, the latest events.  It needs no
  record.json, so it works mid-run; ``3dcode status`` is ``show --section status``;
* **DELIVERABLE** — what the run produced and where to find it;
* **QUALITY EVIDENCE** — why we believe it is good (score, rubric, gates, acceptance);
* **COST & SETTINGS** — tokens and dollars per stage, model ids, thinking levels,
  rubric hash, budget vs spent, wall clock.

Works on the new layout and on runs written before it: the cost / settings block
is computed on the fly when ``telemetry/`` is not there yet.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer
from rich.table import Table

from codeverse3d.cli import _common as C
from codeverse3d.cli._common import (
    RunsDirOpt,
    console,
    fmt_score,
    fmt_usd,
    kv_table,
    print_record_summary,
    warn,
)
from codeverse3d.contracts.run import (
    CostSummary,
    DeliverableFile,
    RunDeliverable,
    RunRecord,
    SettingsSnapshot,
)
from codeverse3d.workspace import Workspace


def _human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024.0
    return f"{size:,.1f} GB"


def _section(title: str) -> None:
    console.rule(f"[bold]{title}[/bold]", align="left")


# --------------------------------------------------------------------------- status
def print_status(ws: Workspace, record: RunRecord | None, n_events: int) -> None:
    """Where the run stands.  Needs no record.json: a run in flight shows its spec, its lock
    holder, run_state and the latest events."""
    from codeverse3d.proc import EventLog, holder_of, read_json_or_none

    _section("STATUS — where the run stands")
    if record is not None:
        print_record_summary(record, ws.root)
    else:
        spec = C.load_spec(ws)
        console.print(kv_table("run (no record.json yet)", {
            "prompt": spec.prompt, "track": spec.track.value, "language": spec.language.value,
            "generator": spec.backends.generator, "judge": spec.backends.judge, "workspace": ws.root}))
    state: dict[str, Any] = {}
    if (held := holder_of(ws.root)) is not None:  # kill THAT pid, never `pkill -f 3dcode`
        state["RUNNING NOW"] = f"pid {held.get('pid', '?')} ({held.get('what') or '3dcode'})"
    if ws.state_path.is_file():
        raw = read_json_or_none(ws.state_path)
        state["run_state"] = "(unreadable)" if raw is None else ", ".join(
            f"{k}={v}" for k, v in raw.items() if not isinstance(v, (dict, list)))[:300]
    if state:
        console.print(kv_table("state", state))
    cands = read_json_or_none(ws.root / "rounds" / "candidates.json")
    if cands is not None:
        rows: dict[str, str] = {"n": str(cands.get("n", len(cands.get("candidates") or [])))}
        for c in cands.get("candidates") or []:
            score = c.get("score")
            rows[f"{c.get('label', c.get('index'))}{' *' if c.get('index') == cands.get('selected') else ''}"] = (
                f"score {score if score is None else round(score, 3)}  build_ok={c.get('build_ok')}")
        if pw := cands.get("pairwise"):  # a record from before 2026-09-22 (in-loop pairwise)
            rows["pairwise"] = f"{pw.get('a')} vs {pw.get('b')} → {pw.get('winner')} (confidence {pw.get('confidence')})"
        console.print(kv_table("candidates (best-of-N, * = selected)", rows))
    if record is not None and (t := record.extra.get("texturing")):
        console.print(kv_table("texturing", {
            "shipped": t.get("shipped"), "delta": t.get("delta"), "reason": t.get("reason", ""),
            "textures": t.get("n_textures", len(t.get("textures", {}) or {})), "glb": t.get("glb_textured", "") or "-"}))
    evs = EventLog(ws.events_path).read()
    if evs:
        console.print(f"[dim]last {min(n_events, len(evs))} of {len(evs)} events:[/dim]")
        for ev in evs[-n_events:]:
            extra = {k: v for k, v in ev.items() if k not in ("t", "event")}
            console.print(f"  {_event_time(ev)}  {ev.get('event', '?'):<14} {json.dumps(extra, default=str)[:160]}")


def _event_time(ev: dict) -> str:
    from datetime import UTC, datetime

    try:
        return datetime.fromtimestamp(float(ev["t"]), UTC).strftime("%H:%M:%S")
    except (KeyError, TypeError, ValueError, OSError):
        return "--:--:--"


# --------------------------------------------------------------------------- (a) deliverable
MAX_ROWS_PER_ROLE = 6


def _deliverable_table(d: RunDeliverable) -> Table:
    """One row per file, except roles with many files (frame stacks, link meshes,
    a multi-file source tree) which collapse into a single counted row."""
    t = Table(title="files (deliverable/)", show_lines=False)
    for col in ("role", "path", "size"):
        t.add_column(col, justify="right" if col == "size" else "left")
    by_role: dict[str, list[DeliverableFile]] = {}
    for f in sorted(d.files, key=lambda f: (f.role, f.path)):
        by_role.setdefault(f.role, []).append(f)
    for role, files in by_role.items():
        if len(files) <= MAX_ROWS_PER_ROLE:
            for f in files:
                t.add_row(role, f.path, _human_bytes(f.bytes))
            continue
        dirs = {f.path.rsplit("/", 1)[0] for f in files}
        head = f"{dirs.pop()}/  " if len(dirs) == 1 else ""
        t.add_row(role, f"{head}({len(files)} files)", _human_bytes(sum(f.bytes for f in files)))
    return t


def _legacy_deliverable_rows(ws: Workspace) -> dict[str, Any]:
    """Old layout: point at the physical artifacts instead of ``deliverable/``."""
    rows: dict[str, Any] = {}
    for name in ("object.glb", "object.stl", "object.step", "robot.urdf", "object_textured.glb", "preview.gif"):
        p = ws.artifacts / name
        if p.is_file():
            rows[name] = f"{p}  ({_human_bytes(p.stat().st_size)})"
    if ws.src.is_dir():
        rows["src/"] = str(ws.src)
    return rows


def print_deliverable(ws: Workspace, record: RunRecord) -> None:
    from codeverse3d.addons import select
    from codeverse3d.record.deliverable import load_deliverable

    _section("DELIVERABLE — what the run produced")
    s = select.summarise(ws.root, record=record)
    head: dict[str, Any] = {"picked round": f"{s.picked_round} (by {s.method})", "score": fmt_score(s.picked_score)}
    d = load_deliverable(ws)
    if d is not None:
        head["code"] = f"{d.entry or 'deliverable/src/'} @ {d.commit[:12] or '-'} ({d.code_source})"
        head["total"] = f"{len(d.files)} files, {_human_bytes(d.total_bytes)}"
    console.print(kv_table("deliverable", head))
    if d is not None:
        console.print(_deliverable_table(d))
        for name, why in d.skipped.items():
            warn(f"not packaged: {name} — {why}")
    else:
        console.print(kv_table("artifacts (no deliverable/ yet — `3dcode pick` packages a round)",
                               _legacy_deliverable_rows(ws)))


# --------------------------------------------------------------------------- (b) evidence
def print_evidence(ws: Workspace, record: RunRecord) -> None:
    from codeverse3d.addons import select
    from codeverse3d.addons.dataset.sample import gate_error_summary
    from codeverse3d.record.record import effective_judgment

    _section("QUALITY EVIDENCE — why we believe it")
    s = select.summarise(ws.root, record=record)
    rnd = next((r for r in record.rounds if r.index == s.round), None)   # the pick, else select.fallback_round
    j = effective_judgment(rnd) if rnd is not None else None
    gates = gate_error_summary(rnd)
    rows: dict[str, Any] = {
        "baseline → picked": f"{fmt_score(s.baseline_score)} → {fmt_score(s.picked_score)}",
        "rubric": (j.rubric if j else "") or str(record.extra.get("rubric") or "-"),
        "judge on that round": "-" if j is None else ("pass" if j.passed else "fail"),
        "gate errors": f"{sum(gates.values())} ({', '.join(f'{g}:{n}' for g, n in gates.items() if n) or 'none'})",
    }
    if j is not None:
        acc = j.acceptance_results or {}
        rows["acceptance"] = f"{sum(1 for v in acc.values() if v)}/{len(acc)} met" if acc else "-"
        rows["judge summary"] = j.summary[:300]
    if rnd is not None and rnd.renders is not None and rnd.renders.contact_sheet:
        # rebase, never print the stored string: record.json holds the ABSOLUTE path the
        # renderer wrote, so an archived / rsynced / moved run made `3dcode show` print a
        # sheet under the ORIGINAL root — a path that is not there, next to an object.glb
        # that resolved correctly (it is recomputed from the workspace), which is what
        # made the breakage silent and partial.  ``_fmt`` does the same for `3dcode status`.
        rows["contact sheet"] = ws.rebase(rnd.renders.contact_sheet)
    if rnd is not None and rnd.measurement is not None:
        mm = rnd.measurement
        rows["measured"] = (f"extents {tuple(round(v, 3) for v in mm.extents)} m · {mm.tri_count} tris · "
                            f"{mm.n_meshes} meshes · ground gap {mm.ground_gap_m:.3f} m")
    rows["evidence dir"] = f"{ws.artifacts} (renders/ gates/ judge/)"
    console.print(kv_table("evidence", rows))
    if j is not None and j.issues:
        for issue in j.issues[:5]:
            console.print(f"  [yellow]{issue.severity}[/yellow] {issue.target}: {issue.detail[:160]}")


# --------------------------------------------------------------------------- (c) cost & settings
def _stage_table(cost: CostSummary) -> Table:
    t = Table(title="cost by stage", show_lines=False)
    for col in ("stage", "calls", "in tok", "out tok", "cached", "USD", "% run", "secs"):
        t.add_column(col, justify="left" if col == "stage" else "right")
    total = cost.total_usd or 1.0
    for s in cost.by_stage:
        t.add_row(s.stage, str(s.calls), f"{s.input_tokens:,}", f"{s.output_tokens:,}", f"{s.cached_tokens:,}",
                  fmt_usd(s.cost_usd), f"{100.0 * s.cost_usd / total:.0f}%", f"{s.seconds:.0f}")
    return t


def _roles_table(settings: SettingsSnapshot) -> Table:
    t = Table(title="models per role", show_lines=False)
    for col in ("role", "model", "thinking", "temp", "samples", "source"):
        t.add_column(col)
    for r in settings.roles:
        t.add_row(r.role, r.model, r.thinking or "-", "-" if r.temperature is None else f"{r.temperature:g}",
                  "-" if r.n_samples is None else str(r.n_samples), r.source)
    return t


def print_cost_and_settings(ws: Workspace, record: RunRecord) -> None:
    from codeverse3d.record.telemetry import build_telemetry, load_telemetry

    _section("COST & SETTINGS — token price and the key step settings")
    tele = load_telemetry(ws, record)
    computed = False
    if tele is None or tele.cost is None:
        tele = build_telemetry(ws, record, write=False)  # old layout: compute, do not write
        computed = True
    cost, settings = tele.cost, tele.settings
    if cost is not None:
        u = cost.tokens
        console.print(kv_table("cost", {
            "spent": f"{fmt_usd(cost.total_usd)} (the ledger, list price)",
            "minutes": ("-" if record.minutes is None else f"{record.minutes:.1f}")
                       + f" (the clock's ceiling: {cost.max_minutes:.0f} min)",
            "tokens": f"in {u.input_tokens:,} (cached {u.cached_tokens:,}) · out {u.output_tokens:,} · "
                      f"thoughts {u.thoughts_tokens:,} · {u.tool_calls} tool calls",
            "model calls": cost.n_calls,
            "by role": " · ".join(f"{r} {fmt_usd(v)}" for r, v in sorted(cost.by_role.items(), key=lambda kv: -kv[1])) or "-",
            "by model": " · ".join(f"{m or '(unknown)'} {fmt_usd(v)}" for m, v in sorted(cost.by_model.items(), key=lambda kv: -kv[1])),
            "rounds": " · ".join(f"r{r['index']} {r['kind']} {fmt_usd(r['cost_usd'])}" for r in cost.by_round),
        }))
        console.print(_stage_table(cost))
    if settings is not None:
        console.print(_roles_table(settings))
        console.print(kv_table("settings", {
            "budget": " · ".join(f"{k}={v}" for k, v in settings.budget.items()),
            "candidates": settings.candidates or 1,
            "texture pass": settings.texture,
            "seed": settings.seed,
            "rubric": f"{settings.rubric} (hash {settings.rubric_hash or '-'})",
            "prompt hashes": " · ".join(f"{k}={v}" for k, v in settings.prompt_hashes.items()) or "-",
            "tools": " · ".join(f"{k}={v}" for k, v in settings.tool_versions.items() if v and k != "platform"),
            "harness": f"{settings.harness_version} @ {settings.harness_git_sha[:12] or 'no-sha'}",
            "key pool": settings.key_pool_size,
            "price table": settings.price_table_version or "-",
            "render": " · ".join(f"{k}={v}" for k, v in settings.render.items()),
        }))
    if computed:
        console.print("[dim]computed on the fly (no telemetry/ in this run)[/dim]")
    else:
        console.print(f"[dim]telemetry: {ws.cost_path} · {ws.telemetry / 'cost.jsonl'} · {ws.settings_path}[/dim]")


# --------------------------------------------------------------------------- command
SECTIONS = ("status", "deliverable", "evidence", "cost")
EventsOpt = Annotated[int, typer.Option("--events", help="tail N events (the status section)")]


def show(
    slug: str,
    runs_dir: RunsDirOpt = None,
    section: Annotated[str, typer.Option("--section", help="all | status | deliverable | evidence | cost")] = "all",
    events: EventsOpt = 8,
) -> None:
    """One run in four sections: STATUS / DELIVERABLE / QUALITY EVIDENCE / COST & SETTINGS."""
    from codeverse3d.record.record import RecordError, load_record

    ws = C.open_workspace(slug, runs_dir)
    if section not in ("all", *SECTIONS):
        raise C.CliError(f"unknown --section {section!r} (all | {' | '.join(SECTIONS)})")
    try:
        record: RunRecord | None = load_record(ws)
    except RecordError as e:
        if section not in ("all", "status"):
            raise C.CliError(f"{e} (`3dcode status {slug}` shows where the run stands)") from e
        record, missing = None, str(e)
    if section in ("all", "status"):
        print_status(ws, record, events)
    if record is None:
        if section == "all":
            warn(f"{missing}: DELIVERABLE / QUALITY EVIDENCE / COST & SETTINGS need the record")
        return
    if section in ("all", "deliverable"):
        print_deliverable(ws, record)
    if section in ("all", "evidence"):
        print_evidence(ws, record)
    if section in ("all", "cost"):
        print_cost_and_settings(ws, record)


def status(slug: str, runs_dir: RunsDirOpt = None, events: EventsOpt = 8) -> None:
    """Where a run stands, mid-run too: `3dcode show <slug> --section status`."""
    show(slug, runs_dir, section="status", events=events)
