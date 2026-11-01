"""Captioner: fake ChatModel (offline) + one optional live Gemini call."""

from __future__ import annotations

import json

import pytest

from codeverse3d.addons.dataset.captions import (
    CaptionError,
    Captions,
    caption_sample,
    validate_captions,
)
from codeverse3d.contracts.chat import ImagePart
from codeverse3d.record.record import load_record
from tests.orchestrator_tracks.fakes import FakeChatModel

GOOD = {
    "detailed": "A four-legged wooden dining chair with a flat square seat and a tall slatted backrest.",
    "instruction": "Write a Blender Python script that builds a simple wooden dining chair with four legs and a slatted back.",
    "factory": "Create a box for the seat, four tapered box legs under its corners, two uprights and three horizontal slats for the back, then apply a wood material.",
}


def test_caption_sample_captions_the_picked_round_records_provenance_and_retries_once(fake_run):
    ws, rec = fake_run
    m = FakeChatModel([GOOD])
    caption_sample(ws, rec, "fake:fake", model=m)
    req = m.requests[0]
    assert "Blender Python script" in req.system and "round 1" in req.messages[0].text   # best-round code
    assert len([p for p in req.messages[0].parts if isinstance(p, ImagePart)]) == 3   # sheet + 2 views
    prov = load_record(ws).extra["captions"]["provenance"]
    assert prov["captioner"] == "fake:fake" and prov["cost_usd"] == pytest.approx(0.002)

    bad = dict(GOOD, detailed="Made with bpy.ops primitives: a chair with four legs and a backrest.")
    m = FakeChatModel([bad, GOOD])
    caps = caption_sample(ws, rec, "fake:fake", model=m)
    assert caps.detailed == GOOD["detailed"] and len(m.requests) == 2
    assert "bpy" in m.requests[1].messages[-1].text
    with pytest.raises(CaptionError):
        caption_sample(ws, rec, "fake:fake", model=FakeChatModel([bad, bad]))


def test_caption_cmd_writes_the_captioner_row_into_the_run_ledger(fake_run, monkeypatch):
    """The captioner's priced call joins the run's ledger."""
    from typer.testing import CliRunner

    import codeverse3d.models.registry as R
    from codeverse3d.addons import select
    from codeverse3d.cli.main import app
    from codeverse3d.cost.ledger import open_run_ledger

    ws, rec = fake_run
    select.package(ws.root, 1)
    ledger = open_run_ledger(ws.root)  # the run already keeps one (create=False appends)
    ledger.path.touch()
    monkeypatch.setattr(R, "build_chat_model", lambda _mid: FakeChatModel([GOOD]))
    r = CliRunner().invoke(app, ["flywheel", "caption", ws.root.name, "--runs-dir", str(ws.root.parent),
                                 "--model", "fake:fake"])
    assert r.exit_code == 0, r.output
    rows = [json.loads(ln) for ln in ledger.path.read_text().splitlines() if ln.strip()]
    assert any(row.get("label") == "captioner" for row in rows), rows
    assert (ws.deliverable / "captions.json").is_file()


@pytest.mark.live
def test_caption_live_gemini(fake_run):
    from codeverse3d.config import get_settings

    if not get_settings().gemini_api_keys:
        pytest.skip("no gemini keys")
    ws, rec = fake_run
    caps = caption_sample(ws, rec, "gemini:gemini-3.7-flash")
    assert len(caps.detailed) > 20 and "blender" in caps.instruction.lower()


GOOD_JS = dict(GOOD, instruction="Write a Three.js module that builds a desk lamp with a round base and conical shade.",
               factory="Build the base cylinder, sweep the arm along an arc, add the cone shade, then merge the groups.")
SHADER = dict(GOOD, instruction="Write a GLSL fragment shader showing neon rain running down a window at night.",
              factory="Layer a bokeh background pass, a grid-cell rain pass, then grade with a vignette.")


@pytest.mark.parametrize("fields, language, problem", [
    (GOOD, "blender", None),
    (dict(GOOD, instruction="Please model a wooden chair with four legs for me."), "blender", "target language"),
    (GOOD_JS, "threejs", None),              # the required 'Three.js' phrase is not the forbidden API
    (dict(GOOD_JS, factory="Build a THREE.Group holding THREE.Mesh boxes for every part."), "threejs", "THREE."),
    (SHADER, "glsl_shader", None),
    (dict(SHADER, instruction="Write an OpenGL Python program showing neon rain on a window."), "opengl_python", None),
    (dict(SHADER, instruction="Write an OpenGL Python program showing neon rain on a window.",
          factory="Uses moderngl FBOs for the feedback pass."), "opengl_python", "moderngl"),
])
def test_validate_captions(fields, language, problem):
    from codeverse3d.contracts.common import Language

    probs = validate_captions(Captions(**fields), Language(language))
    assert (probs == []) if problem is None else any(problem in p for p in probs), probs


# --------------------------------------------------------------------------- a run recorded before artifacts/rNN/
