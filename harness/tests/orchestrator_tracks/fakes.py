"""Offline fakes for models / agents / runtimes / judges / spatial services."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from PIL import Image

from codeverse.contracts.agent import AgentJob, AgentResult
from codeverse.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    Measurement,
    PartMeasure,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse.contracts.chat import ChatRequest, ChatResponse
from codeverse.contracts.common import Language, Usage
from codeverse.contracts.judgment import ImprovementItem, JudgeIssue, Judgment
from codeverse.contracts.plan import Plan
from codeverse.contracts.run import RunRecord
from codeverse.conventions import to_snake
from codeverse.tracks.common import Services, ServiceUnavailable
from codeverse.tracks.generation import changed_files_safe, workspace_lock
from codeverse.workspace import Workspace

FAIL_MARK = "RAISE_BUILD_ERROR"


# ----------------------------------------------------------------------------- runtime
class FakeRuntime:
    """Writes a tiny GLB (one box per part) on build; fails when a src file contains FAIL_MARK."""

    def __init__(self, language: Language = Language.THREEJS, *, lint_error_mark: str = "LINT_ERROR"):
        self.language = language
        self.entry_globs = ("src/**/*.js",) if language in (Language.THREEJS, Language.SCENE_THREEJS) else ("src/model.py",)
        self.lint_error_mark = lint_error_mark
        self.builds = 0
        self.lock = threading.Lock()

    def skeleton(self, ws: Workspace, plan: Plan) -> list[Path]:
        out = []
        if self.language is Language.THREEJS:
            for p in getattr(plan, "parts", []):
                f = ws.src / "parts" / f"{to_snake(p.name)}.js"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(f"// skeleton for {p.name}\nexport function build{p.name}(THREE) {{ return new THREE.Group(); }}\n")
                out.append(f)
            f = ws.src / "object.js"
            f.write_text("// skeleton object.js\nexport function build(THREE) { return new THREE.Group(); }\n")
            out.append(f)
        elif self.language is Language.SCENE_THREEJS:
            for rel in ("src/scene.js", "src/env.js"):
                f = ws.root / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(f"// skeleton {rel}\n")
                out.append(f)
            (ws.src / "zones").mkdir(exist_ok=True)
            (ws.src / "assets").mkdir(exist_ok=True)
        else:
            f = ws.src / "model.py"
            f.write_text("# skeleton model.py\nimport bpy\n")
            out.append(f)
            if self.language is Language.URDF_BLENDER:
                u = ws.src / "robot.urdf"
                u.write_text("<robot name='skeleton'/>\n")
                out.append(u)
        return out

    def _src_text(self, ws: Workspace) -> str:
        return "\n".join(p.read_text() for p in sorted(ws.src.rglob("*")) if p.is_file())

    def lint(self, ws: Workspace) -> GateReport:
        text = self._src_text(ws)
        findings = []
        if self.lint_error_mark in text:
            findings.append(GateFinding(gate=f"lint:{self.language.value}", severity=Severity.ERROR, target="src/model.py",
                                        message="forbidden construct", fix_hint="remove LINT_ERROR"))
        return GateReport(gate=f"lint:{self.language.value}", passed=not findings, findings=findings)

    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        with self.lock:
            self.builds += 1
        text = self._src_text(ws)
        if FAIL_MARK in text:
            return BuildResult(ok=False, language=self.language.value, error_type="RuntimeError", error_message="boom: RAISE_BUILD_ERROR",
                               error_file="src/model.py", error_line=3, stderr_tail="Traceback...\nRuntimeError: boom")
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        glb = ws.artifacts / "object.glb"
        plan = json.loads(ws.plan_path.read_text()) if ws.plan_path.is_file() else {}
        scene = trimesh.Scene()
        parts = plan.get("parts") or [{"name": "Body", "bbox": {"center": [0, 0, 0.25], "extents": [0.5, 0.5, 0.5]}}]
        for p in parts:
            c, e = p["bbox"]["center"], p["bbox"]["extents"]
            box = trimesh.creation.box(extents=e)
            box.apply_translation(c)
            scene.add_geometry(box, node_name=p["name"], geom_name=p["name"])
        glb.write_bytes(scene.export(file_type="glb"))
        extras = {}
        if self.language is Language.URDF_BLENDER:
            u = ws.artifacts / "robot.urdf"
            u.write_text((ws.src / "robot.urdf").read_text() if (ws.src / "robot.urdf").is_file() else "<robot/>")
            extras["urdf"] = str(u)
        return BuildResult(ok=True, language=self.language.value, glb_path=str(glb), extra_paths=extras,
                           census={"objects": [p["name"] for p in parts]})

    def contract_doc(self) -> str:
        return f"FAKE CONTRACT for {self.language.value}"

    def cookbook_path(self) -> Path:
        return Path("/dev/null")


# ----------------------------------------------------------------------------- agent / model
class FakeAgent:
    """``writer(job, ws) -> dict[path, content]``; None → no files (silent bail)."""

    kind = "fake"
    model = "fake-model"

    def __init__(self, writer: Callable[[AgentJob, Workspace], dict[str, str] | None], cost: float = 0.01):
        self.writer = writer
        self.cost = cost
        self.jobs: list[AgentJob] = []

    @property
    def id(self) -> str:
        return "fake:fake-model"

    def available(self) -> tuple[bool, str]:
        return True, "ok"

    def run(self, job: AgentJob) -> AgentResult:
        self.jobs.append(job)
        ws = Workspace(job.workspace)
        with workspace_lock(ws):
            before = ws.head()
        files = self.writer(job, ws)
        for rel, content in (files or {}).items():
            p = ws.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        traj = ws.trajectory_dir(job.label or "job", 0)
        (traj / "transcript.jsonl").write_text(json.dumps({"prompt": job.prompt[:200]}) + "\n")
        return AgentResult(ok=bool(files), exit_reason="completed" if files else "no_changes", files_changed=changed_files_safe(ws, before),
                           transcript_path=str(traj / "transcript.jsonl"), usage=Usage(backend="fake", cost_usd=self.cost, input_tokens=100))


class FakeChatModel:
    """``responder(request) -> str | dict``; dict is returned as ``parsed`` (structured output)."""

    provider = "fake"
    model = "fake-model"

    def __init__(self, responder: Callable[[ChatRequest], Any], cost: float = 0.002):
        self.responder = responder
        self.cost = cost
        self.requests: list[ChatRequest] = []

    @property
    def id(self) -> str:
        return "fake:fake-model"

    def supports_vision(self) -> bool:
        return True

    def generate(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        out = self.responder(request)
        usage = Usage(backend="fake", model="fake-model", cost_usd=self.cost, input_tokens=500, output_tokens=200)
        if isinstance(out, (dict, list)):
            return ChatResponse(text=json.dumps(out), parsed=out, usage=usage)
        return ChatResponse(text=str(out), usage=usage)


# ----------------------------------------------------------------------------- judge
class FakeJudge:
    name = "fake"

    def __init__(self, scores: Sequence[float] = (0.55, 0.7, 0.85), *, targets: Sequence[str] = ("Seat", "FrontLeg", "Backrest", "Armrest"),
                 acceptance_fail: Sequence[str] = (), cost: float = 0.003):
        self.scores = list(scores)
        self.targets = list(targets)
        self.acceptance_fail = list(acceptance_fail)
        self.cost = cost
        self.calls: list[Any] = []

    def judge(self, inp: Any) -> Judgment:
        i = min(len(self.calls), len(self.scores) - 1)
        self.calls.append(inp)
        s = self.scores[i]
        plan = [ImprovementItem(target=t, kind="geometry", instruction=f"make {t} match plan bbox", priority=k + 1, expected_gain=0.05)
                for k, t in enumerate(self.targets)]
        acc = {a.id: (a.id not in self.acceptance_fail) for a in inp.acceptance}
        return Judgment(rubric="fake_v1", judge_backend="fake", scores={"geometry": s, "material": s}, overall=s, passed=s >= 0.8,
                        summary=f"round {inp.round_index} score {s}", issues=[JudgeIssue(target=self.targets[0], kind="geometry", severity="major",
                                                                                          detail="too thin", evidence="front")],
                        improvement_plan=plan, acceptance_results=acc, usage=Usage(backend="fake", cost_usd=self.cost))


# ----------------------------------------------------------------------------- services
def _png(path: Path, size: int = 32) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (size, size), (120, 120, 200)).save(path)


class FakeServices(Services):
    def __init__(self, *, runtime_factory: Callable[[Language], Any] | None = None, judge: Any | None = None,
                 contract_errors: int = 0, assemble: bool = False, sweep_errors: int = 0):
        self.runtime_factory = runtime_factory or (lambda lang: FakeRuntime(lang))
        self._judge = judge
        self.contract_errors = contract_errors
        self.assemble = assemble
        self.sweep_errors = sweep_errors
        self.materialized: list[str] = []
        self.records: list[RunRecord] = []

    def chat_model(self, model_id: str) -> Any:
        raise ServiceUnavailable("no chat model in tests")

    def coding_agent(self, agent_id: str) -> Any:
        raise ServiceUnavailable("no coding agent in tests")

    def judge(self, rubric: str, model_id: str, n_samples: int = 1) -> Any:
        return self._judge if self._judge is not None else FakeJudge()

    def runtime(self, language: Language) -> Any:
        return self.runtime_factory(language)

    def rubric_threshold(self, rubric: str) -> float | None:
        return 0.8

    def measure(self, glb: Path) -> Measurement:
        scene = trimesh.load(str(glb), force="scene")
        bounds = scene.bounds
        parts = []
        for name, geom in scene.geometry.items():
            b = geom.bounds
            parts.append(PartMeasure(name=name, bbox_min=tuple(map(float, b[0])), bbox_max=tuple(map(float, b[1])),
                                     tri_count=int(len(geom.faces))))
        ext = (bounds[1] - bounds[0]).tolist()
        cen = ((bounds[1] + bounds[0]) / 2).tolist()
        return Measurement(bbox_min=tuple(map(float, bounds[0])), bbox_max=tuple(map(float, bounds[1])), extents=tuple(ext),
                           center=tuple(cen), tri_count=sum(p.tri_count for p in parts), n_meshes=len(parts), n_islands=len(parts),
                           parts=parts, ground_gap_m=float(bounds[0][1]), footprint_offset_m=float(np.hypot(cen[0], cen[2])))

    def connectivity(self, glb: Path) -> GateReport:
        return GateReport(gate="connectivity", passed=True)

    def contract(self, measurement: Measurement, plan: Plan, tol_m: float, language: str = "") -> GateReport:
        findings = [GateFinding(gate="contract", severity=Severity.ERROR, target=p.name, message=f"{p.name} bbox off by 0.03 m",
                                fix_hint=f"move {p.name} to its plan centre") for p in list(getattr(plan, "parts", []))[: self.contract_errors]]
        return GateReport(gate="contract", passed=not findings, findings=findings)

    def render_object(self, glb: Path, out_dir: Path, *, views, width: int, height: int) -> RenderSet:
        vs = []
        for v in views:
            p = out_dir / f"view_{v.name}.png"
            _png(p)
            vs.append(RenderView(name=v.name, path=str(p), width=32, height=32))
        sheet = out_dir / "sheet.png"
        _png(sheet)
        return RenderSet(views=vs, contact_sheet=str(sheet), renderer="fake")

    def render_scene(self, ws: Workspace, out_dir: Path, *, cameras, times, width: int, height: int) -> RenderSet:
        vs = []
        for c in cameras or []:
            for t in times:
                p = out_dir / f"cam_{to_snake(c.name)}_t{t}.png"
                _png(p)
                vs.append(RenderView(name=f"{c.name}@{t}", path=str(p), time_s=t))
        sheet = out_dir / "sheet.png"
        _png(sheet)
        errs = ["TypeError: boom in src/zones/pond.js"] if (ws.src / "zones" / "pond.js").is_file() and "CONSOLE_ERROR" in (ws.src / "zones" / "pond.js").read_text() else []
        return RenderSet(views=vs, contact_sheet=str(sheet), renderer="fake", console_errors=errs, fps=60.0)

    def contact_sheet(self, images, out: Path) -> Path:
        _png(out)
        return out

    def joint_sweep(self, ws: Workspace, plan: Plan, out_dir: Path) -> tuple[GateReport, list[RenderView]]:
        out_dir.mkdir(parents=True, exist_ok=True)
        views = []
        for j in getattr(plan, "joints", []):
            for pose in ("lower", "upper"):
                p = out_dir / f"pose_{to_snake(j.name)}_{pose}.png"
                _png(p)
                views.append(RenderView(name=f"pose_{j.name}_{pose}", path=str(p)))
        findings = [GateFinding(gate="joint_sweep", severity=Severity.ERROR, target=j.name, message=f"{j.child} penetrates {j.parent} by 12.0 mm at upper",
                                fix_hint=f"shrink {j.child} by 17 mm along the axis") for j in list(getattr(plan, "joints", []))[: self.sweep_errors]]
        return GateReport(gate="joint_sweep", passed=not findings, findings=findings), views

    def materialize(self, ws: Workspace, *, agent_kind: str, contract_md: str, cookbook_rel: str, spatial_tools: bool, mcp_command: list[str]) -> None:
        (ws.root / "AGENTS.md").write_text(contract_md)
        self.materialized.append(agent_kind)

    def tool_cards(self, track: str, language: str) -> str:
        return "- `build` (slow): lint+build+measure\n- `measure` (fast): bbox per part"

    def assemble_scene(self, ws: Workspace, plan: Plan) -> Any:
        if not self.assemble:
            raise ServiceUnavailable("no assembler in tests")
        (ws.src / "scene.js").write_text("// assembled by fake\nexport function createScene(){}\n")
        return {"zones": [z.name for z in plan.zones]}

    def finalize_record(self, ws: Workspace, record: RunRecord) -> None:
        self.records.append(record)
        ws.write_json(ws.record_path, record)


def wait_for(pred: Callable[[], bool], timeout: float = 5.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return False
