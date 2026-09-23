"""Build the gallery index from run roots.

``build_index(roots)`` walks every run directory once, reads ``record.json`` and
turns it into a :class:`~codeverse3d.addons.gallery.model.RunEntry` whose every path is
**run-relative**.  It is deliberately cheap (no image work, no git): ~80 runs
index in well under a second, which is what makes ``--reload`` free.

Robustness is a feature here, not a nicety: a bench writing into these
directories means a record can be absent, empty or half-written at any moment,
and the gallery must still render.  Nothing in this module raises for one bad run.
"""

from __future__ import annotations

import time
from pathlib import Path

from codeverse3d.addons import select
from codeverse3d.addons.dataset.quality import quality_tier
from codeverse3d.addons.dataset.sample import gate_error_summary, telemetry_digest
from codeverse3d.addons.gallery.model import (
    GalleryIndex,
    RootSection,
    RoundRow,
    RunEntry,
    RunLink,
    humanize_view,
)
from codeverse3d.contracts.run import RunId, RunRecord
from codeverse3d.proc import read_json_or_none
from codeverse3d.record.deliverable import round_outputs, texture_shipped_for
from codeverse3d.record.record import (
    RecordError,
    battery_label,
    effective_judgment,
    find_run_dirs,
    is_run_dir,
    load_record,
)
from codeverse3d.workspace import Workspace

#: run roots the CLI defaults to when the user names none
DEFAULT_RUNS = "runs"
DEFAULT_BENCH_GLOB = "bench/out/*/runs"


def default_roots(base: Path | str = ".") -> list[Path]:
    """``runs/`` plus every ``bench/out/*/runs`` that exists, in that order."""
    base = Path(base)
    roots: list[Path] = []
    if (base / DEFAULT_RUNS).is_dir():
        roots.append(base / DEFAULT_RUNS)
    roots += sorted(p for p in base.glob(DEFAULT_BENCH_GLOB) if p.is_dir())
    return roots


def _rel(ws: Workspace, path: Path | str | None) -> str:
    """``path`` (rebased into this workspace) as a run-relative posix string, or ``""``
    when it is outside the run."""
    if not path:
        return ""
    try:
        return ws.rebase(path).resolve().relative_to(ws.root.resolve()).as_posix()
    except (ValueError, OSError):
        return ""


def _first_file(run_dir: Path, *rels: str) -> str:
    for rel in rels:
        if (run_dir / rel).is_file():
            return rel
    return ""


def _round_sheet(ws: Workspace, rec: RunRecord, index: int) -> str:
    rnd = next((r for r in rec.rounds if r.index == index), None)
    if rnd is not None and rnd.renders is not None and rnd.renders.contact_sheet:
        rel = _rel(ws, rnd.renders.contact_sheet)
        if rel and (ws.root / rel).is_file():
            return rel
    return _first_file(ws.root, f"artifacts/renders/r{index:02d}/sheet.png")


def picked_sheet(ws: Workspace, rec: RunRecord, picked: int | None) -> str:
    """Contact sheet of the picked round, else the latest round that has one, else the
    packaged deliverable sheet."""
    if picked is not None:
        rel = _round_sheet(ws, rec, picked)
        if rel:
            return rel
    for rnd in reversed(rec.rounds):
        rel = _round_sheet(ws, rec, rnd.index)
        if rel:
            return rel
    return _first_file(ws.root, "deliverable/sheet.png", "artifacts/frames_sheet.png")


#: view names that make the best single thumbnail, most telling first.  A ¾ view
#: shows silhouette *and* depth; a flat orthographic front hides both.  The ``*_34``
#: names are the pre-D47 rig, kept because the gallery serves stored runs.
HERO_PREFERENCE = ("front_right_high", "back_left_high", "front_right_low",
                   "front_right_34", "back_left_34", "low_front_left", "front")


def hero_view(ws: Workspace, rec: RunRecord, picked: int | None) -> tuple[str, str, int]:
    """``(rel, label, n_views)`` — the ONE image a card should show (from the picked round).

    A card that shows an 8-up contact sheet at 320 px shows eight unreadable
    thumbnails; one 320 px hero view is legible.  Costs a single ``is_file``
    beyond what the record already told us, so the index stays cheap."""
    rnd = next((r for r in rec.rounds if r.index == picked), None)
    if rnd is None or rnd.renders is None or not rnd.renders.views:
        return "", "", 0
    views = [v for v in rnd.renders.views if not v.name.startswith(("pose_", "articulation"))]
    if not views:
        return "", "", 0
    by_name = {v.name: v for v in views}
    chosen = next((by_name[n] for n in HERO_PREFERENCE if n in by_name), views[0])
    rel = _rel(ws, chosen.path)
    if not rel or not (ws.root / rel).is_file():
        return "", "", len(views)
    return rel, humanize_view(chosen.name), len(views)


def _articulation_sheet(ws: Workspace, picked: int | None) -> str:
    order = [picked] if picked is not None else []
    order += [int(p.name[1:]) for p in sorted((ws.artifacts / "renders").glob("r[0-9][0-9]"), reverse=True)
              if p.is_dir()]
    for idx in order:
        rel = f"artifacts/renders/r{idx:02d}/poses/articulation_sheet.png"
        if (ws.root / rel).is_file():
            return rel
    return ""


def entry_links(ws: Workspace, rec: RunRecord | None, picked: int | None) -> list[RunLink]:
    """The working links of a card: workspace, record, code, sheet, GLB, track extras — the
    artifact files of the handed-over round (``deliverable/``), else of the picked round
    (``select.round_file``: ``artifacts/rNN/``, or ``artifacts/`` of a run recorded before rounds
    kept their own, for the round it rebuilt there) — never another round's.  A run with no
    picked round (no record yet, none judged) links whatever ``artifacts/`` holds."""
    run = ws.root
    rnd = next((r for r in rec.rounds if r.index == picked), None) if rec is not None else None
    out = round_outputs(ws, rnd)

    def artifact(name: str, *extra: str) -> str:
        if rnd is None:
            own = [f"artifacts/{name}"]
        else:
            own = [_rel(ws, out / name)] if out is not None else []
        return _first_file(run, f"deliverable/{name}", *own, *extra)

    # the texture pack is a link only when a pass SHIPPED from this round's GLB — a stray file
    # from a rejected pass (or an older harness that leaked one), or another round's pack, is not
    tex = (rec.extra.get("texturing") or {}) if rec is not None else {}
    shipped = texture_shipped_for(ws, rec, out) if rnd is not None and rec is not None else bool(tex.get("shipped"))

    links = [RunLink(label="workspace", rel="", kind="dir"),
             RunLink(label="record.json", rel="record.json")]
    for name in ("spec.json", "plan.json"):
        if (run / name).is_file():
            links.append(RunLink(label=name, rel=name))
    if (run / "src").is_dir():
        links.append(RunLink(label="src/", rel="src", kind="code"))
    sheet = picked_sheet(ws, rec, picked) if rec is not None else ""
    if sheet:
        links.append(RunLink(label="sheet", rel=sheet))
    glb = artifact("object.glb")
    if glb:
        links.append(RunLink(label="glb", rel=glb, kind="viewer"))
    if shipped:
        textured = _first_file(run, "deliverable/object_textured.glb", "artifacts/object_textured.glb")
        if textured:
            links.append(RunLink(label="textured glb", rel=textured, kind="viewer"))
    urdf = artifact("robot.urdf", "src/robot.urdf")
    if urdf:
        links.append(RunLink(label="robot.urdf", rel=urdf))
    art = _articulation_sheet(ws, picked)
    if art:
        links.append(RunLink(label="articulation", rel=art))
    gif = artifact("preview.gif")
    if gif:
        links.append(RunLink(label="preview.gif", rel=gif))
    frames = ("deliverable/frames", f"artifacts/renders/r{picked:02d}") if rnd is not None else ("artifacts/frames",)
    if rnd is not None and out == ws.artifacts:
        frames += ("artifacts/frames",)  # the one round an old run rebuilt there
    frames_rel = next((f for f in frames if any((run / f).glob("f*_t*.png")) or any((run / f).glob("frame_t*.png"))), "")
    if frames_rel:
        links.append(RunLink(label="frames/", rel=frames_rel, kind="dir"))
    for tex_rel in ("deliverable/textures", "artifacts/textures" if shipped else "", "public/textures"):
        if tex_rel and (run / tex_rel).is_dir():
            links.append(RunLink(label="textures/", rel=tex_rel, kind="dir"))
            break
    if (run / "public" / "assets").is_dir():
        links.append(RunLink(label="assets/", rel="public/assets", kind="dir"))
    if (run / "telemetry" / "cost.json").is_file():
        links.append(RunLink(label="cost.json", rel="telemetry/cost.json"))
    return links


def round_rows(ws: Workspace, rec: RunRecord) -> list[RoundRow]:
    rounds = {r.index: r for r in rec.rounds}
    return [RoundRow(**{**row.model_dump(), "commit": row.commit[:12]}, sheet=_round_sheet(ws, rec, row.index),
                     gates={k: v for k, v in gate_error_summary(rounds[row.index]).items() if v})
            for row in select.round_rows(ws.root, record=rec)]


#: complexity axes worth putting on a card / detail page (the index carries the rest)
_CX_AXES = ("part_count", "assembly_depth", "tri_count", "materials",
            "silhouette", "feature_density", "symmetry_groups", "hollowness")


def _complexity(ws: Workspace, rec: RunRecord, picked: int | None) -> tuple[float | None, str, dict[str, float]]:
    """``(index, band, axes)`` of the picked round's artifact (``select.round_complexity_block``).
    A run built before the complexity vector existed has none (``eval/bench/complexity_report.py``
    recomputes those from the GLB)."""
    try:
        block = select.round_complexity_block(ws, next((r for r in rec.rounds if r.index == picked), None))
    except Exception:  # noqa: BLE001 - a card is never worth an exception
        block = None
    if block is None or block.get("index") is None:
        return None, "", {}
    axes = {a: float(block[a]) for a in _CX_AXES if isinstance(block.get(a), (int, float))}
    return float(block["index"]), str(block.get("band") or ""), axes


def entry_from_record(battery: str, ws: Workspace, rec: RunRecord, *, slug: str | None = None) -> RunEntry:
    """A complete card from a parsed record (never raises: every field degrades).

    ``slug`` is the RunId slug the scan minted; without one the directory basename is
    used (correct for flat layouts only — nested battery runs are all named ``run``).
    The card is the round ``addons/select`` picks: its score, sheet, hero and tier."""
    summary = select.summarise(ws.root, record=rec)
    picked = summary.picked_round
    rnd = next((r for r in rec.rounds if r.index == picked), None)
    j = effective_judgment(rnd) if rnd is not None else None
    gates = gate_error_summary(rnd)
    n_err = sum(gates.values())
    minutes = rec.minutes
    digest = telemetry_digest(ws, rec)
    caps = rec.extra.get("captions") or {}
    hero, hero_label, n_views = hero_view(ws, rec, picked)
    plan = rec.plan
    cx_index, cx_band, cx_axes = _complexity(ws, rec, picked)
    return RunEntry(
        battery=battery, slug=slug or ws.root.name, path=str(ws.root), state="ok",
        title=(getattr(plan, "object_name", "") or getattr(plan, "title", "") or "") if plan else "",
        prompt=rec.spec.prompt, track=rec.spec.track.value, language=rec.spec.language.value,
        generator=rec.spec.backends.generator, judge=rec.spec.backends.judge,
        status=summary.stop_reason, error=rec.error, caption=str(caps.get("instruction", "") or ""),
        score=summary.picked_score, baseline_score=summary.baseline_score,
        tier=quality_tier(passed=j.passed if j else None, gate_errors=n_err, score=j.overall if j else None),
        gate_errors=n_err, gate_summary={k: v for k, v in gates.items() if v},
        cost_usd=rec.total_usage.cost_usd, minutes=round(minutes, 1) if minutes is not None else None,
        rounds=len(rec.rounds), picked_round=picked,
        complexity=cx_index, complexity_band=cx_band, complexity_axes=cx_axes,
        sheet=picked_sheet(ws, rec, picked), hero=hero, hero_label=hero_label, n_views=n_views,
        links=entry_links(ws, rec, picked), round_rows=round_rows(ws, rec),
        cost_by_stage={k: round(v, 4) for k, v in (digest.get("by_stage") or {}).items()},
        models=dict(digest.get("models") or {}),
    )


def _spec_fields(run_dir: Path) -> dict[str, str]:
    """Prompt / track / language straight from spec.json — a run with no record yet
    still deserves a readable card (and spec.json can be half-written too)."""
    spec = read_json_or_none(run_dir / "spec.json") or {}
    return {k: str(spec.get(k) or "") for k in ("prompt", "track", "language")}


def entry_for_dir(battery: str, run_dir: Path, *, slug: str | None = None) -> RunEntry:
    """One run directory → an entry, whatever state it is in.

    ``slug`` is the RunId slug the scan minted (default: the directory basename,
    which is only unique in flat layouts)."""
    ws = Workspace(run_dir)
    slug = slug or run_dir.name
    fields = _spec_fields(run_dir)
    if not (run_dir / "record.json").is_file():
        return RunEntry(battery=battery, slug=slug, path=str(run_dir), state="pending",
                        error="no record.json yet (run in progress or never finished)",
                        links=entry_links(ws, None, None), **fields)
    try:
        rec = load_record(ws)
    except (RecordError, OSError, ValueError) as e:
        return RunEntry(battery=battery, slug=slug, path=str(run_dir), state="broken",
                        error=str(e)[:400], links=entry_links(ws, None, None), **fields)
    try:
        return entry_from_record(battery, ws, rec, slug=slug)
    except Exception as e:  # noqa: BLE001 - one weird record must not break the page
        return RunEntry(battery=battery, slug=slug, path=str(run_dir), state="broken",
                        error=f"{type(e).__name__}: {e}"[:400], links=entry_links(ws, None, None), **fields)


def _pending_direct_children(root: Path, found: set[Path]) -> list[Path]:
    """Direct children with a spec.json but no record.json yet — a bench mid-write
    still deserves a readable (pending) card.  Only DIRECT children qualify: a run is
    otherwise gated on record.json exactly like ``record.record.is_run_dir``, because
    accepting spec-only directories during descent counted every nested ``eval/``
    judge workspace of a battery cell as a phantom run."""
    if not root.is_dir():
        return []
    return [d for d in sorted(root.iterdir())
            if d.is_dir() and d not in found
            and (d / "spec.json").is_file() and not (d / "record.json").is_file()]


def scan_root(root: Path, label: str | None = None) -> RootSection:
    """Runs under ``root``.  Uses ``find_run_dirs`` rather than a one-level
    ``iterdir()``: a compare_backends or ab_plan battery directory holds its runs four
    and five levels down, so pointing the gallery at one built a page "of 0 runs" and
    exited 0 — the silent-empty failure, on a directory full of real runs.

    Every entry is named by its :class:`~codeverse3d.contracts.run.RunId` slug, so two
    nested runs whose directories are both called ``run`` stay two entries.  A slug
    that still repeats within the root gets ``#2``/``#3`` — the same precedent as
    duplicate section labels in :func:`build_index`."""
    root = Path(root)
    label = label or battery_label(root)
    found = find_run_dirs(root, predicate=is_run_dir)
    dirs = sorted({*found, *_pending_direct_children(root, set(found))})
    used: dict[str, int] = {}
    entries: list[RunEntry] = []
    for d in dirs:
        slug = RunId(battery=label, rel=d.relative_to(root).as_posix()).slug
        used[slug] = used.get(slug, 0) + 1
        if used[slug] > 1:
            slug = f"{slug}#{used[slug]}"
        entries.append(entry_for_dir(label, d, slug=slug))
    return RootSection(label=label, path=str(root.resolve()), entries=entries)


def build_index(roots: list[Path] | list[str]) -> GalleryIndex:
    """Scan every root; duplicate labels get ``label#2`` so URLs stay unique."""
    t0 = time.perf_counter()
    sections: list[RootSection] = []
    used: dict[str, int] = {}
    for root in roots:
        root = Path(root)
        base = battery_label(root)
        used[base] = used.get(base, 0) + 1
        label = base if used[base] == 1 else f"{base}#{used[base]}"
        sections.append(scan_root(root, label))
    return GalleryIndex(sections=sections, build_ms=int((time.perf_counter() - t0) * 1000),
                        roots=[str(Path(r).resolve()) for r in roots])
