"""``tracks/skills_hook``: the round's view of the skill system, and its failure modes.

The hook is where a bug would be most expensive: it runs inside every round, and its job
is to be invisible when the switch is off and harmless when anything goes wrong.  So the
tests below spend most of their attention on the off path and the broken path.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
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
    assert H.repair_pointers(ctx, None) == ""


def test_the_switch_on_attaches_records_and_measures(ctx, monkeypatch):
    monkeypatch.setenv("C3D_SKILLS", "on")
    got = H.attach_for_round(ctx, index=0, kind="baseline")
    assert got and got.listed
    assert (ctx.ws.root / ".agents" / "skills" / got.listed[0] / "SKILL.md").is_file()
    assert ctx.events[0][0] == "skills.attached"
    assert any(k.startswith("skill:") for k in ctx.prompt_hashes)
    usage = H.record_usage(ctx, index=0, kind="baseline")
    assert usage is not None and usage.listed == got.listed and usage.deep == []
    assert (ctx.ws.root / "telemetry" / "skills.jsonl").is_file()
    assert ctx.events[-1][0] == "skills.read"


def test_the_previous_rounds_findings_route_the_repair_sheet(ctx, monkeypatch):
    monkeypatch.setenv("C3D_SKILLS", "on")
    rounds = ctx.ws.root / "rounds"
    rounds.mkdir()
    rec = {"index": 0, "kind": "baseline", "gates": [GateReport(
        gate="connectivity", passed=False,
        findings=[GateFinding(gate="connectivity", severity=Severity.ERROR,
                              message="part 'Leg' is floating: nearest supported part is 'Seat' at 4.0 mm")],
    ).model_dump(mode="json")]}
    import json

    (rounds / "r00.json").write_text(json.dumps(rec))
    got = H.attach_for_round(ctx, index=1, kind="repair")
    assert "c3d-part-contact" in got.listed
    assert "connectivity/floating_part" in got.reasons["c3d-part-contact"]
    assert got.selections[0].gate_fired


def test_round_zero_never_looks_for_a_previous_round(ctx, monkeypatch):
    monkeypatch.setenv("C3D_SKILLS", "on")
    got = H.attach_for_round(ctx, index=0, kind="baseline")
    assert all(not s.gate_fired for s in got.selections)


def test_repair_pointers_name_the_skill_that_answers_the_current_lint(ctx, monkeypatch):
    monkeypatch.setenv("C3D_SKILLS", "on")
    H.attach_for_round(ctx, index=0, kind="repair")
    lint = GateReport(gate="lint:blender", passed=False, findings=[GateFinding(
        gate="lint:blender", severity=Severity.WARN,
        message="src/parts/trigger.py is never imported by src/model.py → its part is not built")])
    text = H.repair_pointers(ctx, lint)
    assert "c3d-blender-forms" in text


def test_single_shot_inlines_one_body_into_every_task(ctx, monkeypatch):
    monkeypatch.setenv("C3D_SKILLS", "on")
    ctx.single_shot = True
    got = H.attach_for_round(ctx, index=0, kind="baseline")
    assert got.inlined and got.paths == []

    class Task:
        def __init__(self, prompt):
            self.prompt = prompt

        def model_copy(self, *, update):
            return Task(update["prompt"])

    out = H.with_inlined_skill(ctx, [Task("write the chair")])
    assert out[0].prompt.endswith("write the chair") and got.inlined in out[0].prompt


def test_an_agent_session_never_gets_an_inlined_body(ctx, monkeypatch):
    monkeypatch.setenv("C3D_SKILLS", "on")
    H.attach_for_round(ctx, index=0, kind="baseline")
    tasks = [NS(prompt="p")]
    assert H.with_inlined_skill(ctx, tasks) == tasks


def test_a_broken_library_costs_the_skills_not_the_round(ctx, monkeypatch, caplog):
    monkeypatch.setenv("C3D_SKILLS", "on")
    monkeypatch.setenv("C3D_SKILLS_DIR", "/definitely/not/a/directory")
    with caplog.at_level("WARNING"):
        got = H.attach_for_round(ctx, index=0, kind="baseline")
    assert got is not None and got.listed == []  # routed nothing, wrote nothing, raised nothing


def test_a_failed_attach_clears_the_previous_rounds_set(ctx, monkeypatch):
    """Otherwise round N+1 probes round N's bundles and reports reads it never earned."""
    monkeypatch.setenv("C3D_SKILLS", "on")
    assert H.attach_for_round(ctx, index=0, kind="baseline")
    monkeypatch.setattr("codeverse3d.skills.attach_skills",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert H.attach_for_round(ctx, index=1, kind="refine") is None
    assert H.record_usage(ctx, index=1, kind="refine") is None


def test_a_probe_failure_is_logged_not_raised(ctx, monkeypatch):
    monkeypatch.setenv("C3D_SKILLS", "on")
    H.attach_for_round(ctx, index=0, kind="baseline")
    monkeypatch.setattr("codeverse3d.skills.telemetry.probe_reads", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert H.record_usage(ctx, index=0, kind="baseline") is None
