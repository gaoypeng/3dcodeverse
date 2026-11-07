"""CLI tests (typer CliRunner, offline): make --no-run, status, flywheel, doctor pieces, tools list."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from codeverse3d.cli.main import app
from codeverse3d.contracts.spec import Spec

runner = CliRunner()


@pytest.fixture
def made_run(tmp_path: Path):
    """`3dcodeverse make --no-run` into a fresh runs dir → ``(runs_dir, run_dir)``."""
    def make(*extra: str) -> tuple[Path, Path]:
        runs = tmp_path / "runs"
        r = runner.invoke(app, ["make", "a clay pot", "--runs-dir", str(runs), "--no-run", *extra])
        assert r.exit_code == 0, r.output
        return runs, next(d for d in runs.iterdir() if d.is_dir() and not d.name.startswith("."))

    return make


@pytest.fixture
def stub_track(monkeypatch):
    """Replace ``tracks.get_track`` with a stub whose ``run`` calls ``on_run(spec, resume,
    force)`` and then raises ``exc`` — KeyboardInterrupt by default, which the CLI turns
    into exit code 130, so the resume path is exercised without any real work."""
    import codeverse3d.tracks as tracks_pkg

    def install(on_run=None, exc: BaseException | None = None):
        class _T:
            def run(self, spec, ws_, *, resume=False, force=False):
                if on_run is not None:
                    on_run(spec, resume, force)
                raise exc if exc is not None else KeyboardInterrupt

        monkeypatch.setattr(tracks_pkg, "get_track", lambda track, **options: _T())

    return install


def test_make_no_run_creates_valid_workspace(tmp_path: Path):
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "a wooden dining chair", "--track", "static_object", "--language", "blender",
                            "--runs-dir", str(runs), "--no-run", "--dim", "width=0.5", "--must", "four legs",
                            "--rounds", "2", "--generator", "agy:gemini-3.8-flash"])
    assert r.exit_code == 0, r.output
    # `.locks/` (runlock.exclusive) lives beside the run dirs on purpose — a lock inside
    # the directory a --force wipe deletes is no lock
    dirs = [d for d in runs.iterdir() if d.is_dir() and not d.name.startswith(".")]
    assert len(dirs) == 1
    ws = dirs[0]
    assert ws.name.startswith("a_wooden_dining_chair_") and (ws / "spec.json").is_file() and (ws / ".git").is_dir()
    spec = Spec.model_validate_json((ws / "spec.json").read_text())
    assert spec.constraints.dimensions_m == {"width": 0.5} and spec.constraints.must_have == ["four legs"]
    assert spec.budget.max_rounds == 2 and spec.budget.max_minutes == 60.0
    assert spec.backends.generator == "agy:gemini-3.8-flash"
    assert (ws / "src").is_dir() and (ws / "artifacts" / "renders").is_dir()
    # same prompt again → same slug → refuse without --force
    r2 = runner.invoke(app, ["make", "a wooden dining chair", "--runs-dir", str(runs), "--no-run"])
    assert r2.exit_code == 1 and "already exists" in " ".join(r2.output.split())  # the console wraps long paths
    r3 = runner.invoke(app, ["make", "a wooden dining chair", "--runs-dir", str(runs), "--no-run", "--force"])
    assert r3.exit_code == 0


def test_doctor_json(monkeypatch):
    """The CLI serialises doctor rows; the checks themselves are tested by install tests."""
    import codeverse3d.doctor as doctor_cli

    seen = {}

    def fake_doctor(*, live, gpu):
        seen.update(live=live, gpu=gpu)
        return [("python", "OK", "3.x"), ("node", "WARN", "missing"), ("mcp", "FAIL", "broken")]

    monkeypatch.setattr(doctor_cli, "run_doctor", fake_doctor)
    r = runner.invoke(app, ["doctor", "--no-gpu", "--json"])
    assert r.exit_code == 1, r.output
    assert seen == {"live": False, "gpu": False}
    rows = json.loads(r.output[r.output.index("["):])
    assert rows == [
        {"check": "python", "status": "OK", "detail": "3.x"},
        {"check": "node", "status": "WARN", "detail": "missing"},
        {"check": "mcp", "status": "FAIL", "detail": "broken"},
    ]


class _FakeVlm:
    captured: dict = {}

    def __init__(self, *, rubric=None, model_id=None, n_samples=1, **kw):
        _FakeVlm.captured["rubric"] = rubric
        _FakeVlm.captured["n_samples"] = n_samples

    def judge(self, inp):
        from tests.flywheel_cli.conftest import _judgment

        _FakeVlm.captured["inp"] = inp
        return _judgment(0.7, False, ["polish"])


def _write_stool_plan(ws):
    from codeverse3d.contracts.plan import AcceptanceItem, BBox, PartPlan, StaticPlan

    plan = StaticPlan(object_name="Chair", summary="a chair", overall_bbox=BBox(center=(0, 0, 0.4), extents=(0.5, 0.5, 0.8)),
                      parts=[PartPlan(name="Seat", role="r", description="d", bbox=BBox(center=(0, 0, 0.4), extents=(0.4, 0.4, 0.04)))],
                      acceptance=[AcceptanceItem(id="a1", text="four legs", how="visual", priority="must")])
    ws.write_json(ws.plan_path, plan)


def test_commands_over_a_runs_dir(runs_dir: Path, tmp_path: Path, monkeypatch):
    """flywheel export/pairs/index/caption, judge (in-run inputs, the reference judge, a typed failure), status."""
    import codeverse3d.addons.dataset.captions as cap
    import codeverse3d.judges.vlm_judge as vj
    from codeverse3d.config import get_settings
    from codeverse3d.workspace import Workspace
    from tests.flywheel_cli.conftest import tiny_png

    out = tmp_path / "ds"
    r = runner.invoke(app, ["flywheel", "export", str(runs_dir), str(out), "--pack"])
    assert r.exit_code == 0, r.output
    assert (out / "metadata.parquet").is_file() and (out / "samples-000.tar").is_file()
    r = runner.invoke(app, ["flywheel", "pairs", str(runs_dir), str(tmp_path / "p.jsonl")])
    assert r.exit_code == 0 and "pairs" in r.output

    # no --model: Settings.default_captioner, not a literal the CLI kept (a configured one was ignored)
    seen: list[str] = []
    monkeypatch.setattr(get_settings(), "default_captioner", "fake:configured")
    monkeypatch.setattr(cap, "caption_sample", lambda ws, rec, model, **kw: seen.append(model) or SimpleNamespace(instruction="x"))
    r = runner.invoke(app, ["flywheel", "caption", "wooden_chair_ab12cd34", "--runs-dir", str(runs_dir), "--out",
                            str(tmp_path / "side")])
    assert r.exit_code == 0, r.output
    assert seen == ["fake:configured"]

    # judge: re-judges with the run's own inputs
    ws = Workspace(runs_dir / "wooden_chair_ab12cd34")
    _write_stool_plan(ws)
    tiny_png(ws.renders_dir(1) / "clay" / "view_top.png", (128, 128, 128))
    monkeypatch.setattr(vj, "VlmJudge", _FakeVlm)
    _FakeVlm.captured.clear()
    r = runner.invoke(app, ["judge", "wooden_chair_ab12cd34", "--runs-dir", str(runs_dir)])
    assert r.exit_code == 0, r.output
    inp = _FakeVlm.captured["inp"]
    assert _FakeVlm.captured["rubric"] == "static_object"  # the rubric the round was judged with
    assert inp.round_index == 1  # best round
    assert "Chair" in inp.plan_summary  # plan digest, not empty
    assert [a.id for a in inp.acceptance] == ["a1"]
    assert inp.previous is not None and inp.previous.overall == 0.55  # round 0's verdict
    assert inp.gates and inp.renders.views
    assert inp.geometry_views is not None and inp.geometry_views.views[0].mode == "clay"
    assert (ws.artifacts / "judge" / "r01_cli.json").is_file()

    # a measured rubric goes to the reference judge
    class _FakeRef(_FakeVlm):
        pass

    monkeypatch.setattr(vj, "ReferenceJudge", _FakeRef)
    _FakeRef.captured.clear()
    r = runner.invoke(app, ["judge", "wooden_chair_ab12cd34", "--runs-dir", str(runs_dir), "--rubric", "reference"])
    assert r.exit_code == 0, r.output  # no ValueError traceback any more
    assert _FakeRef.captured["rubric"] == "reference"

    # a judge ValueError is a CLI error
    class _Boom(_FakeVlm):
        def judge(self, inp):
            raise ValueError("rubric has measured criteria but VlmJudge computes none")

    monkeypatch.setattr(vj, "VlmJudge", _Boom)
    r = runner.invoke(app, ["judge", "wooden_chair_ab12cd34", "--runs-dir", str(runs_dir)])
    assert r.exit_code == 1
    assert "judge failed" in r.output + str(getattr(r, "stderr", "") or "") and "Traceback" not in r.output

    r = runner.invoke(app, ["status", "wooden_chair_ab12cd34", "--runs-dir", str(runs_dir)])
    assert r.exit_code == 0, r.output


def test_resume_refuses_a_finished_run_and_never_re_enters_it(made_run, stub_track):
    """A finished run never re-enters the track unless forced; a raised cap is written to spec.json
    and seen by the resumed run; spec drift is a clean CLI error."""
    from codeverse3d.contracts.run import RunStatus
    from codeverse3d.orchestrator import RunState
    from codeverse3d.workspace import Workspace

    runs, run_dir = made_run()
    ws = Workspace(run_dir)
    ws.state_path.write_text(json.dumps({"status": "no_refine_tasks"}))
    assert RunState.load(ws).status is RunStatus.NO_REFINE_TASKS

    entered, specs = [], []
    stub_track(lambda spec, resume, force: (entered.append((resume, force)), specs.append(spec)))
    r = runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs)])
    assert r.exit_code == 1 and "already finished" in r.output and "status=no_refine_tasks" in r.output
    assert entered == [], "the pipeline must not be re-entered"
    assert RunState.load(ws).status is RunStatus.NO_REFINE_TASKS, "the terminal state must survive"
    # ... and the escape hatch still works, loudly
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs), "--force"]).exit_code == 130
    assert entered == [(True, True)]

    # every round ran: raising --rounds is how a max_rounds stop goes on (from its last round)
    RunState(status=RunStatus.MAX_ROUNDS).save(ws)
    r = runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs)])
    assert r.exit_code == 1 and "status=max_rounds" in r.output
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs), "--max-minutes", "90"]).exit_code == 1
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs), "--rounds", "5"]).exit_code == 130
    assert Spec.model_validate_json(ws.spec_path.read_text()).budget.max_rounds == 5

    # a budget stop is the documented exception: it resumes when a cap is raised
    RunState(status=RunStatus.BUDGET).save(ws)
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs)]).exit_code == 1
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs), "--max-minutes", "60"]).exit_code == 130
    assert Spec.model_validate_json(ws.spec_path.read_text()).budget.max_minutes == 60.0
    assert specs[-1].budget.max_minutes == 60.0, "the resumed run must see the raised ceiling"

    # an interrupted run is untouched by the guard
    RunState(status=RunStatus.REFINING).save(ws)
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs)]).exit_code == 130

    # spec drift is a typed CLI refusal, not a traceback
    from codeverse3d.tracks.lifecycle import SpecChanged

    stub_track(exc=SpecChanged("spec.json changed under this run; fork a new run or resume with --force"))
    r = runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs)])
    assert r.exit_code == 2 and "--force" in r.output + str(getattr(r, "stderr", "") or "")
    assert "Traceback" not in r.output


def test_a_replay_judges_the_picked_round_on_the_runs_rubric_and_books_into_the_record(tmp_path: Path, monkeypatch):
    """`3dcodeverse judge` replays the run it reads.  r0 0.55, r1 unjudged (a judge outage), r2 0.90, of a
    static `--image` run:
    * no --round after `pick --round 0` is r0, the packaged round — not the best-scored r2 (N55a);
    * the unjudged r1 replays on the run's reference, not the track's static_object (N49);
    * r2's previous verdict is the loop's — r1's, i.e. none — not r0's (N56);
    * what the replay (or a pick) paid joins record.total_usage: it stays the ledger's sum (N53)."""
    import codeverse3d.judges.vlm_judge as vj
    from codeverse3d.contracts.common import Usage
    from codeverse3d.contracts.spec import ReferenceImage
    from codeverse3d.cost.ledger import ledger_usage, load_ledger, record_call
    from codeverse3d.record.record import load_record
    from tests.flywheel_cli.conftest import _judgment, make_fake_run

    runs = tmp_path / "runs"
    ws, rec = make_fake_run(runs, "stool_img")
    rec.spec.references = [ReferenceImage(path=str(tmp_path / "ref.png"))]
    r2 = rec.rounds[1].model_copy(update={"index": 2, "judgment": _judgment(0.90, True, [])})
    rec.rounds[1].judgment = None
    rec.rounds.append(r2)
    rec.extra["rubric"] = "reference"
    ws.write_json(ws.record_path, rec)
    ws.write_json(ws.spec_path, rec.spec)
    ledger = ws.telemetry / "cost.jsonl"

    def paid(cost: float) -> float:   # a paid call booked into the run's ledger; returns the ledger total
        record_call(Usage(backend="gemini", model="gemini-3.8-flash", cost_usd=cost), stage="judge", label="t", ledger=ledger)
        return ledger_usage(load_ledger(ws.root)).cost_usd

    total = paid(0.03)                # a paid call: the pick re-packages the record
    assert runner.invoke(app, ["pick", "stool_img", "--round", "0", "--runs-dir", str(runs)]).exit_code == 0
    assert load_record(ws).total_usage.cost_usd == pytest.approx(total)

    class _Judge:
        seen: list = []

        def __init__(self, *, rubric=None, **kw):
            self.rubric = rubric

        def judge(self, inp):
            _Judge.seen.append((inp.round_index, self.rubric, inp.previous))
            paid(0.05)
            return _judgment(0.7, False, [])

    monkeypatch.setattr(vj, "ReferenceJudge", _Judge)
    for args in ([], ["--round", "1"], ["--round", "2"]):
        r = runner.invoke(app, ["judge", "stool_img", "--runs-dir", str(runs), *args])
        assert r.exit_code == 0, r.output
    (i0, _, _), (i1, rubric1, _), (i2, _, prev2) = _Judge.seen
    assert (i0, i1, i2) == (0, 1, 2)
    assert rubric1 == "reference"
    assert prev2 is None
    assert load_record(ws).total_usage.cost_usd == pytest.approx(ledger_usage(load_ledger(ws.root)).cost_usd)
    assert load_record(ws).total_usage.cost_usd == pytest.approx(total + 3 * 0.05)
