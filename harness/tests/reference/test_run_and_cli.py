"""``ground_spec`` on a real workspace, and the ``3dcv make --reference`` wiring."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from codeverse.contracts.spec import ReferenceImage
from codeverse.events import EventLog
from codeverse.reference import SYNTH_TAG, ground_spec
from codeverse.workspace import Workspace
from tests.reference.conftest import GOOD_GATE, PROMPT_PLAN, FakeChat, FakeImageModel, make_spec


def _chat(gates=None):
    return FakeChat({"reference_prompt": [PROMPT_PLAN], "reference_gate": list(gates or [GOOD_GATE, GOOD_GATE])})


def test_ground_spec_rewrites_the_spec_and_records_everything(tmp_ws: Workspace, cache_dir: Path):
    events = EventLog(tmp_ws.events_path)
    spec = make_spec()
    tmp_ws.write_json(tmp_ws.spec_path, spec)
    out, refset, why = ground_spec(spec, tmp_ws, model=_chat(), image_model=FakeImageModel(),
                                   events=events, cache_dir=cache_dir)
    assert len(out.references) == 2 and SYNTH_TAG in out.tags and "attached 2" in why
    on_disk = json.loads(tmp_ws.spec_path.read_text())
    assert len(on_disk["references"]) == 2 and on_disk["tags"] == [SYNTH_TAG]
    assert all(r["note"].startswith("SYNTHESIZED") for r in on_disk["references"])
    # the whole set — verdicts included — is kept next to the run
    saved = json.loads((tmp_ws.artifacts / "reference" / "reference_set.json").read_text())
    assert len(saved["views"]) == 2 and saved["views"][0]["verdict"]["ok"] is True
    names = [json.loads(line)["event"] for line in tmp_ws.events_path.read_text().splitlines()]
    assert "reference.start" in names and "reference.done" in names


def test_ground_spec_leaves_a_user_reference_alone(tmp_ws: Workspace, cache_dir: Path):
    spec = make_spec(references=[ReferenceImage(path=str(tmp_ws.root / "mine.png"), note="my photo")])
    out, refset, why = ground_spec(spec, tmp_ws, model=_chat(), image_model=FakeImageModel(), cache_dir=cache_dir)
    assert out is spec and refset.views == [] and "user --image references win" in why


def test_ground_spec_survives_a_rejected_reference(tmp_ws: Workspace, cache_dir: Path):
    bad = dict(GOOD_GATE, plain_background=False)
    spec = make_spec()
    out, refset, why = ground_spec(spec, tmp_ws, model=_chat([bad, bad]), image_model=FakeImageModel(),
                                   cache_dir=cache_dir)
    assert out is spec and not refset.ok and "running without one" in why


def test_ground_spec_survives_a_dead_backend(tmp_ws: Workspace, monkeypatch, cache_dir: Path):
    monkeypatch.setattr("codeverse.reference._chat_model",
                        lambda mid: (_ for _ in ()).throw(RuntimeError("no keys")))
    spec = make_spec()
    out, refset, why = ground_spec(spec, tmp_ws, cache_dir=cache_dir)
    assert out is spec and "reference grounding unavailable" in why


def test_cli_flag_calls_the_grounding_and_never_kills_the_run(tmp_path: Path, monkeypatch):
    from codeverse.cli.main import app

    seen = {}

    def fake_ground(spec, ws, *, n_views, model=None, image_model=None, image_model_id="", events=None, cache_dir=None):
        seen["n_views"] = n_views
        return spec, __import__("codeverse.reference", fromlist=["ReferenceSet"]).ReferenceSet(prompt=spec.prompt), "nope"

    monkeypatch.setattr("codeverse.reference.ground_spec", fake_ground)
    res = CliRunner().invoke(app, ["make", "a stool", "--reference", "--reference-views", "1",
                                   "--runs-dir", str(tmp_path), "--no-run"])
    assert res.exit_code == 0, res.output
    assert seen["n_views"] == 1 and "reference grounding: nope" in res.output


def test_cli_without_the_flag_never_touches_the_reference_package(tmp_path: Path, monkeypatch):
    from codeverse.cli.main import app

    monkeypatch.setattr("codeverse.reference.ground_spec",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")))
    res = CliRunner().invoke(app, ["make", "a stool", "--runs-dir", str(tmp_path), "--no-run"])
    assert res.exit_code == 0, res.output
