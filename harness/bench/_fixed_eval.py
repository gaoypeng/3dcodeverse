"""The fixed evaluator shared by every arm of ``bench/compare_backends.py``.

``FixedEvaluator.evaluate(ws, spec)``: BlenderRuntime lint + build → ``measure_glb``
→ connectivity gate → 8-view ``render_glb`` → ``VlmJudge(static_object_v1,
<fixed judge>, n_samples)``.  The judge's acceptance checklist is the brief's
``must_have`` list (``acceptance_from_spec``) so harness and one-shot arms are
scored against exactly the same checklist — never the harness's plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.config import Settings, get_settings
from codeverse.contracts.artifacts import BuildResult, GateReport, Measurement, RenderSet
from codeverse.contracts.common import Language
from codeverse.contracts.judgment import Judgment
from codeverse.contracts.plan import AcceptanceItem
from codeverse.contracts.spec import Spec
from codeverse.conventions import OBJECT_VIEWS
from codeverse.workspace import Workspace

RUBRIC = "static_object_v1"


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

    def __init__(self, judge_model: str, *, n_samples: int = 2, settings: Settings | None = None, rubric: str = RUBRIC):
        self.judge_model = judge_model
        self.n_samples = n_samples
        self.settings = settings or get_settings()
        self.rubric = rubric
        self._runtimes: dict[Language, Any] = {}
        self._judge: Any = None

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
        if self._judge is None:
            from codeverse.judges.vlm_judge import VlmJudge

            self._judge = VlmJudge(rubric=self.rubric, model_id=self.judge_model, n_samples=self.n_samples)
        return self._judge

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
        if not build.ok or not build.glb_path:
            return out
        try:
            glb = Path(build.glb_path)
            out.measurement = measure_glb(glb)
            out.gates.append(check_connectivity(glb))
            r = self.settings.render
            out.renders = render_glb(glb, ws.renders_dir(0), views=list(OBJECT_VIEWS), width=r.width, height=r.height, sheet=True)
            inp = JudgeInput(spec=spec, renders=out.renders, measurement=out.measurement, gates=out.gates,
                             acceptance=acceptance_from_spec(spec), round_index=0)
            out.judgment = self.judge.judge(inp)
        except Exception as e:  # noqa: BLE001 — recorded per cell, never kills the matrix
            out.error = f"{type(e).__name__}: {e}"
        return out


__all__ = ["RUBRIC", "EvalOutcome", "FixedEvaluator", "acceptance_from_spec"]
