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
from typing import Any

from codeverse.cli import _common as C
from codeverse.contracts.artifacts import BuildResult, RenderSet, RenderView
from codeverse.contracts.common import TRACK_INFO
from codeverse.contracts.plan import AcceptanceItem
from codeverse.contracts.run import RoundRecord, RunRecord
from codeverse.judges.base import SLICE_TRACKS, judged_subset, plan_digest, resolve_paths
from codeverse.proc import read_json_or_none
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
    return TRACK_INFO[rec.spec.track].rubric


def plan_summary_for(ws: Workspace) -> str:
    """Track-agnostic plan digest from plan.json (same fields the tracks summarise)."""
    data = read_json_or_none(ws.plan_path)
    return plan_digest(data) if data else ""


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
        glb_path=stored_glb_path(ws, rec, rnd),
    )


def stored_glb_path(ws: Workspace, rec: RunRecord, rnd: RoundRecord) -> str | None:
    """The round's canonical GLB (object tracks), rebased to THIS workspace — what lets
    ``3dcv judge`` reproduce the D48 conditional slice payload from the stored gates."""
    if rec.spec.track.value not in SLICE_TRACKS or rnd.build is None or not rnd.build.glb_path:
        return None
    p = ws.rebase(rnd.build.glb_path)
    return str(p) if p.is_file() else None


def make_judge(rec: RunRecord, rubric_name: str, model_id: str, n: int) -> Any:
    """ReferenceJudge for measured rubrics / reference specs, VlmJudge otherwise."""
    load_rubric = C.lazy("codeverse.judges.rubrics", "load_rubric")
    try:
        rubric = load_rubric(rubric_name)
    except Exception as e:  # unknown rubric name / bad yaml
        raise C.CliError(f"cannot load rubric {rubric_name!r}: {e}") from e
    measured = bool(rubric.measured_criteria())
    if measured or rec.spec.references:
        ReferenceJudge = C.lazy("codeverse.judges.vlm_judge", "ReferenceJudge")
        return ReferenceJudge(model_id=model_id, n_samples=n, rubric=rubric_name)
    VlmJudge = C.lazy("codeverse.judges.vlm_judge", "VlmJudge")
    return VlmJudge(rubric=rubric_name, model_id=model_id, n_samples=n)


def count_prompt_images(inp: Any, rubric_name: str, judge: Any = None) -> int | None:
    """Number of image parts a single judge sample will send (None on failure).

    With ``judge`` given, its D48 slice payload is counted too, so a dirty round's
    printed count matches what the verdict call actually sends."""
    try:
        load_rubric = C.lazy("codeverse.judges.rubrics", "load_rubric")
        build_judge_messages = C.lazy("codeverse.judges.prompt_builder", "build_judge_messages")
        from codeverse.contracts.chat import ImagePart

        slices, elicit = judge.slice_payload(inp) if judge is not None else ([], False)
        _, messages = build_judge_messages(inp, load_rubric(rubric_name),
                                           slice_images=slices, provenance_elicitation=elicit)
        return sum(1 for m in messages for p in m.parts if isinstance(p, ImagePart))
    except Exception:  # noqa: BLE001 — informational only
        return None
