"""``3dcodeverse judge`` implementation: re-judge a stored round with the inputs the in-run judge
saw, built by ``judges.base.round_input`` from the run directory."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from codeverse3d.cli import _common as C
from codeverse3d.contracts.artifacts import BuildResult, RenderSet, RenderView
from codeverse3d.contracts.plan import Plan
from codeverse3d.contracts.run import RoundRecord, RunRecord
from codeverse3d.contracts.spec import Spec
from codeverse3d.judges.base import judged_subset, resolve_paths, round_input
from codeverse3d.proc import read_json_or_none
from c3d_flywheel.record.record import previous_verdict
from codeverse3d.workspace import Workspace


def load_round(ws: Workspace, rec: RunRecord, index: int) -> RoundRecord | None:
    p = ws.root / "rounds" / f"r{index:02d}.json"
    if p.is_file():
        try:
            return RoundRecord.model_validate_json(p.read_text())
        except Exception:  # noqa: BLE001 — fall through to the record copy
            pass
    return next((r for r in rec.rounds if r.index == index), None)


def rubric_for(rec: RunRecord, rnd: RoundRecord, override: str | None) -> str:
    from c3d_flywheel.record.record import rubric_of

    if override:
        return override
    if rnd.judgment is not None and rnd.judgment.rubric:
        return rnd.judgment.rubric
    return rubric_of(rec)


def stored_plan(ws: Workspace, rec: RunRecord) -> Plan | None:
    """The run's typed plan: record.json's, else ``plan.json``."""
    if rec.plan is not None:
        return rec.plan
    from codeverse3d.tracks import get_track

    data = read_json_or_none(ws.plan_path)
    try:
        return get_track(rec.spec.track).plan_model.model_validate(data) if data else None
    except ValidationError:
        return None


def extra_context_for(ws: Workspace, rec: RunRecord, plan: Plan | None, rnd: RoundRecord) -> str:
    from codeverse3d.tracks import get_track

    build = rnd.build if rnd.build is not None else BuildResult(ok=False, language=rec.spec.language.value)
    try:
        return get_track(rec.spec.track).make_pipeline().judge_context(ws, plan, rnd.index, build, list(rnd.gates))
    except Exception as e:  # noqa: BLE001 — extra context is optional judge input
        return f"(judge context unavailable: {type(e).__name__}: {e})"


def clay_geometry_views(ws: Workspace, index: int) -> RenderSet | None:
    d = ws.renders_dir(index) / "clay"
    if not d.is_dir():
        return None
    views = [RenderView(name=p.stem.removeprefix("view_"), path=str(p), mode="clay")
             for p in sorted(d.glob("view_*.png"))]
    return RenderSet(views=views, renderer="stored") if views else None


def build_judge_input(ws: Workspace, rec: RunRecord, rnd: RoundRecord) -> Any:
    plan = stored_plan(ws, rec)
    return round_input(rec.spec, plan, rnd, renders=judged_subset(resolve_paths(ws, rnd.renders)), gates=rnd.gates,
                       previous=previous_verdict([load_round(ws, rec, rnd.index - 1)], rnd.index), extra_context=extra_context_for(ws, rec, plan, rnd),
                       geometry_views=clay_geometry_views(ws, rnd.index), glb_path=stored_glb_path(ws, rnd))


def stored_glb_path(ws: Workspace, rnd: RoundRecord) -> str | None:
    """The round's own GLB (never the canonical one, which is the last build's)."""
    from c3d_flywheel.select import round_file

    glb = round_file(ws, rnd)
    return str(glb) if glb is not None else None


def make_judge(spec: Spec, rubric_name: str, model_id: str, n: int, **options: Any) -> Any:
    from codeverse3d.judges import vlm_judge
    from codeverse3d.judges.rubrics import load_rubric

    try:
        rubric = load_rubric(rubric_name)
    except Exception as e:  # unknown rubric name / bad yaml
        raise C.CliError(f"cannot load rubric {rubric_name!r}: {e}") from e
    cls = vlm_judge.judge_for(spec.track, rubric, references=bool(spec.references))
    return cls(model_id=model_id, n_samples=n, rubric=rubric_name, **options)
