"""Fakes for the harness-vs-one-shot bench (no models, no Blender, no Chrome)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from PIL import Image

REPO = Path(__file__).resolve().parents[1]           # eval/ — the `bench` package lives here
HARNESS = REPO.parent / "harness"                     # the tree under evaluation: ITS codeverse3d, not an install
for _p in (HARNESS, REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _harness_test_module(rel: str, name: str) -> Any:
    """A helper module of the harness's own test suite, loaded by path: `tests` here is
    eval/tests, so `tests.flywheel_cli.conftest` cannot be imported by name."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, HARNESS / "tests" / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_fake_run = _harness_test_module("flywheel_cli/conftest.py", "_harness_flywheel_conftest").make_fake_run

from bench._fixed_eval import EvalOutcome  # noqa: E402
from bench._oneshot import MODEL_FILE, OneShotResult  # noqa: E402
from codeverse3d.contracts.artifacts import (  # noqa: E402
    BuildResult,
    GateFinding,
    GateReport,
    Judgment,  # noqa: E402
    Measurement,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse3d.contracts.common import Usage  # noqa: E402
from codeverse3d.contracts.run import RoundRecord, RunRecord, RunStatus  # noqa: E402
from codeverse3d.contracts.spec import Spec  # noqa: E402
from codeverse3d.workspace import Workspace  # noqa: E402

BATTERY = REPO / "bench" / "prompts" / "compare_v1.yaml"
GOOD = "import bpy\n# score={score}\nbpy.ops.mesh.primitive_cube_add()\nbpy.context.object.name = 'Body'\n"
BAD = "import bpy\nraise RuntimeError('BOOM')\n"


def _png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (200, 50, 50)).save(path)
    return path


def _score_of(code: str) -> float:
    for line in code.splitlines():
        if line.startswith("# score="):
            return float(line.split("=", 1)[1])
    return 0.5


class FakeEvaluator:
    """Builds succeed unless the code raises BOOM; score is read from a ``# score=`` comment."""

    def __init__(self) -> None:
        self.evaluated: list[str] = []
        self.builds = 0

    def build(self, ws: Workspace, language: object = None) -> tuple[BuildResult, GateReport]:
        self.builds += 1
        code = (ws.root / MODEL_FILE).read_text()
        lint = GateReport(gate="lint:blender", passed=True)
        if "BOOM" in code:
            return BuildResult(ok=False, language="blender", error_type="RuntimeError", error_message="BOOM at line 2",
                               error_file=MODEL_FILE, error_line=2), lint
        glb = ws.artifacts / "object.glb"
        glb.parent.mkdir(parents=True, exist_ok=True)
        glb.write_bytes(b"glb")
        return BuildResult(ok=True, language="blender", glb_path=str(glb)), lint

    def evaluate(self, ws: Workspace, spec: Spec) -> EvalOutcome:
        self.evaluated.append(f"{spec.id}")
        build, lint = self.build(ws)
        out = EvalOutcome(build=build, lint=lint, gates=[lint])
        if not build.ok:
            return out
        code = (ws.root / MODEL_FILE).read_text()
        score = _score_of(code)
        sheet = _png(ws.renders_dir(0) / "sheet.png")
        view = _png(ws.renders_dir(0) / "view_front.png")
        out.renders = RenderSet(views=[RenderView(name="front", path=str(view))], contact_sheet=str(sheet))
        out.measurement = Measurement(bbox_min=(0, 0, 0), bbox_max=(1, 1, 1), extents=(1, 1, 1), center=(0.5, 0.5, 0.5),
                                      tri_count=int(score * 1000), n_meshes=1, n_islands=1)
        gate = GateReport(gate="connectivity", passed=score >= 0.4, findings=[] if score >= 0.4 else [
            GateFinding(gate="connectivity", severity=Severity.ERROR, target="Body", message="floating part")])
        out.gates.append(gate)
        out.judgment = Judgment(rubric="static_object_v1", judge_backend="fake", scores={"intent_fidelity": score},
                                overall=score, passed=score >= 0.72, n_samples=2, usage=Usage(cost_usd=0.01))
        return out


class FakeBackend:
    """One-shot backend: answers from a script of texts (cycled); records prompts."""

    def __init__(self, answers: list[str], *, tool_calls: int = 0):
        self.answers = answers
        self.calls: list[str] = []
        self.tool_calls = tool_calls
        self.id = "oneshot:fake"

    def generate(self, prompt: str, *, out_dir: Path, timeout_s: float = 0, label: str = "") -> OneShotResult:
        self.calls.append(prompt)
        nth = sum(1 for c in self.calls if c.splitlines()[0] == prompt.splitlines()[0])  # per-prompt attempt no.
        text = self.answers[min(nth - 1, len(self.answers) - 1)]
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "response.md").write_text(text)
        return OneShotResult(ok=bool(text), text=text, usage=Usage(cost_usd=0.02), tool_calls=self.tool_calls,
                             notes="" if text else "empty", duration_s=1.0, transcript_dir=str(out_dir))


def fake_run_track(score: float = 0.8):
    calls: list[str] = []

    def run(spec: Spec, ws: Workspace, resume: bool) -> RunRecord:
        calls.append(spec.id)
        ws.src.mkdir(parents=True, exist_ok=True)
        (ws.root / MODEL_FILE).write_text(GOOD.format(score=score))
        (ws.src / "parts").mkdir(exist_ok=True)
        (ws.src / "parts" / "legs.py").write_text("LEGS = 3\n")
        loop_verdict = Judgment(rubric="static_object_v1", scores={}, overall=round(score - 0.1, 4), passed=False)
        rec = RunRecord(spec=spec, workspace=str(ws.root), status=RunStatus.MAX_ROUNDS,
                        rounds=[RoundRecord(index=0, kind="baseline", judgment=loop_verdict,
                                            build=BuildResult(ok=True, language="blender"))],
                        total_usage=Usage(cost_usd=0.9, tool_calls=12))
        ws.write_json(ws.record_path, rec)
        return rec

    run.calls = calls  # type: ignore[attr-defined]
    return run


class FakePairwise:
    def __init__(self, model_id: str):
        self.model_id = model_id
        self.calls = 0

    def compare(self, spec: Spec, ra: RenderSet, rb: RenderSet, *, rubric: Any = None):
        from codeverse3d.judges.pairwise import PairwiseResult

        self.calls += 1
        return PairwiseResult(winner="a", confidence=0.8, reasons=["Candidate A is cleaner"], usage=Usage(cost_usd=0.03))
