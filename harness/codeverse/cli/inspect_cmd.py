"""``3dcv status`` / ``3dcv render`` / ``3dcv judge`` — inspect one existing run.

Owns the three read-mostly commands on a finished (or in-flight) workspace: the
``status`` summary (spec / run_state / record / candidates / recent events), the
``render`` command with its round-label refusal (`_render_round_or_refuse`) and the
graphics-track frame copy, and the ``judge`` re-judge that writes
``artifacts/judge/rNN_cli.json``.  Its sibling ``cli/main.py`` owns the typer app,
``make`` / ``resume`` (spec building + track dispatch), ``mcp`` and the registration
of every command — including these three, which it registers with ``app.command``
like ``layout_cmd.py``'s ``show`` / ``migrate-runs``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from codeverse.cli import _common as C
from codeverse.cli._fmt import console, kv_table, print_record_summary, warn
from codeverse.config import get_settings
from codeverse.contracts.common import Track
from codeverse.contracts.spec import Spec
from codeverse.proc import read_json_or_none

RunsDirOpt = Annotated[
    Path | None, typer.Option("--runs-dir", help="runs root (default: settings.runs_dir)")
]


# --------------------------------------------------------------------------- status
def status(
    slug: str,
    runs_dir: RunsDirOpt = None,
    events: Annotated[int, typer.Option("--events", help="tail N events")] = 8,
) -> None:
    """Show spec / run_state / record / recent events of a run."""
    from codeverse.events import EventLog
    from codeverse.flywheel.record import RecordError, load_record

    ws = C.open_workspace(slug, runs_dir)
    spec = C.load_spec(ws)
    rows = {
        "workspace": ws.root,
        "track": spec.track.value,
        "language": spec.language.value,
        "generator": spec.backends.generator,
        "judge": spec.backends.judge,
        "prompt": spec.prompt,
    }
    if ws.state_path.is_file():
        try:
            state = json.loads(ws.state_path.read_text())
            rows["run_state"] = ", ".join(
                f"{k}={v}" for k, v in state.items() if not isinstance(v, (dict, list))
            )[:300]
        except ValueError:
            rows["run_state"] = "(unreadable)"
    console.print(kv_table("status", rows))
    record = None
    try:
        record = load_record(ws)
        print_record_summary(record, ws.root)
    except RecordError as e:
        warn(f"no record yet ({e})")
    _print_candidates(ws)
    if record is not None and record.extra.get("texturing"):
        t = record.extra["texturing"]
        console.print(
            kv_table(
                "texturing",
                {
                    "shipped": t.get("shipped"),
                    "delta": t.get("delta"),
                    "reason": t.get("reason", ""),
                    "textures": t.get("n_textures", len(t.get("textures", {}) or {})),
                    "glb": t.get("glb_textured", "") or "-",
                },
            )
        )
    console.print(
        f"[dim]`3dcv show {ws.root.name}` for the DELIVERABLE / QUALITY EVIDENCE / COST & SETTINGS view[/dim]"
    )
    evs = EventLog(ws.events_path).read()
    if evs:
        console.print(f"[dim]last {min(events, len(evs))} of {len(evs)} events:[/dim]")
        for ev in evs[-events:]:
            extra = {k: v for k, v in ev.items() if k not in ("t", "event")}
            console.print(
                f"  {_event_time(ev)}  {ev.get('event', '?'):<14} {json.dumps(extra, default=str)[:160]}"
            )


def _print_candidates(ws) -> None:
    """Best-of-N candidate table + pairwise verdict (rounds/candidates.json), when present."""
    p = ws.root / "rounds" / "candidates.json"
    if not p.is_file():
        return
    try:
        data = json.loads(p.read_text())
    except ValueError:
        return
    cands = data.get("candidates") or []
    rows: dict[str, str] = {"n": str(data.get("n", len(cands)))}
    for c in cands:
        mark = " *" if c.get("index") == data.get("selected") else ""
        score = c.get("score")
        rows[f"{c.get('label', c.get('index'))}{mark}"] = (
            f"score {score if score is None else round(score, 3)}  build_ok={c.get('build_ok')}"
        )
    pw = data.get("pairwise")
    if pw:
        rows["pairwise"] = (
            f"{pw.get('a')} vs {pw.get('b')} → {pw.get('winner')} (confidence {pw.get('confidence')})"
        )
    console.print(kv_table("candidates (best-of-N, * = selected)", rows))


def _event_time(ev: dict) -> str:
    from datetime import UTC, datetime

    try:
        return datetime.fromtimestamp(float(ev["t"]), UTC).strftime("%H:%M:%S")
    except (KeyError, TypeError, ValueError, OSError):
        return "--:--:--"


# --------------------------------------------------------------------------- render / judge
def render(
    slug: str,
    round_index: Annotated[int | None, typer.Option("--round")] = None,
    mode: Annotated[
        str, typer.Option("--mode", help="shaded | wire | normals | clay | silhouette")
    ] = "shaded",
    out: Annotated[Path | None, typer.Option("--out")] = None,
    width: Annotated[
        int | None,
        typer.Option("--width", min=1, help="default: render.width (scene: render.scene_width)"),
    ] = None,
    height: Annotated[
        int | None,
        typer.Option("--height", min=1, help="default: render.height (scene: render.scene_height)"),
    ] = None,
    runs_dir: RunsDirOpt = None,
) -> None:
    """Render the current artifact (object.glb or the scene) with the canonical rig."""
    ws = C.open_workspace(slug, runs_dir)
    spec = C.load_spec(ws)
    idx = _render_round_or_refuse(ws, round_index)
    out_dir = out or ws.renders_dir(idx) / ("cli" if mode == "shaded" else f"cli_{mode}")
    if spec.track is Track.GRAPHICS:
        _render_graphics(ws, spec, out_dir)
        return
    # the size settings are honoured by the in-run renders (tracks/static_object.py,
    # tracks/scene.py) and were silently dropped by the one command whose whole job is
    # rendering, so a CLI render did not match the one the judge saw
    r = get_settings().render
    if spec.track is Track.SCENE:
        render_scene = C.lazy("codeverse.spatial.render_scene", "render_scene")
        rs = render_scene(
            ws, out_dir, cameras=None, width=width or r.scene_width, height=height or r.scene_height
        )
    else:
        glb = ws.artifacts / "object.glb"
        if not glb.is_file():
            raise C.CliError(f"no artifact to render: {glb} (run a build first)")
        render_glb = C.lazy("codeverse.spatial.render", "render_glb")
        rs = render_glb(glb, out_dir, mode=mode, width=width or r.width, height=height or r.height)
    console.print(
        kv_table(
            "renders",
            {
                "views": len(rs.views),
                "sheet": rs.contact_sheet,
                "dir": out_dir,
                "renderer": rs.renderer,
                "ms": rs.duration_ms,
            },
        )
    )


def judge(
    slug: str,
    round_index: Annotated[int | None, typer.Option("--round")] = None,
    rubric: Annotated[
        str | None, typer.Option("--rubric", help="default: the rubric the round was judged with")
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="judge chat model id")] = None,
    n: Annotated[int, typer.Option("--n", min=1)] = 1,
    runs_dir: RunsDirOpt = None,
) -> None:
    """Re-judge a round with the SAME inputs as the in-run judge (renders + measurement +
    gates + acceptance + plan digest + previous verdict + stored clay views); writes
    artifacts/judge/rNN_cli.json."""
    from codeverse.cli import _judge as J
    from codeverse.flywheel.record import load_record

    ws = C.open_workspace(slug, runs_dir)
    rec = load_record(ws)
    idx = (
        round_index
        if round_index is not None
        else (rec.best_round if rec.best_round is not None else _latest_round(ws))
    )
    rnd = J.load_round(ws, rec, idx)
    if rnd is None or rnd.renders is None or not rnd.renders.views:
        raise C.CliError(f"round {idx} has no renders (rounds/r{idx:02d}.json / record.json)")
    rubric_name = J.rubric_for(rec, rnd, rubric)
    inp = J.build_judge_input(ws, rec, rnd)
    judge_obj = J.make_judge(rec, rubric_name, model or rec.spec.backends.judge, n)
    n_images = J.count_prompt_images(inp, rubric_name)
    from codeverse.cost.instrument import run_ledger

    try:
        # a re-judge joins the run's ledger when it has one; otherwise the per-process log
        # (a ledger holding only this verdict would be read as the whole run's cost)
        with run_ledger(ws.root, run=ws.root.name, create=False):
            verdict = judge_obj.judge(inp)
    except ValueError as e:  # e.g. a measured rubric fed to a judge that computes nothing
        raise C.CliError(f"judge failed: {e}") from e
    except Exception as e:
        ReferenceJudgeError = C.lazy("codeverse.judges.reference", "ReferenceJudgeError")
        if isinstance(e, ReferenceJudgeError):
            raise C.CliError(f"judge failed: {e}") from e
        raise
    ws.write_json(ws.judge_path(idx, "_cli"), verdict)
    console.print(
        kv_table(
            "judgment",
            {
                "rubric": verdict.rubric,
                "round": idx,
                "overall": f"{verdict.overall:.3f}",
                "passed": verdict.passed,
                "std": verdict.score_std,
                "prompt images": n_images if n_images is not None else "?",
                "stored score": f"{rnd.judgment.overall:.3f}" if rnd.judgment else "-",
                "summary": verdict.summary,
                "cost": f"${verdict.usage.cost_usd:.4f}",
            },
        )
    )
    for it in verdict.improvement_plan:
        console.print(f"  [{it.priority}] {it.target}: {it.instruction}")


def _render_graphics(ws, spec: Spec, out_dir: Path) -> None:
    """Graphics runs have no GLB: regenerate the judged frames + sheet via the runtime."""
    import shutil

    get_runtime = C.lazy("codeverse.languages", "get_runtime")
    br = get_runtime(spec.language).build(ws)
    if not br.ok:
        raise C.CliError(f"graphics build failed: {br.error_type}: {br.error_message}")
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = Path(br.extra_paths.get("frames") or (ws.artifacts / "frames"))
    n = 0
    for p in sorted(frames_dir.glob("*.png")) if frames_dir.is_dir() else []:
        shutil.copy2(p, out_dir / p.name)
        n += 1
    sheet = br.extra_paths.get("sheet") or ""
    if sheet and Path(sheet).is_file():
        shutil.copy2(sheet, out_dir / "sheet.png")
        sheet = str(out_dir / "sheet.png")
    renderer = br.census.get("renderer", "moderngl") if isinstance(br.census, dict) else "moderngl"
    console.print(
        kv_table(
            "renders",
            {
                "views": n,
                "sheet": sheet or "-",
                "dir": out_dir,
                "renderer": renderer,
                "ms": br.duration_ms,
            },
        )
    )


def _render_round_or_refuse(ws, round_index: int | None) -> int:
    """Which round this render is labelled as — refusing when the label would lie.

    ``render`` renders the WORKING TREE, which sits at the last round the run wrote.
    ``--round`` only chose the output folder, so `3dcv render X --round 3` wrote
    r03-labelled images of round 4's code, and with no flag at all a run whose best round
    was not its last silently published its worst one.

    Measured 2026-08-25 on tsr_scn_neon_alley: judge by round 0.338 / 0.375 / 0.529 /
    0.632 / 0.000 — round 4 rendered completely blank, all eight tiles empty.  The harness
    correctly kept r3 and the deliverable is correct, but the tree was left at r4, so a
    plain `3dcv render` re-rendered eight blank frames and was very nearly shipped.

    So: no flag renders the tree only when the tree IS the best round; otherwise this
    refuses and points at ``deliverable/``, which already holds the best round's code,
    sheet and a manifest naming the commit.  Rendering a round other than the tree's would
    need that round checked out, which this command does not do — hence a refusal rather
    than a mislabelled image.
    """
    tree = _latest_round(ws)
    best = _best_round_of_record(ws)
    if round_index is not None:
        if round_index != tree:
            raise C.CliError(
                f"cannot render round {round_index}: `render` renders the working tree, which is at "
                f"round {tree}, and --round only labels the output folder.  The best round's code, "
                f"renders and manifest are already packaged in {ws.deliverable} — read "
                f"{ws.deliverable / 'sheet.png'}, or `3dcv resume {ws.root.name}` to keep iterating.",
                code=2)
        return round_index
    if best is not None and best != tree:
        raise C.CliError(
            f"refusing to render: this run's BEST round is r{best} but the working tree is at "
            f"r{tree}, so this would render the wrong round — and r{tree} may be why it was not "
            f"chosen.  Read {ws.deliverable / 'sheet.png'} (the packaged best round), or pass "
            f"--round {tree} to render the tree anyway.",
            code=2)
    return tree


def _best_round_of_record(ws) -> int | None:
    """``record.best_round``, or None when there is no readable record yet."""
    rec = read_json_or_none(ws.record_path)
    if rec is None:
        return None
    best = rec.get("best_round")
    return best if isinstance(best, int) else None


def _latest_round(ws) -> int:
    rdir = ws.artifacts / "renders"
    idxs = (
        sorted(int(p.name[1:]) for p in rdir.glob("r[0-9][0-9]") if p.is_dir())
        if rdir.is_dir()
        else []
    )
    return idxs[-1] if idxs else 0


