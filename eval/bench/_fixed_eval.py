"""The fixed evaluator shared by every arm of ``bench/compare_backends.py``.

``FixedEvaluator.evaluate(ws, spec)``: the cell's runtime lint + build → the track's evidence
(objects: ``glb_evidence`` — measure, connectivity in the author's frame, 14-view render, clay
views; articulated: + joint sweep + pose sheet; graphics: the frame sheet; scene: authored
cameras + orbit rig at two times + the scene_frames gate) → ``fixed_input`` — the harness's
``round_input``, so the payload is the in-run judge's for the same round → ``judge_for(spec)``
— the class the loop would use (``vlm_judge.judge_for``) on ``rubric_for(spec)``, the track's
``TRACK_INFO`` rubric, the ONE track→rubric mapping of the compare bench.  The judge's
acceptance checklist is the brief's ``must_have`` list (``acceptance_from_spec``) so harness
and one-shot arms are scored against exactly the same checklist — never the harness's plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse3d.config import Settings, get_settings
from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateReport,
    Judgment,
    Measurement,
    RenderSet,
)
from codeverse3d.contracts.common import TRACK_INFO, Language, Track
from codeverse3d.contracts.plan import AcceptanceItem
from codeverse3d.contracts.run import RoundRecord
from codeverse3d.contracts.spec import Spec
from codeverse3d.conventions import OBJECT_VIEWS
from codeverse3d.judges.base import SLICE_TRACKS, JudgeInput, judged_subset, round_input
from codeverse3d.workspace import Workspace

RUBRIC = "static_object_v1"


def rubric_for(spec: Spec | Track) -> str:
    """The fixed judge's rubric for a cell (or for a battery's track): the track's ``TRACK_INFO``
    row — static_object_v1 / articulated_v1 / scene_v1 / shader_v2.

    The one place the compare bench maps a track to a rubric: ``FixedEvaluator`` defaults to
    it, ``judge_for`` picks each cell's judge with it and ``_compare_report`` names it in the
    report header.  (PR #1 carried a second table, ``RUBRIC_BY_TRACK``, next to main's
    ``judge_for``; folded here on merge, 2026-08-26.)
    """
    track = spec if isinstance(spec, Track) else Track(spec.track)
    return TRACK_INFO[track].rubric


def acceptance_from_spec(spec: Spec) -> list[AcceptanceItem]:
    """The fixed judge's checklist: the brief's must_have items, identical for every arm."""
    return [AcceptanceItem(id=f"must_{i + 1}", text=m, how="visual", priority="must")
            for i, m in enumerate(spec.constraints.must_have)]


# ----------------------------------------------------------------------------- fixed evaluator
class EvalOutcome(BaseModel):
    build: BuildResult
    lint: GateReport
    gates: list[GateReport] = Field(default_factory=list)
    measurement: Measurement | None = None
    renders: RenderSet | None = None
    judgment: Judgment | None = None
    error: str = ""

    @property
    def gate_errors(self) -> list[str]:
        return [f"{g.gate}: {f.message}" for g in self.gates for f in g.errors]


class FixedEvaluator:
    """build → measure → connectivity → render → VlmJudge, the same for every arm."""

    def __init__(self, judge_model: str, *, n_samples: int = 2, settings: Settings | None = None, rubric: str | None = None,
                 track: Track = Track.STATIC_OBJECT, language: Language = Language.BLENDER):
        self.judge_model = judge_model
        self.n_samples = n_samples
        self.settings = settings or get_settings()
        self.track = track
        self.language = language  # the battery's default; cells are built with THEIR spec's language (runtime())
        # the rubric follows each cell's track (rubric_for, through judge_for); the battery's
        # track sets the default and a caller may pin one rubric for every cell
        self._pinned = rubric is not None
        self.rubric = rubric or rubric_for(track)
        self._runtimes: dict[Language, Any] = {}
        self._judges: dict[str, Any] = {}

    def runtime(self, language: Language | str) -> Any:
        """The build runtime for THIS cell's language, cached per language.

        This was hardwired to Blender.  A three.js cell whose harness run had been judged
        0.589 on ``src/object.js`` was then evaluated by the Blender runtime, which raised
        ``MissingEntryFile: src/model.py does not exist`` — and the cell was recorded
        ``build_failed`` with a hard **0.0**.  Measured 2026-08-26 on
        ``fancy_v1/tj``; every three.js cell in that battery was heading for the same
        false zero.  Same bug class as b4ea4cc (``entry_of``), one layer further down.
        """
        lang = Language(language)
        if lang not in self._runtimes:
            from codeverse3d.languages import get_runtime

            self._runtimes[lang] = get_runtime(lang)
        return self._runtimes[lang]

    @property
    def judge(self) -> Any:
        """The judge on the evaluator's default rubric (the battery's track); cells use ``judge_for``."""
        return self._vlm_judge(self.rubric)

    def _vlm_judge(self, rubric: str) -> Any:
        if rubric not in self._judges:
            from codeverse3d.judges.vlm_judge import VlmJudge

            self._judges[rubric] = VlmJudge(rubric=rubric, model_id=self.judge_model, n_samples=self.n_samples)
        return self._judges[rubric]

    def judge_for(self, spec: Spec) -> Any:
        """The fixed judge for THIS cell: ``rubric_for(spec)`` (the pinned rubric if a caller set
        one), through the class the loop would judge it with (``vlm_judge.judge_for``: reference
        photos → ``LikenessJudge`` on graphics / scene, ``ReferenceJudge`` on an object track;
        a measured rubric → ``ReferenceJudge``), else ``VlmJudge`` (cached per rubric).

        The evaluator judged every cell with the object rubric on a GLB.  A graphics cell
        has frames, not a GLB, so ``evaluate`` returned before judging and every graphics
        cell of an ``ab_plan`` battery was recorded ``judge_error: no judgment`` — measured
        2026-08-26 on ``seed_v1`` (both arms, three rounds of in-loop verdicts each, and
        no pair score).  Graphics cells are judged on their frame sheet with the track's
        rubric (``shader_v2``), through ``LikenessJudge`` when the spec carries reference
        photos so the arms see the same photos the loop saw.
        """
        from codeverse3d.judges.vlm_judge import VlmJudge, judge_for

        rubric = self.rubric if self._pinned else rubric_for(spec)
        cls = judge_for(spec.track, rubric, references=bool(spec.references))
        if cls is VlmJudge:
            return self._vlm_judge(rubric)
        return cls(model_id=self.judge_model, n_samples=self.n_samples, rubric=rubric)

    def build(self, ws: Workspace, language: Language | str) -> tuple[BuildResult, GateReport]:
        rt = self.runtime(language)
        lint = rt.lint(ws)
        build = rt.build(ws, timeout_s=self.settings.limits.build_timeout_s)
        return build, lint

    def evaluate(self, ws: Workspace, spec: Spec) -> EvalOutcome:
        """build → the track's evidence (articulated: the sweep reads the built URDF, no plan, so
        every arm is swept alike; the build's own gates last, as in ``steps._run_round``) → judge."""
        from codeverse3d.tracks import get_track

        build, lint = self.build(ws, spec.language)
        out = EvalOutcome(build=build, lint=lint, gates=[lint])
        if not build.ok or (spec.track.value in SLICE_TRACKS and not build.glb_path):
            return out
        geometry = None
        try:
            if spec.track is Track.GRAPHICS:
                from codeverse3d.tracks.graphics import frames_render_set

                out.renders = frames_render_set(ws, build, 0)
            elif spec.track is Track.SCENE:
                from codeverse3d.spatial.frame_metrics import frame_gate_from_renders
                from codeverse3d.spatial.render_scene import render_scene

                out.renders = render_scene(ws, ws.renders_dir(0), orbit=True, times=(0.0, 1.5), sheet=True)
                out.gates.append(frame_gate_from_renders(ws.renders_dir(0)))
            else:
                out.measurement, gate, out.renders, geometry = glb_evidence(
                    Path(build.glb_path), ws.renders_dir(0), spec.language, self.settings)
                out.gates.append(gate)
                if spec.track is Track.ARTICULATED_OBJECT:
                    from codeverse3d.tracks.articulated_object import default_joint_sweep

                    sweep, pose_views = default_joint_sweep(ws, None, ws.renders_dir(0) / "poses")
                    out.gates.append(sweep)
                    out.renders.views = list(out.renders.views) + pose_views
            out.gates.extend(build.gates)
            extra = get_track(spec.track).make_pipeline().judge_context(ws, None, 0, build, out.gates)
            inp = fixed_input(spec, renders=out.renders, gates=out.gates, measurement=out.measurement,
                              geometry_views=geometry, glb_path=build.glb_path, extra_context=extra)
            out.judgment = self.judge_for(spec).judge(inp)
        except Exception as e:  # noqa: BLE001 — recorded per cell, never kills the matrix
            out.error = f"{type(e).__name__}: {e}"
        return out


def glb_evidence(glb: Path, renders_dir: Path, language: Language | str,
                 settings: Settings) -> tuple[Measurement, GateReport, RenderSet, RenderSet | None]:
    """``ObjectPipeline``'s measure / connectivity (author's frame) / render / clay views for a GLB
    with no plan, through the same ``Services`` calls; clay ``None`` when it fails, as in the loop."""
    from codeverse3d.tracks.common import Services
    from codeverse3d.tracks.static_object import GEOMETRY_VIEWS

    svc, r = Services(), settings.render
    measurement = svc.measure(glb)
    gate = svc.connectivity(glb, str(language))
    renders = svc.render_object(glb, renders_dir, views=OBJECT_VIEWS, width=r.width, height=r.height)
    try:
        geometry = svc.render_geometry(glb, renders_dir / "clay", views=GEOMETRY_VIEWS)
    except Exception:  # noqa: BLE001 — optional judge context, never fatal (ObjectPipeline.geometry_views)
        geometry = None
    return measurement, gate, renders, geometry


def fixed_input(spec: Spec, *, renders: RenderSet, gates: list[GateReport], measurement: Measurement | None = None,
                geometry_views: RenderSet | None = None, glb_path: str | None = None,
                extra_context: str = "") -> JudgeInput:
    """``round_input`` (the in-run judge's builder) for a round 0 with no plan and no previous verdict;
    the ONE deliberate difference: the checklist is the brief's ``must_have``, never a plan's (N83)."""
    inp = round_input(spec, None, RoundRecord(index=0, kind="baseline", measurement=measurement),
                      renders=judged_subset(renders), gates=gates, previous=None, extra_context=extra_context,
                      geometry_views=geometry_views, glb_path=glb_path)
    return inp.model_copy(update={"acceptance": acceptance_from_spec(spec)})


__all__ = ["RUBRIC", "EvalOutcome", "FixedEvaluator", "acceptance_from_spec", "fixed_input", "glb_evidence", "rubric_for"]
