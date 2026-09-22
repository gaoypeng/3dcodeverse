"""``3dcode judge`` implementation: re-judge a round with the SAME inputs the
in-run judge saw.

The payload is ``judges.base.round_input`` — the builder the in-run judge uses — fed from
disk: the round's renders / measurement / gates from ``rounds/rNN.json`` (fallback: the
round inside record.json), the typed plan from record.json (else ``plan.json``), ``previous``
the preceding round's (non-degraded) verdict, the track's ``extra_context`` rebuilt from the
stored gates / frame metrics, and the clay geometry views rendered for the in-run judge
(``renders/rNN/clay/``) reattached when present.  The judge class matches the in-run choice:
``ReferenceJudge`` when the rubric has measured criteria or the spec carries reference
images, ``VlmJudge`` otherwise.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from codeverse3d.cli import _common as C
from codeverse3d.contracts.artifacts import BuildResult, RenderSet, RenderView
from codeverse3d.contracts.common import TRACK_INFO
from codeverse3d.contracts.plan import Plan
from codeverse3d.contracts.run import RoundRecord, RunRecord
from codeverse3d.contracts.spec import Spec
from codeverse3d.judges.base import judged_subset, resolve_paths, round_input
from codeverse3d.proc import read_json_or_none
from codeverse3d.workspace import Workspace


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


def stored_plan(ws: Workspace, rec: RunRecord) -> Plan | None:
    """The run's typed plan: record.json's, else ``plan.json`` read as the track's plan model
    (a run that stopped before its record was written)."""
    if rec.plan is not None:
        return rec.plan
    from codeverse3d.tracks import get_track

    data = read_json_or_none(ws.plan_path)
    try:
        return get_track(rec.spec.track).plan_model.model_validate(data) if data else None
    except ValidationError:
        return None


def previous_judgment(ws: Workspace, rec: RunRecord, index: int) -> Any:
    """The preceding round's verdict (skipping degraded ones), like the round loop."""
    from codeverse3d.record.record import effective_judgment

    for i in range(index - 1, -1, -1):
        rnd = load_round(ws, rec, i)
        if rnd is None:
            continue
        j = effective_judgment(rnd)
        if j is not None:
            return j
    return None


def extra_context_for(ws: Workspace, rec: RunRecord, plan: Plan | None, rnd: RoundRecord) -> str:
    """The track pipeline's OWN ``judge_context`` over the stored artifacts —
    ``judge_context(ws, plan, round_index, build, gates)`` needs no run context,
    so the CLI never restates the per-track formatting."""
    from codeverse3d.tracks import get_track

    build = rnd.build if rnd.build is not None else BuildResult(ok=False, language=rec.spec.language.value)
    try:
        return get_track(rec.spec.track).make_pipeline().judge_context(ws, plan, rnd.index, build, list(rnd.gates))
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
    """``round_input`` for a stored round (``3dcode judge``, calibration)."""
    plan = stored_plan(ws, rec)
    return round_input(rec.spec, plan, rnd, renders=judged_subset(resolve_paths(ws, rnd.renders)), gates=rnd.gates,
                       previous=previous_judgment(ws, rec, rnd.index), extra_context=extra_context_for(ws, rec, plan, rnd),
                       geometry_views=clay_geometry_views(ws, rnd.index), glb_path=stored_glb_path(ws, rnd))


def stored_glb_path(ws: Workspace, rnd: RoundRecord) -> str | None:
    """The round's canonical GLB, rebased to THIS workspace — what lets ``3dcode judge``
    reproduce the D48 conditional slice payload from the stored gates."""
    if rnd.build is None or not rnd.build.glb_path:
        return None
    kept = ws.round_artifacts(rnd.index) / "object.glb"  # the round's own GLB; the stored path is
    p = kept if kept.is_file() else ws.rebase(rnd.build.glb_path)  # the canonical one, rebuilt since
    return str(p) if p.is_file() else None


def make_judge(spec: Spec, rubric_name: str, model_id: str, n: int, **options: Any) -> Any:
    """ReferenceJudge for measured rubrics / reference specs, VlmJudge otherwise; ``options``
    go to the judge (calibration's cache dir, thinking level, fixed order)."""
    from codeverse3d.judges import vlm_judge
    from codeverse3d.judges.rubrics import load_rubric

    try:
        rubric = load_rubric(rubric_name)
    except Exception as e:  # unknown rubric name / bad yaml
        raise C.CliError(f"cannot load rubric {rubric_name!r}: {e}") from e
    if rubric.measured_criteria() or spec.references:
        return vlm_judge.ReferenceJudge(model_id=model_id, n_samples=n, rubric=rubric_name, **options)
    return vlm_judge.VlmJudge(rubric=rubric_name, model_id=model_id, n_samples=n, **options)


def count_prompt_images(inp: Any, rubric_name: str, judge: Any = None) -> int | None:
    """Number of image parts a single judge sample will send (None on failure).

    With ``judge`` given, its D48 slice payload is counted too, so a dirty round's
    printed count matches what the verdict call actually sends."""
    try:
        from codeverse3d.contracts.chat import ImagePart
        from codeverse3d.judges.prompt_builder import build_judge_messages
        from codeverse3d.judges.rubrics import load_rubric

        slices, elicit = judge.slice_payload(inp) if judge is not None else ([], False)
        _, messages = build_judge_messages(inp, load_rubric(rubric_name),
                                           slice_images=slices, provenance_elicitation=elicit)
        return sum(1 for m in messages for p in m.parts if isinstance(p, ImagePart))
    except Exception:  # noqa: BLE001 — informational only
        return None
