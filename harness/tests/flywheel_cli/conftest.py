"""Shared fixtures: a synthetic run (git repo with two commits, tiny PNGs, each round's
kept GLB under artifacts/rNN/, record.json with two judged rounds) — no models, Blender or
node required."""

from __future__ import annotations

import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    ImprovementItem,
    JudgeIssue,
    Judgment,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse3d.contracts.common import Backends, Language, Track, Usage
from codeverse3d.contracts.run import RoundRecord, RunRecord, RunStatus, StepTime
from codeverse3d.contracts.spec import Spec
from codeverse3d.cost.ledger import record_call
from codeverse3d.workspace import Workspace


def tiny_png(path: Path, rgb: tuple[int, int, int] = (200, 30, 30), size: int = 4) -> Path:
    """Write a valid size×size RGB PNG without PIL."""
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return path


def _judgment(score: float, passed: bool, plan: list[str]) -> Judgment:
    return Judgment(
        rubric="static_object_v1", judge_backend="gemini:gemini-3.7-flash",
        scores={"fidelity": score, "geometry": score}, overall=score, passed=passed, summary=f"score {score}",
        issues=[JudgeIssue(target="Leg", kind="geometry", severity="major", detail="legs too thin")] if not passed else [],
        improvement_plan=[ImprovementItem(target="Leg", kind="geometry", instruction=s, priority=1) for s in plan],
        acceptance_results={"a1": passed}, usage=Usage(backend="gemini", model="gemini-3.7-flash", cost_usd=0.01),
    )


def make_fake_run(
    runs_dir: Path, slug: str = "wooden_chair_ab12cd34", *, prompt: str = "a wooden dining chair",
    language: Language = Language.BLENDER, generator: str = "gemini-cli:gemini-3.6-flash",
    scores: tuple[float, float] = (0.55, 0.80), with_repair: bool = False, code_v2: str | None = None,
) -> tuple[Workspace, RunRecord]:
    ws = Workspace(runs_dir / slug).create()
    track = Track.SCENE if language is Language.SCENE_THREEJS else (
        Track.ARTICULATED_OBJECT if language is Language.URDF_BLENDER else (
            Track.GRAPHICS if language in (Language.GLSL_SHADER, Language.OPENGL_PYTHON) else Track.STATIC_OBJECT))
    spec = Spec(id=slug, track=track, language=language, prompt=prompt, backends=Backends(generator=generator))
    ws.write_json(ws.spec_path, spec)
    entry = {"blender": "src/model.py", "cadquery": "src/model.py", "urdf_blender": "src/model.py",
             "threejs": "src/object.js", "scene_threejs": "src/scene.js",
             "glsl_shader": "src/shader.frag", "opengl_python": "src/program.py"}[language.value]
    e = ws.root / entry
    e.parent.mkdir(parents=True, exist_ok=True)
    e.write_text("# round 0\nimport bpy  # comment\n\nbpy.ops.mesh.primitive_cube_add(size=1.0)\n")
    if language is Language.URDF_BLENDER:
        (ws.src / "robot.urdf").write_text("<robot name='x'><link name='base'/></robot>")
    c0 = ws.commit("round 0")
    rounds = []
    for i, sc in enumerate(scores):
        if i == 1:
            e.write_text(code_v2 or "# round 1\nimport bpy\n\nbpy.ops.mesh.primitive_cube_add(size=2.0)\n")
            (ws.src / "parts").mkdir(exist_ok=True)
            (ws.src / "parts" / "leg.py").write_text("LEG = 0.04\n")
        commit = c0 if i == 0 else ws.commit(f"round {i}")
        kept = ws.round_artifacts(i)
        kept.mkdir(parents=True, exist_ok=True)
        (kept / "object.glb").write_bytes(b"glTF\x02\x00\x00\x00" + bytes([i]) * 16)
        rd = ws.renders_dir(i)
        views = [RenderView(name=n, path=str(tiny_png(rd / f"view_{n}.png"))) for n in ("front", "top")]
        sheet = tiny_png(rd / "sheet.png", (0, 0, 200))
        rounds.append(RoundRecord(
            index=i, kind="baseline" if i == 0 else "refine", commit=commit, agent_backend=generator,
            instructions=[] if i == 0 else ["thicken the legs"],
            build=BuildResult(ok=True, language=language.value, glb_path=str(ws.artifacts / "object.glb")),
            gates=[GateReport(gate="lint", passed=True)],
            renders=RenderSet(views=views, contact_sheet=str(sheet)),
            judgment=_judgment(sc, sc >= 0.75, ["thicken the legs", "add a back rail"]),
            usage=Usage(backend="gemini", model="gemini-3.7-flash", input_tokens=1000, output_tokens=500, cost_usd=0.02),
            duration_s=12.0,
        ))
    if with_repair:
        e.write_text("import bpy\nbpy.ops.mesh.primitive_cube_add(size=\n")
        bad = ws.commit("broken")
        rounds.append(RoundRecord(
            index=len(rounds), kind="refine", commit=bad,
            build=BuildResult(ok=False, language=language.value, error_type="SyntaxError",
                              error_message="unexpected EOF", error_file="src/model.py", error_line=2,
                              stderr_tail="SyntaxError: unexpected EOF while parsing"),
            gates=[GateReport(gate="lint", passed=False, findings=[GateFinding(gate="lint", severity=Severity.ERROR, message="syntax")])],
        ))
        e.write_text("import bpy\nbpy.ops.mesh.primitive_cube_add(size=3.0)\n")
        fixed = ws.commit("repair")
        rounds.append(RoundRecord(
            index=len(rounds), kind="repair", commit=fixed, instructions=["fix the syntax error at line 2"],
            build=BuildResult(ok=True, language=language.value),
        ))
    (ws.artifacts / "object.glb").write_bytes(b"glTF\x02\x00\x00\x00" + bytes([len(scores) - 1]) * 16)  # the last round
    # the ledger its calls wrote: the planner, then one session per round ($0.06, the record's total)
    ledger = ws.telemetry / "cost.jsonl"
    record_call(Usage(backend="gemini", model="gemini-3.7-flash", input_tokens=500, cost_usd=0.02),
                run=slug, stage="plan", label="planner", ledger=ledger)
    for r in rounds[:2]:
        record_call(r.usage, run=slug, round=r.index, stage=r.kind, label=r.kind, source="session", ledger=ledger)
    rec = RunRecord(spec=spec, workspace=str(ws.root), status=RunStatus.MAX_ROUNDS, rounds=rounds,
                    total_usage=Usage(cost_usd=0.06, input_tokens=2000, output_tokens=1000),
                    steps=[StepTime(step="plan", wall_s=30.0, lost_s=6.0)], finished_at=datetime.now(UTC),
                    environment={"blender": "Blender 5.0.1"})
    ws.write_json(ws.record_path, rec)
    return ws, rec


@pytest.fixture
def fake_run(tmp_path: Path) -> tuple[Workspace, RunRecord]:
    return make_fake_run(tmp_path / "runs")


@pytest.fixture
def runs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "runs"
    make_fake_run(d)
    make_fake_run(d, "wooden_chair_codex", generator="codex:gpt-5.6-sol", scores=(0.5, 0.6), with_repair=True)
    make_fake_run(d, "lamp_three", prompt="a desk lamp", language=Language.THREEJS, scores=(0.7, 0.72))
    return d
