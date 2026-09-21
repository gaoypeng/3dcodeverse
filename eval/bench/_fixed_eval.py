"""The fixed evaluator shared by every arm of ``bench/compare_backends.py``.

``FixedEvaluator.evaluate(ws, spec)``: the cell's runtime lint + build → ``measure_glb``
→ connectivity gate → 14-view ``render_glb`` (articulated: + joint sweep + pose sheet;
graphics: the frame sheet; scene: authored cameras + orbit rig at two times + the
scene_frames gate) → ``judge_for(spec)`` — a ``VlmJudge`` (or ``LikenessJudge``
for graphics with reference photos) on ``rubric_for(spec)``, the track's ``TRACK_INFO``
rubric, the ONE track→rubric mapping of the compare bench.  The judge's acceptance checklist is the brief's
``must_have`` list (``acceptance_from_spec``) so harness and one-shot arms are
scored against exactly the same checklist — never the harness's plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.config import Settings, get_settings
from codeverse.contracts.artifacts import BuildResult, GateReport, Judgment, Measurement, RenderSet
from codeverse.contracts.common import TRACK_INFO, Language, Track
from codeverse.contracts.plan import AcceptanceItem
from codeverse.contracts.spec import Spec
from codeverse.conventions import OBJECT_VIEWS
from codeverse.workspace import Workspace

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
            from codeverse.languages import get_runtime

            self._runtimes[lang] = get_runtime(lang)
        return self._runtimes[lang]

    @property
    def judge(self) -> Any:
        """The judge on the evaluator's default rubric (the battery's track); cells use ``judge_for``."""
        return self._vlm_judge(self.rubric)

    def _vlm_judge(self, rubric: str) -> Any:
        if rubric not in self._judges:
            from codeverse.judges.vlm_judge import VlmJudge

            self._judges[rubric] = VlmJudge(rubric=rubric, model_id=self.judge_model, n_samples=self.n_samples)
        return self._judges[rubric]

    def judge_for(self, spec: Spec) -> Any:
        """The fixed judge for THIS cell: ``rubric_for(spec)`` (the pinned rubric if a caller set
        one), through ``LikenessJudge`` for a graphics cell with reference photos, ``VlmJudge``
        (cached per rubric) otherwise.

        The evaluator judged every cell with the object rubric on a GLB.  A graphics cell
        has frames, not a GLB, so ``evaluate`` returned before judging and every graphics
        cell of an ``ab_plan`` battery was recorded ``judge_error: no judgment`` — measured
        2026-08-26 on ``seed_v1`` (both arms, three rounds of in-loop verdicts each, and
        no pair score).  Graphics cells are judged on their frame sheet with the track's
        rubric (``shader_v2``), through ``LikenessJudge`` when the spec carries reference
        photos so the arms see the same photos the loop saw.
        """
        rubric = self.rubric if self._pinned else rubric_for(spec)
        if spec.track is Track.GRAPHICS and spec.references:
            from codeverse.judges.vlm_judge import LikenessJudge

            return LikenessJudge(self.judge_model, n_samples=self.n_samples, rubric=rubric)
        return self._vlm_judge(rubric)

    def build(self, ws: Workspace, language: Language | str) -> tuple[BuildResult, GateReport]:
        rt = self.runtime(language)
        lint = rt.lint(ws)
        build = rt.build(ws, timeout_s=self.settings.limits.build_timeout_s)
        return build, lint

    def evaluate(self, ws: Workspace, spec: Spec) -> EvalOutcome:
        from codeverse.judges.base import JudgeInput
        from codeverse.spatial.connectivity import check_connectivity
        from codeverse.spatial.measure import measure_glb
        from codeverse.spatial.render import render_glb

        build, lint = self.build(ws, spec.language)
        out = EvalOutcome(build=build, lint=lint, gates=[lint])
        if build.ok and spec.track is Track.GRAPHICS:
            return self._evaluate_frames(ws, spec, out)
        if build.ok and spec.track is Track.SCENE:
            return self._evaluate_scene(ws, spec, out)
        if not build.ok or not build.glb_path:
            return out
        try:
            glb = Path(build.glb_path)
            out.measurement = measure_glb(glb)
            out.gates.append(check_connectivity(glb))
            r = self.settings.render
            out.renders = render_glb(glb, ws.renders_dir(0), views=list(OBJECT_VIEWS), width=r.width, height=r.height, sheet=True)
            if spec.track is Track.ARTICULATED_OBJECT:
                # the same deterministic articulation evidence the track gives its judge: the
                # joint sweep (collisions over every joint's range) and the pose sheet / pose
                # tiles, read straight from the built URDF — no plan, so every arm is treated alike
                from codeverse.tracks.articulated_object import default_joint_sweep

                sweep, pose_views = default_joint_sweep(ws, None, ws.renders_dir(0) / "poses")
                out.gates.append(sweep)
                if pose_views:
                    out.renders.views = list(out.renders.views) + pose_views
            inp = JudgeInput(spec=spec, renders=out.renders, measurement=out.measurement, gates=out.gates,
                             acceptance=acceptance_from_spec(spec), round_index=0)
            out.judgment = self.judge_for(spec).judge(inp)
        except Exception as e:  # noqa: BLE001 — recorded per cell, never kills the matrix
            out.error = f"{type(e).__name__}: {e}"
        return out


    def _evaluate_scene(self, ws: Workspace, spec: Spec, out: EvalOutcome) -> EvalOutcome:
        """Scene: the authored cameras plus the orbit rig at t = 0 and 1.5 s, the scene_frames gate,
        the scene rubric — the pictures the loop's judge sees, minus the loop's plan (2026-09-07:
        the compare bench had no scene branch, so a scene cell built, then returned unjudged)."""
        from codeverse.judges.base import JudgeInput
        from codeverse.spatial.frame_metrics import frame_gate_from_renders
        from codeverse.spatial.render_scene import render_scene
        try:
            out.renders = render_scene(ws, ws.renders_dir(0), orbit=True, times=(0.0, 1.5), sheet=True)
            out.gates.append(frame_gate_from_renders(ws.renders_dir(0)))
            inp = JudgeInput(spec=spec, renders=out.renders, gates=out.gates, acceptance=acceptance_from_spec(spec),
                             round_index=0)
            out.judgment = self.judge_for(spec).judge(inp)
        except Exception as e:  # noqa: BLE001 — recorded per cell, never kills the matrix
            out.error = f"{type(e).__name__}: {e}"
        return out

    def _evaluate_frames(self, ws: Workspace, spec: Spec, out: EvalOutcome) -> EvalOutcome:
        """Graphics: the judged frames + the gl_frames gate + the frame metrics, as the loop does."""
        from codeverse.judges.base import JudgeInput
        from codeverse.languages._gl_common import read_metrics
        from codeverse.tracks.graphics import frame_stats_text, frames_render_set

        try:
            out.renders = frames_render_set(ws, out.build, 0)
            m = read_metrics(ws)
            if m is not None:
                out.gates.append(m[1])
            inp = JudgeInput(spec=spec, renders=out.renders, gates=out.gates, acceptance=acceptance_from_spec(spec),
                             round_index=0, extra_context="FRAME METRICS (harness-measured):\n" + frame_stats_text(ws))
            out.judgment = self.judge_for(spec).judge(inp)
        except Exception as e:  # noqa: BLE001 — recorded per cell, never kills the matrix
            out.error = f"{type(e).__name__}: {e}"
        return out


__all__ = ["RUBRIC", "EvalOutcome", "FixedEvaluator", "acceptance_from_spec", "rubric_for"]
