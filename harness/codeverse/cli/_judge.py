"""``3dcv judge`` implementation: re-judge a round with the SAME inputs the
in-run judge saw.

The round's renders / measurement / gates come from ``rounds/rNN.json``
(fallback: the round inside record.json), the acceptance checklist and plan
digest from ``plan.json``, ``previous`` is the preceding round's (non-degraded)
verdict, the track-specific ``extra_context`` is rebuilt from the stored gates
/ frame metrics, and the clay geometry views rendered for the in-run judge
(``renders/rNN/clay/``) are reattached when present.  The judge class matches
the in-run choice: ``ReferenceJudge`` when the rubric has measured criteria or
the spec carries reference images, ``VlmJudge`` otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codeverse.cli import _common as C
from codeverse.contracts.artifacts import BuildResult, RenderSet, RenderView
from codeverse.contracts.common import TRACK_INFO
from codeverse.contracts.plan import AcceptanceItem
from codeverse.contracts.run import RoundRecord, RunRecord
from codeverse.workspace import Workspace


def load_round(ws: Workspace, rec: RunRecord, index: int) -> RoundRecord | None:
    """rounds/rNN.json (written by the round loop) beats the copy in record.json."""
    p = ws.root / "rounds" / f"r{index:02d}.json"
    if p.is_file():
        try:
            return RoundRecord.model_validate_json(p.read_text())
        except Exception:  # noqa: BLE001 — fall through to the record copy
            pass
    return next((r for r in rec.rounds if r.index == index), None)


def rubric_for(rec: RunRecord, rnd: RoundRecord, override: str | None) -> str:
    """--rubric > the rubric the round was judged with > the track default (TRACK_INFO)."""
    if override:
        return override
    if rnd.judgment is not None and rnd.judgment.rubric:
        return rnd.judgment.rubric
    info = TRACK_INFO.get(rec.spec.track)
    return info.rubric if info is not None else "static_object_v1"


def plan_summary_for(ws: Workspace) -> str:
    """Track-agnostic plan digest from plan.json (same fields the tracks summarise)."""
    if not ws.plan_path.is_file():
        return ""
    try:
        data = json.loads(ws.plan_path.read_text())
    except ValueError:
        return ""
    plan_digest = C.lazy("codeverse.judges.calibration", "plan_digest")
    return plan_digest(data)


def previous_judgment(ws: Workspace, rec: RunRecord, index: int) -> Any:
    """The preceding round's verdict (skipping degraded ones), like the round loop."""
    from codeverse.flywheel.record import effective_judgment

    for i in range(index - 1, -1, -1):
        rnd = load_round(ws, rec, i)
        if rnd is None:
            continue
        j = effective_judgment(rnd)
        if j is not None:
            return j
    return None


def extra_context_for(ws: Workspace, rec: RunRecord, rnd: RoundRecord) -> str:
    """The track pipeline's OWN ``judge_context`` over the stored artifacts —
    ``judge_context(ws, plan, round_index, build, gates)`` needs no run context,
    so the CLI never restates the per-track formatting."""
    get_track = C.lazy("codeverse.tracks", "get_track")
    build = rnd.build if rnd.build is not None else BuildResult(ok=False, language=rec.spec.language.value)
    try:
        return get_track(rec.spec.track).make_pipeline().judge_context(ws, rec.plan, rnd.index, build, list(rnd.gates))
    except Exception as e:  # noqa: BLE001 — extra context is optional judge input
        return f"(judge context unavailable: {type(e).__name__}: {e})"


def clay_geometry_views(ws: Workspace, index: int) -> RenderSet | None:
    """The clay views the in-run judge saw (``renders/rNN/clay/``), when present."""
    d = ws.renders_dir(index) / "clay"
    if not d.is_dir():
        return None
    views = [RenderView(name=p.stem.removeprefix("view_"), path=str(p), mode="clay")
             for p in sorted(d.glob("view_*.png"))]
    return RenderSet(views=views, renderer="stored") if views else None


def resolve_paths(ws: Workspace, rs: RenderSet | None) -> RenderSet | None:
    """Round records may store workspace-relative render paths; make them absolute."""
    if rs is None:
        return None
    fixed = []
    for v in rs.views:
        p = Path(v.path)
        fixed.append(v if p.is_absolute() else v.model_copy(update={"path": str(ws.root / p)}))
    sheet = rs.contact_sheet
    if sheet and not Path(sheet).is_absolute():
        sheet = str(ws.root / sheet)
    out_dir = rs.out_dir
    if out_dir and not Path(out_dir).is_absolute():
        out_dir = str(ws.root / out_dir)
    return rs.model_copy(update={"views": fixed, "contact_sheet": sheet, "out_dir": out_dir})


def judged_subset(rs: RenderSet | None) -> RenderSet | None:
    """The views the in-run judge actually saw: the per-view ``judge`` flags
    stamped at render time; legacy rounds (no flags) keep every stored view."""
    if rs is None or not any(v.judge is not None for v in rs.views):
        return rs
    return rs.model_copy(update={"views": [v for v in rs.views if v.judge]})


def build_judge_input(ws: Workspace, rec: RunRecord, rnd: RoundRecord) -> Any:
    JudgeInput = C.lazy("codeverse.judges.base", "JudgeInput")
    acceptance: list[AcceptanceItem] = list(getattr(rec.plan, "acceptance", []) or [])
    if not acceptance and ws.plan_path.is_file():
        try:
            raw = json.loads(ws.plan_path.read_text()).get("acceptance") or []
            acceptance = [AcceptanceItem.model_validate(a) for a in raw]
        except (ValueError, TypeError):
            acceptance = []
    return JudgeInput(
        spec=rec.spec, renders=judged_subset(resolve_paths(ws, rnd.renders)), measurement=rnd.measurement, gates=rnd.gates,
        acceptance=acceptance, plan_summary=plan_summary_for(ws), round_index=rnd.index,
        previous=previous_judgment(ws, rec, rnd.index), extra_context=extra_context_for(ws, rec, rnd),
        geometry_views=clay_geometry_views(ws, rnd.index),
    )


def make_judge(rec: RunRecord, rubric_name: str, model_id: str, n: int) -> Any:
    """ReferenceJudge for measured rubrics / reference specs, VlmJudge otherwise."""
    load_rubric = C.lazy("codeverse.judges.rubrics", "load_rubric")
    try:
        rubric = load_rubric(rubric_name)
    except Exception as e:  # unknown rubric name / bad yaml
        raise C.CliError(f"cannot load rubric {rubric_name!r}: {e}") from e
    measured = bool(rubric.measured_criteria())
    if measured or rec.spec.references:
        ReferenceJudge = C.lazy("codeverse.judges.reference", "ReferenceJudge")
        return ReferenceJudge(model_id=model_id, n_samples=n, rubric=rubric_name)
    VlmJudge = C.lazy("codeverse.judges.vlm_judge", "VlmJudge")
    return VlmJudge(rubric=rubric_name, model_id=model_id, n_samples=n)


def count_prompt_images(inp: Any, rubric_name: str) -> int | None:
    """Number of image parts a single judge sample will send (None on failure)."""
    try:
        load_rubric = C.lazy("codeverse.judges.rubrics", "load_rubric")
        build_judge_messages = C.lazy("codeverse.judges.prompt_builder", "build_judge_messages")
        from codeverse.contracts.chat import ImagePart

        _, messages = build_judge_messages(inp, load_rubric(rubric_name))
        return sum(1 for m in messages for p in m.parts if isinstance(p, ImagePart))
    except Exception:  # noqa: BLE001 — informational only
        return None
