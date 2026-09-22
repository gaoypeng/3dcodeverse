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
from codeverse3d.proc import EventLog
from codeverse3d.workspace import Workspace

from .conftest import make_fake_run, tiny_png

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
            j = Judgment(rubric="static_object_v1", scores={}, overall=0.0, passed=False,
                         summary="judge_error: 503", raw=json.dumps({"status": "degraded"}))
        else:
            j = None if score is None else Judgment(rubric="static_object_v1", scores={}, overall=score, passed=score >= 0.72)
        gates = [GateReport(gate="contract", passed=not errors,
                            findings=[GateFinding(gate="contract", severity=Severity.ERROR, message="off")] * errors)]
        recs.append(RoundRecord(index=i, kind="baseline" if i == 0 else "refine", commit=commit, judgment=j, gates=gates,
                                build=BuildResult(ok=True, language="blender"), duration_s=90.0,
                                renders=RenderSet(views=views, contact_sheet=str(tiny_png(ws.renders_dir(i) / "sheet.png"))),
                                usage=Usage(cost_usd=0.1 * (i + 1))))
    ws.write_json(ws.record_path, RunRecord(spec=spec, workspace=str(ws.root), status=status, rounds=recs,
                                            extra={"stop_reason": status.value, "rubric": "static_object_v1"}))
    return ws


# --------------------------------------------------------------------------- pick
@pytest.mark.parametrize("rounds,want", [
    ([(0.50, 0), (0.70, 0), (0.60, 0)], 1),            # the highest judged score
    ([(0.70, 2), (0.70, 0)], 1),                       # a tie: fewer gate errors
    ([(0.70, 1), (0.70, 1), (0.65, 0)], 0),            # a full tie: the earlier round
    ([(0.40, 0), ("degraded", 0), (None, 0)], 0),      # a glitch or no verdict is never a score
    ([(None, 0), ("degraded", 0)], None),              # no judged round: nothing to pick by score
], ids=["score", "gate-errors", "earlier", "unjudged", "none-judged"])
def test_pick_takes_the_best_score_then_fewer_gate_errors_then_the_earlier_round(tmp_path, rounds, want):
    assert select.pick(_run(tmp_path / "r", rounds).root) == want


def test_round_rows_carry_the_judges_own_verdict_per_round(tmp_path):
    rows = select.round_rows(_run(tmp_path / "r", [(0.5, 0), (0.8, 2), ("degraded", 0)]).root)
    assert [(r.index, r.score, r.passed, r.gate_errors) for r in rows] == [
        (0, 0.5, False, 0), (1, 0.8, True, 2), (2, None, None, 0)]
    assert rows[1].commit and rows[1].minutes == 1.5 and rows[1].cost_usd == pytest.approx(0.2)


def test_summarise_is_baseline_pick_delta_rounds_and_the_stop_reason(tmp_path):
    ws = _run(tmp_path / "r", [(0.5, 0), (0.8, 0), (0.6, 0)], status=RunStatus.BUDGET)
    s = select.summarise(ws.root)
    assert (s.rounds, s.stop_reason, s.baseline_score, s.picked_round, s.picked_score, s.method) == (
        3, "budget", 0.5, 1, 0.8, "score")
    assert s.delta == pytest.approx(0.3)
    # once a round is handed over, that round is the run's pick — whichever way it was chosen
    select.package(ws.root, 2, method="round")
    s = select.summarise(ws.root)
    assert (s.picked_round, s.picked_score, s.method) == (2, 0.6, "round") and s.delta == pytest.approx(0.1)
    none = select.summarise(_run(tmp_path / "n", [(None, 0)]).root)
    assert (none.picked_round, none.picked_score, none.baseline_score, none.delta) == (None, None, None, None)


def test_an_old_record_with_best_fields_still_summarises(tmp_path):
    """A record.json written before 2026-09-22 (best_round / final_score / status 'passed')
    loads — the extra keys are ignored, the status reads 'stopped', the stop reason stays."""
    ws, _ = make_fake_run(tmp_path / "runs")
    data = json.loads(ws.record_path.read_text())
    data.update(status="passed", best_round=0, baseline_score=0.55, final_score=0.55)
    data.setdefault("extra", {})["stop_reason"] = "pass"
    ws.record_path.write_text(json.dumps(data))
    s = select.summarise(ws.root)
    assert (s.picked_round, s.stop_reason, s.baseline_score) == (1, "pass", 0.55)


class _Pairwise:
    def __init__(self, winner: str, confidence: float, *, fail: bool = False):
        self.winner, self.confidence, self.fail, self.calls = winner, confidence, fail, []

    def compare(self, spec, renders_a, renders_b, *, rubric):
        self.calls.append((renders_a.views[0].path, renders_b.views[0].path, rubric))
        if self.fail:
            raise RuntimeError("judge down")
        return SimpleNamespace(winner=self.winner, confidence=self.confidence, reasons=["r"], usage=Usage(cost_usd=0.05),
                               error="")


def test_pick_by_pairwise_asks_only_inside_the_margin_and_never_buys_a_verdict_twice(tmp_path):
    ws = _run(tmp_path / "r", [(0.70, 0), (0.72, 0)])
    judge = _Pairwise("b", 0.9)
    assert select.pick(ws.root, by="pairwise", judge=judge) == 0     # r00 is B, and B won
    assert judge.calls and "/r01/" in judge.calls[0][0] and judge.calls[0][2] == "static_object_v1"
    assert select.pick(ws.root, by="pairwise", judge=judge) == 0 and len(judge.calls) == 1   # cached, not re-bought
    assert ws.judge_path(1, "_vs_r00_pairwise").is_file()
    assert [e for e in EventLog(ws.events_path).read() if e["event"] == "pick.pairwise"]
    # an unsure verdict, an outage or a clear gap keep the top score
    assert select.pick(_run(tmp_path / "u", [(0.70, 0), (0.72, 0)]).root, by="pairwise", judge=_Pairwise("b", 0.5)) == 1
    assert select.pick(_run(tmp_path / "f", [(0.70, 0), (0.72, 0)]).root, by="pairwise",
                       judge=_Pairwise("b", 0.9, fail=True)) == 1
    far = _Pairwise("b", 0.99)
    assert select.pick(_run(tmp_path / "g", [(0.60, 0), (0.72, 0)]).root, by="pairwise", judge=far) == 1 and not far.calls
    assert select.pick(ws.root) == 1  # by score it is still the top score


# --------------------------------------------------------------------------- package
def test_package_hands_over_one_round_and_says_why(tmp_path):
    ws = _run(tmp_path / "r", [(0.5, 0), (0.8, 0), (0.6, 0)])
    out = select.package(ws.root, 1, method="score")
    assert out == ws.deliverable and (out / "src" / "model.py").read_text() == "import bpy  # round 1\n"
    assert (out / "object.glb").read_bytes() == (ws.round_artifacts(1) / "object.glb").read_bytes()
    sel = json.loads((ws.root / select.SELECTION_NAME).read_text())
    assert (sel["round"], sel["method"], sel["textured"]) == (1, "score", False)
    assert sel["scores"] == {"0": 0.5, "1": 0.8, "2": 0.6}
    assert json.loads(ws.deliverable_manifest_path.read_text())["round"] == 1
    with pytest.raises(ValueError, match="no round 7"):
        select.package(ws.root, 7)


def test_a_scene_or_graphics_round_packages_from_its_own_commit_and_renders(tmp_path):
    """A scene round's hand-over is its commit (src/ + public/, GLB assets included); a
    graphics round's is its commit + its judged frames (renders/rNN) + the sheet and GIF it
    kept under artifacts/rNN/.  Round 0 of two packages round 0's bytes, never the last's."""
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


def _fake_texture_pass(calls: list, *, shipped: bool = True, fail: bool = False):
    import codeverse3d.texturing.run as trun

    def run(ws, spec, plan, *, glb_in, sheet, update_record, **kw):
        calls.append((Path(glb_in), Path(sheet)))
        if fail:
            raise RuntimeError("image model down")
        report = trun.TextureReport(plan=trun.TexturePlan(), textures=trun.TextureSet(), seam=trun.SeamGateResult(),
                                    glb_in=str(glb_in), shipped=shipped)
        (ws.artifacts / trun.TEXTURED_GLB).write_bytes(b"glTF textured")
        ws.write_json(trun.report_path(ws), report)
        if update_record:
            trun.record_texturing(ws, report)
        return report

    return run


def test_package_textures_the_rounds_own_glb_once(tmp_path, monkeypatch):
    import codeverse3d.texturing.run as trun

    ws = _run(tmp_path / "r", [(0.5, 0), (0.8, 0)])
    calls: list = []
    monkeypatch.setattr(trun, "texture_pass", _fake_texture_pass(calls))
    select.package(ws.root, 0, texture=True)
    assert calls == [(ws.round_artifacts(0) / "object.glb", ws.renders_dir(0) / "sheet.png")]   # round 0, not the last
    assert (ws.deliverable / "object_textured.glb").is_file()
    assert json.loads((ws.root / select.SELECTION_NAME).read_text())["textured"] is True
    assert json.loads(ws.record_path.read_text())["extra"]["texturing"]["shipped"] is True
    select.package(ws.root, 0, texture=True)
    assert len(calls) == 1, "a pass already made from these bytes is never bought again"
    # another round's hand-over does not carry round 0's pack
    select.package(ws.root, 1)
    assert not (ws.deliverable / "object_textured.glb").exists()


def test_a_failed_texture_pass_does_not_stop_the_hand_over(tmp_path, monkeypatch):
    import codeverse3d.texturing.run as trun

    ws = _run(tmp_path / "r", [(0.5, 0)])
    monkeypatch.setattr(trun, "texture_pass", _fake_texture_pass([], fail=True))
    select.package(ws.root, 0, texture=True)
    assert (ws.deliverable / "object.glb").is_file() and not (ws.deliverable / "object_textured.glb").exists()
    assert json.loads((ws.root / select.SELECTION_NAME).read_text())["textured"] is False
    assert "texture.failed" in [e["event"] for e in EventLog(ws.events_path).read()]


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
