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
from codeverse.contracts.artifacts import GateReport, RenderSet, RenderView
from codeverse.contracts.common import Track
from codeverse.contracts.plan import AcceptanceItem
from codeverse.contracts.run import RoundRecord, RunRecord
from codeverse.workspace import Workspace

#: rubric per track when the round has no stored judgment (mirrors the tracks)
TRACK_RUBRIC: dict[Track, str] = {
    Track.STATIC_OBJECT: "static_object_v1",
    Track.ARTICULATED_OBJECT: "articulated_v1",
    Track.SCENE: "scene_v1",
    Track.GRAPHICS: "shader_v1",
}


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
    """--rubric > the rubric the round was judged with > the track default."""
    if override:
        return override
    if rnd.judgment is not None and rnd.judgment.rubric:
        return rnd.judgment.rubric
    return TRACK_RUBRIC.get(rec.spec.track, "static_object_v1")


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


def _gate(rnd: RoundRecord, name: str) -> GateReport | None:
    return next((g for g in rnd.gates if g.gate == name), None)


def extra_context_for(ws: Workspace, rec: RunRecord, rnd: RoundRecord) -> str:
    """Rebuild the track pipeline's ``judge_context`` from the stored artifacts."""
    track = rec.spec.track
    if track is Track.GRAPHICS:
        frame_stats_text = C.lazy("codeverse.tracks.graphics_steps", "frame_stats_text")
        renderer = ""
        if rnd.build is not None and isinstance(rnd.build.census, dict):
            renderer = str(rnd.build.census.get("renderer", ""))
        return f"FRAME METRICS (harness-measured, renderer {renderer or 'moderngl'}):\n{frame_stats_text(ws)}"
    if track is Track.ARTICULATED_OBJECT:
        lines = ["Articulation sheet: the pose_* views show the object at rest, each joint at its lower and upper limit."]
        sweep = _gate(rnd, "joint_sweep")
        if sweep is not None:
            errs = [f"- {f.target or 'joint'}: {f.message}" for f in sweep.errors]
            lines.append(f"Joint sweep: {'no penetrations' if not errs else str(len(errs)) + ' problems'}")
            lines.extend(errs[:10])
        motion = _gate(rnd, "motion_direction")
        if motion is not None:
            wrong = [f"- {f.target}: {f.message}" for f in motion.errors]
            lines.append("Motion direction (harness FK check): "
                         + ("all planned directions realised" if not wrong else f"{len(wrong)} WRONG"))
            lines.extend(wrong[:6])
        return "\n".join(lines)
    if track is Track.SCENE and rec.plan is not None:
        plan = rec.plan
        lines = [f"Environment plan: {getattr(plan, 'environment', '')}",
                 "Animation plan: " + "; ".join(getattr(plan, "animation", []) or [])]
        cameras = getattr(plan, "cameras", []) or []
        if cameras:
            lines.append("Cameras: " + "; ".join(f"{c.name} ({c.purpose})" for c in cameras))
        return "\n".join(lines)
    return ""


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
    return rs.model_copy(update={"views": fixed, "contact_sheet": sheet})


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
        spec=rec.spec, renders=resolve_paths(ws, rnd.renders), measurement=rnd.measurement, gates=rnd.gates,
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
