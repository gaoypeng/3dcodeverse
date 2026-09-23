"""``tracks/skills_hook``: invisible when the switch is off, harmless when anything breaks."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

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
