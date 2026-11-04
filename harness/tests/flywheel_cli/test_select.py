"""``codeverse3d.addons.select``: which round of a finished run is handed over, and how
(``round_rows`` / ``pick`` / ``summarise`` / ``package``, ``3dcode pick``, ``make --no-pick``)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from codeverse3d.addons import select
from codeverse3d.cli.main import app
from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    Judgment,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse3d.contracts.common import Backends, Language, Track, Usage
from codeverse3d.contracts.run import RoundRecord, RunRecord, RunStatus
from codeverse3d.contracts.spec import Spec
from codeverse3d.workspace import Workspace

from .conftest import tiny_png

runner = CliRunner()


def _run(root: Path, rounds: list[tuple[float | str | None, int]], *, status: RunStatus = RunStatus.MAX_ROUNDS) -> Workspace:
    """A finished run whose round i scored ``rounds[i][0]`` (None = unjudged, "degraded" = a
    judge glitch) with ``rounds[i][1]`` gate errors; every round kept its own GLB."""
    ws = Workspace(root).create()
    spec = Spec(id=root.name, track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="a stool",
                backends=Backends(judge="fake:judge"))
    ws.write_json(ws.spec_path, spec)
    recs = []
    for i, (score, errors) in enumerate(rounds):
        (ws.src / "model.py").write_text(f"import bpy  # round {i}\n")
        commit = ws.commit(f"r{i:02d}")
        views = [RenderView(name="front", path=str(tiny_png(ws.renders_dir(i) / "view_front.png")))]
        kept = ws.round_artifacts(i) / "object.glb"
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(b"glTF\x02\x00\x00\x00" + bytes([i]) * 16)
        if score == "degraded":  # a judge glitch: paid for, never a score
            j = Judgment(rubric="static_object", scores={}, overall=0.0, passed=False,
                         summary="judge_error: 503", raw=json.dumps({"status": "degraded"}))
        else:
            j = None if score is None else Judgment(rubric="static_object", scores={}, overall=score, passed=score >= 0.72)
        gates = [GateReport(gate="contract", passed=not errors,
                            findings=[GateFinding(gate="contract", severity=Severity.ERROR, message="off")] * errors)]
        recs.append(RoundRecord(index=i, kind="baseline" if i == 0 else "refine", commit=commit, judgment=j, gates=gates,
                                build=BuildResult(ok=True, language="blender"), duration_s=90.0,
                                renders=RenderSet(views=views, contact_sheet=str(tiny_png(ws.renders_dir(i) / "sheet.png"))),
                                usage=Usage(cost_usd=0.1 * (i + 1))))
    ws.write_json(ws.record_path, RunRecord(spec=spec, workspace=str(ws.root), status=status, rounds=recs,
                                            extra={"stop_reason": status.value, "rubric": "static_object"}))
    return ws


def test_an_unjudged_run_shows_one_fallback_round_in_every_reader(tmp_path, monkeypatch):
    """N55b: nothing judged → every reader shows ``select.fallback_round``, the latest round that
    built AND has renders — here r0 (r1 failed to build but kept renders, r2 built with no
    renders).  Before, five readers gave four answers (2, None, r02's sheet, 1, 2)."""
    from codeverse3d.addons.calibration import load_run_cases
    from codeverse3d.addons.dataset.sample import exported_round
    from codeverse3d.addons.gallery.index import entry_from_record
    from codeverse3d.cli import _judge as J
    from codeverse3d.record.record import load_record

    ws = _run(tmp_path / "u", [(None, 2), (None, 0), (None, 0)])
    rec = load_record(ws)
    rec.rounds[1].build = BuildResult(ok=False, language="blender")
    rec.rounds[2].renders = None
    ws.write_json(ws.record_path, rec)
    (ws.root / "rounds").mkdir()
    for r in rec.rounds:
        ws.write_json(ws.root / "rounds" / f"r{r.index:02d}.json", r)

    s = select.summarise(ws.root, record=rec)
    assert (s.picked_round, s.round) == (None, 0)
    assert exported_round(ws, rec).index == 0
    card = entry_from_record("b", ws, rec)
    assert (card.picked_round, card.gate_errors, card.sheet) == (0, 2, "artifacts/renders/r00/sheet.png")
    assert [c.round_index for c in load_run_cases(ws.root) if c.is_picked] == [0]
    asked: list[int] = []
    monkeypatch.setattr(J, "load_round", lambda ws, rec, idx: asked.append(idx))
    runner.invoke(app, ["judge", str(ws.root)])
    assert asked == [0]                                           # `3dcode judge`'s default round


class _Pairwise:
    def __init__(self, winner: str, confidence: float, *, fail: bool = False):
        self.winner, self.confidence, self.fail, self.calls = winner, confidence, fail, []

    def compare(self, spec, renders_a, renders_b, *, rubric):
        self.calls.append((renders_a.views[0].path, renders_b.views[0].path, rubric))
        if self.fail:
            raise RuntimeError("judge down")
        return SimpleNamespace(winner=self.winner, confidence=self.confidence, reasons=["r"], usage=Usage(cost_usd=0.05),
                               error="")


# --------------------------------------------------------------------------- package
def test_a_scene_or_graphics_round_packages_from_its_own_commit_and_renders(tmp_path):
    """A scene or graphics hand-over of round 0 of two is round 0's bytes, never the last's."""
    for track, language, entry in ((Track.SCENE, Language.SCENE_THREEJS, "src/scene.js"),
                                   (Track.GRAPHICS, Language.GLSL_SHADER, "src/shader.frag")):
        ws = Workspace(tmp_path / language.value).create()
        spec = Spec(id=language.value, track=track, language=language, prompt="p")
        recs = []
        for i in range(2):
            (ws.root / entry).parent.mkdir(parents=True, exist_ok=True)
            (ws.root / entry).write_text(f"// round {i}\n")
            if track is Track.SCENE:
                (ws.root / "public" / "assets").mkdir(parents=True, exist_ok=True)
                (ws.root / "public" / "assets" / "tree.glb").write_bytes(b"glTF tree " + bytes([i]))
            else:
                kept = ws.round_artifacts(i)
                kept.mkdir(parents=True, exist_ok=True)
                (kept / "preview.gif").write_bytes(b"GIF89a" + bytes([i]))
                tiny_png(kept / "frames_sheet.png", (i, 0, 0))
            views = [RenderView(name=f"t{t}", path=str(tiny_png(ws.renders_dir(i) / f"f{t:02d}_t{t}.png", (i, t, 0))))
                     for t in range(2)]
            recs.append(RoundRecord(index=i, kind="baseline" if i == 0 else "refine", commit=ws.commit(f"r{i:02d}"),
                                    build=BuildResult(ok=True, language=language.value),
                                    renders=RenderSet(views=views, contact_sheet=str(tiny_png(ws.renders_dir(i) / "sheet.png"))),
                                    judgment=Judgment(rubric="r", scores={}, overall=0.5 + i / 10, passed=False)))
        ws.write_json(ws.record_path, RunRecord(spec=spec, workspace=str(ws.root), status=RunStatus.MAX_ROUNDS, rounds=recs))
        out = select.package(ws.root, 0)
        assert (out / entry).read_text() == "// round 0\n"
        if track is Track.SCENE:
            assert (out / "public" / "assets" / "tree.glb").read_bytes() == b"glTF tree \x00"
        else:
            assert sorted(q.name for q in (out / "frames").iterdir()) == ["f00_t0.png", "f01_t1.png"]
            assert (out / "frames" / "f01_t1.png").read_bytes() == (ws.renders_dir(0) / "f01_t1.png").read_bytes()
            assert (out / "preview.gif").read_bytes() == b"GIF89a\x00"
            assert (out / "frames_sheet.png").read_bytes() == (ws.round_artifacts(0) / "frames_sheet.png").read_bytes()


# --------------------------------------------------------------------------- CLI
def test_pick_command_by_score_by_round_and_by_pairwise(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    ws = _run(runs / "stool", [(0.70, 0), (0.72, 0), (0.50, 0)])
    r = runner.invoke(app, ["pick", "stool", "--runs-dir", str(runs)])
    assert r.exit_code == 0, r.output
    assert "picked round 1" in r.output and json.loads((ws.root / "selection.json").read_text())["round"] == 1
    r = runner.invoke(app, ["pick", "stool", "--runs-dir", str(runs), "--round", "2"])
    assert r.exit_code == 0 and json.loads((ws.root / "selection.json").read_text())["method"] == "round"
    assert (ws.deliverable / "src" / "model.py").read_text() == "import bpy  # round 2\n"

    import codeverse3d.judges.pairwise as pw

    monkeypatch.setattr(pw, "PairwiseJudge", lambda model: _Pairwise("b", 0.95))
    r = runner.invoke(app, ["pick", "stool", "--runs-dir", str(runs), "--by", "pairwise", "--judge", "fake:pairwise"])
    assert r.exit_code == 0, r.output
    sel = json.loads((ws.root / "selection.json").read_text())
    assert (sel["round"], sel["method"]) == (0, "pairwise")
    assert runner.invoke(app, ["pick", "stool", "--runs-dir", str(runs), "--by", "vibes"]).exit_code == 2
    assert runner.invoke(app, ["pick", "stool", "--runs-dir", str(runs), "--round", "9"]).exit_code == 2

    _run(runs / "blank", [(None, 0)])
    r = runner.invoke(app, ["pick", "blank", "--runs-dir", str(runs)])
    assert r.exit_code == 1 and "no round was judged" in r.output


def test_make_hands_over_the_picked_round_unless_no_pick(tmp_path, monkeypatch):
    import codeverse3d.tracks as tracks_pkg

    class _Track:
        def run(self, spec, ws, *, resume=False, force=False):
            done = _run(ws.root, [(0.6, 0), (0.9, 0), (0.7, 0)])
            return RunRecord.model_validate_json(done.record_path.read_text())

    monkeypatch.setattr(tracks_pkg, "get_track", lambda track, **options: _Track())
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "a stool", "--runs-dir", str(runs), "--slug", "picked", "--language", "blender"])
    assert r.exit_code == 0, r.output
    assert json.loads((runs / "picked" / "selection.json").read_text())["round"] == 1
    assert (runs / "picked" / "deliverable" / "src" / "model.py").read_text() == "import bpy  # round 1\n"
    r = runner.invoke(app, ["make", "a stool", "--runs-dir", str(runs), "--slug", "kept", "--language", "blender",
                            "--no-pick"])
    assert r.exit_code == 0, r.output
    assert not (runs / "kept" / "selection.json").exists() and not (runs / "kept" / "deliverable" / "manifest.json").exists()


@pytest.mark.parametrize("rounds,want", [
    ([(0.50, 0), (0.70, 0), (0.60, 0)], 1),            # the highest judged score
    ([(0.70, 2), (0.70, 0)], 1),                       # a tie: fewer gate errors
    ([(0.70, 1), (0.70, 1), (0.65, 0)], 0),            # a full tie: the earlier round
    ([(0.40, 0), ("degraded", 0), (None, 0)], 0),      # a glitch or no verdict is never a score
    ([(None, 0), ("degraded", 0)], None),              # no judged round: nothing to pick by score
], ids=["score", "gate-errors", "earlier", "unjudged", "none-judged"])
def test_pick_takes_the_best_score_then_fewer_gate_errors_then_the_earlier_round(tmp_path, rounds, want):
    assert select.pick(_run(tmp_path / "r", rounds).root) == want


def test_round_outputs_are_the_rounds_own_never_the_last_builds(tmp_path: Path):
    """A reader asking for round N's GLB or complexity once got the last build's (B4, B6)."""
    ws = _run(tmp_path / "r", [(0.8, 0), (0.6, 0)])
    rec = RunRecord.model_validate_json(ws.record_path.read_text())
    (ws.artifacts / "object.glb").write_bytes(b"last")
    ws.write_json(ws.artifacts / "measurement.json", {"extra": {"complexity": {"index": 0.9}}})
    assert select.round_file(ws, rec.rounds[0]) == ws.round_artifacts(0) / "object.glb"
    assert select.round_file(ws, rec.rounds[0], "preview.gif") is None
    assert select.round_complexity_block(ws, rec.rounds[0]) is None  # never the canonical file's
    # Missing snapshots cannot borrow the canonical build, even with an obsolete best_round key.
    for i in (0, 1):
        (ws.round_artifacts(i) / "object.glb").unlink()
        ws.round_artifacts(i).rmdir()
    data = json.loads(ws.record_path.read_text())
    ws.write_json(ws.record_path, {**data, "best_round": 1})
    assert select.round_file(ws, rec.rounds[0]) is None
    assert select.round_file(ws, rec.rounds[1]) is None
    assert select.round_complexity_block(ws, rec.rounds[1]) is None
