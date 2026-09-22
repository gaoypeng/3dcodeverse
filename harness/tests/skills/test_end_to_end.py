"""One real track run with the switch on: attach → index → prompt → probe → record.

Every other test here exercises a piece.  This one runs the actual round loop (with the
repo's own fakes: no network, no Blender, no node) and asserts the four things a battery
depends on — the bundles reach the workspace, the index reaches the agent's prompt file,
the round record carries what was attached, and the previous round's gate findings change
what the next round gets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.config import Settings
from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse3d.contracts.common import Language
from codeverse3d.contracts.run import RunStatus
from codeverse3d.skills.materialize import MARK_BEGIN
from codeverse3d.skills.registry import ROUTED_SKILLS
from codeverse3d.tracks.static_object import StaticObjectTrack
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import chair_plan as _chair_plan
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeChatModel,
    FakeJudge,
    FakeRuntime,
    FakeServices,
)
from tests.orchestrator_tracks.test_tracks import _agent_writer
from tests.skills.conftest import write_bundle


class RealMessageServices(FakeServices):
    """FakeServices, but the contract gate speaks the words the real gate speaks.

    The stock fake says "Seat bbox off by 0.03 m", which ``finding_kind`` correctly
    refuses to classify — it is not a message our gates emit.  Routing off a gate is only
    proven by the real text."""

    def contract(self, measurement, plan, tol_m, language=""):
        parts = list(getattr(plan, "parts", []))[: self.contract_errors]
        findings = [GateFinding(gate="contract", severity=Severity.ERROR, target=p.name,
                                message=f"part '{p.name}' bbox deviates from the plan (worst 3.4x tolerance)",
                                fix_hint=f"resize {p.name} to its plan extents") for p in parts]
        return GateReport(gate="contract", passed=not findings, findings=findings)


class RepairOnceRuntime(FakeRuntime):
    """A real lint-shaped failure that the baseline's repair session must consume."""

    def lint(self, ws):
        if "SHADER_LINT_ERROR" in self._src_text(ws):
            finding = GateFinding(
                gate="lint:threejs", severity=Severity.ERROR, target="src/object.js",
                message="shader compile error: unbound uniform u_time",
                fix_hint="bind u_time before compiling the material",
            )
            return GateReport(gate="lint:threejs", passed=False, findings=[finding])
        return super().lint(ws)


def _repairing_writer(job, ws):
    files = _agent_writer(job, ws)
    if job.kind == "baseline":
        files["src/object.js"] += "// SHADER_LINT_ERROR\n"
    return files


@pytest.fixture(autouse=True)
def _no_brief(monkeypatch):
    """The fake planner answers one canned plan; the optional brief call would eat it."""
    monkeypatch.setenv("C3D_PLAN_BRIEF", "off")


@pytest.fixture
def chair_plan():
    return _chair_plan.__wrapped__()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(runs_dir=tmp_path / "runs", cache_dir=tmp_path / "cache")


@pytest.fixture
def run(tmp_path: Path, chair_plan, settings, monkeypatch):
    lib = tmp_path / "library"
    for name in ROUTED_SKILLS:
        write_bundle(lib, name)
    monkeypatch.setenv("C3D_SKILLS", "on")
    monkeypatch.setenv("C3D_SKILLS_DIR", str(lib))
    chair_plan.summary += " with a custom shader material"
    ws = Workspace(tmp_path / "runs" / "chair")
    agent = FakeAgent(_repairing_writer)
    track = StaticObjectTrack(services=RealMessageServices(contract_errors=1), judge=FakeJudge(scores=(0.55, 0.7, 0.85)),
                              agent=agent, planner_model=FakeChatModel(lambda req: chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=RepairOnceRuntime(Language.THREEJS))
    rec = track.run(make_spec(max_rounds=3), ws)
    assert rec.status is RunStatus.PASSED
    return rec, ws, agent


def test_enabled_run_delivers_routes_repairs_and_records_skills(run):
    rec, ws, agent = run
    for root in (".agents/skills", ".claude/skills"):
        names = sorted(p.name for p in (ws.root / root).iterdir())
        assert names and all((ws.root / root / n / "SKILL.md").is_file() for n in names)

    body = (ws.root / "AGENTS.md").read_text()
    assert MARK_BEGIN in body and "MANDATORY" in body

    for r in rec.rounds:
        assert r.skills is not None, r.kind
        assert r.skills.listed and len(r.skills.listed) <= 5
        assert r.skills.index_tokens > 0
        assert r.skills.deep_read_rate == 0.0  # the fake agent reads nothing — and we can SEE that
        assert {x.name for x in r.skills.reads} == set(r.skills.listed)

    baseline = rec.rounds[0].skills.listed
    refine = rec.rounds[1].skills.listed
    # R11/R3: a threejs baseline gets the standing form + contact sheets, with no gate to react to
    assert "c3d-threejs-forms" in baseline
    assert not any(x.reason.startswith("contract/") for x in rec.rounds[0].skills.reads)
    assert "c3d-bbox-contract" in refine            # R4: the contract gate fired in round 0
    reasons = {x.name: x.reason for x in rec.rounds[1].skills.reads}
    assert "contract/" in reasons["c3d-bbox-contract"]

    keys = [k for k in rec.prompt_hashes if k.startswith("skill:")]
    assert keys and all(rec.prompt_hashes[k] for k in keys)
    assert {k.split(":", 1)[1] for k in keys} >= set(rec.rounds[0].skills.listed)

    rows = (ws.root / "telemetry" / "skills.jsonl").read_text().splitlines()
    assert len(rows) == 3 and all('"listed"' in r for r in rows)

    repair = next(job for job in agent.jobs if job.kind == "repair")
    assert "c3d-threejs-shader-traps" in repair.prompt
    assert rec.rounds[0].notes.startswith("repair attempts: 1/2 (fixed)")


def test_the_switch_off_leaves_no_trace(tmp_path, chair_plan, settings, monkeypatch):
    monkeypatch.setenv("C3D_SKILLS", "0")   # ON is the default since 2026-09-22; off must be said
    ws = Workspace(tmp_path / "runs" / "chair_off")
    track = StaticObjectTrack(services=FakeServices(contract_errors=1), judge=FakeJudge(scores=(0.55, 0.7, 0.85)),
                              agent=FakeAgent(_agent_writer),
                              planner_model=FakeChatModel(lambda req: chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.THREEJS))
    rec = track.run(make_spec(max_rounds=3), ws)
    assert all(r.skills is None for r in rec.rounds)
    assert not (ws.root / ".agents").exists() and not (ws.root / "telemetry" / "skills.jsonl").exists()
    assert MARK_BEGIN not in (ws.root / "AGENTS.md").read_text()
