"""``tracks/skills_hook``: invisible when the switch is off, harmless when anything breaks."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import BuildResult, GateFinding, GateReport, Severity
from codeverse3d.tracks import skills_hook as H


class Events(list):
    def emit(self, name, **kw):
        self.append((name, kw))


@pytest.fixture
def ctx(tmp_path: Path, library_dir: Path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    for name in ("AGENTS.md", "GEMINI.md", "CLAUDE.md"):
        (root / name).write_text("# body\n")
    monkeypatch.setenv("C3D_SKILLS_DIR", str(library_dir))
    parts = [NS(name=f"P{i}", instances=1, symmetry="none", children=[]) for i in range(3)]
    hashes: dict[str, str] = {}
    return NS(ws=NS(root=root), spec=NS(track=NS(value="static_object")), language=NS(value="blender"),
              agent_id="unknown-backend:gemini-3.7-flash", agent_kind="unknown-backend", plan=NS(parts=parts, summary="a chair"),
              single_shot=False, extra={}, events=Events(), prompt_hashes=hashes,
              record_prompt=lambda name, text: hashes.__setitem__(name, str(len(text))))


def test_the_switch_off_means_no_files_no_events_no_record(ctx, monkeypatch):
    monkeypatch.setenv("C3D_SKILLS", "0")   # ON is the default since 2026-09-22; off must be said
    assert H.attach_for_round(ctx, index=0, kind="baseline") is None
    assert not (ctx.ws.root / ".agents").exists()
    assert ctx.events == [] and ctx.extra == {}
    assert H.record_usage(ctx, index=0, kind="baseline") is None
    assert H.repair_pointers(ctx, BuildResult(ok=True, language="blender"), GateReport(gate="lint:blender", passed=True)) == ""


def test_no_skill_failure_costs_the_round(ctx, monkeypatch):
    monkeypatch.setenv("C3D_SKILLS", "on")
    assert H.attach_for_round(ctx, index=0, kind="baseline")
    # a probe failure is logged, not raised
    with monkeypatch.context() as m:
        m.setattr("codeverse3d.skills.telemetry.probe_reads", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
        assert H.record_usage(ctx, index=0, kind="baseline") is None
    # a failed attach clears the previous round's set — otherwise round N+1 probes round N's
    # bundles and reports reads it never earned
    with monkeypatch.context() as m:
        m.setattr("codeverse3d.skills.attach_skills", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert H.attach_for_round(ctx, index=1, kind="refine") is None
    assert H.record_usage(ctx, index=1, kind="refine") is None
    # a broken library costs the skills, not the round: routed nothing, wrote nothing, raised nothing
    monkeypatch.setenv("C3D_SKILLS_DIR", "/definitely/not/a/directory")
    get_settings.cache_clear()
    got = H.attach_for_round(ctx, index=2, kind="refine")
    assert got is not None and got.listed == []


def _report(gate: str, kind: str) -> GateReport:
    return GateReport(gate=gate, passed=False, findings=[GateFinding(gate=gate, severity=Severity.ERROR, message="m",
                                                                     data={"kind": kind})])


@pytest.mark.parametrize(("track", "language", "lint", "build_gates", "sheet"), [
    # the lint is found after the round attached its set: R9 points at the sheet
    ("static_object", "blender", _report("lint:blender", "part_not_imported"), [], "c3d-blender-forms"),
    # a scene's shader error is the BUILD's own report, not the lint's: R21 must see it too
    ("scene", "scene_threejs", GateReport(gate="lint:scene_threejs", passed=True),
     [_report("shader_preflight", "compile_error")], "c3d-threejs-shader-traps"),
])
def test_a_repair_names_the_sheet_that_answers_the_failure_it_is_fixing(ctx, monkeypatch, track, language, lint,
                                                                        build_gates, sheet):
    monkeypatch.setenv("C3D_SKILLS", "on")
    ctx.spec.track.value, ctx.language.value = track, language
    ctx.plan.effects = [NS(kind="shader", description="a custom water shader")]
    assert H.attach_for_round(ctx, index=0, kind="repair")
    build = BuildResult(ok=not build_gates, language=language, gates=build_gates)
    assert sheet in H.repair_pointers(ctx, build, lint)
