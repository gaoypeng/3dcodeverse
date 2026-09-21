"""Captioner: fake ChatModel (offline) + one optional live Gemini call."""

from __future__ import annotations

import json

import pytest

from codeverse.addons.dataset.captions import (
    CaptionError,
    Captions,
    caption_sample,
    validate_captions,
)
from codeverse.contracts.chat import ChatRequest, ChatResponse, ImagePart
from codeverse.contracts.common import Usage
from codeverse.record.record import load_record

GOOD = {
    "detailed": "A four-legged wooden dining chair with a flat square seat and a tall slatted backrest.",
    "instruction": "Write a Blender Python script that builds a simple wooden dining chair with four legs and a slatted back.",
    "factory": "Create a box for the seat, four tapered box legs under its corners, two uprights and three horizontal slats for the back, then apply a wood material.",
}


class FakeModel:
    provider, model = "fake", "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[ChatRequest] = []

    @property
    def id(self):
        return "fake:fake"

    def generate(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        data = self.replies.pop(0)
        return ChatResponse(text=json.dumps(data), parsed=data, usage=Usage(cost_usd=0.001))


def test_caption_sample_stores_and_writes(fake_run):
    ws, rec = fake_run
    m = FakeModel([GOOD])
    caps = caption_sample(ws, rec, "fake:fake", model=m)
    assert caps.detailed.startswith("A four-legged")
    req = m.requests[0]
    assert req.response_schema is not None and "Blender Python script" in req.system
    imgs = [p for p in req.messages[0].parts if isinstance(p, ImagePart)]
    assert len(imgs) == 3  # sheet + 2 views
    assert "round 1" in req.messages[0].text  # best-round code
    assert (ws.root / "captions.json").is_file()
    again = load_record(ws)
    assert again.extra["captions"]["factory"] == GOOD["factory"]
    assert again.extra["captions"]["provenance"]["captioner"] == "fake:fake"
    assert again.extra["captions"]["provenance"]["cost_usd"] == pytest.approx(0.001)
    # a caption changes the hand-over folder too — it used to leave deliverable/,
    # the manifest, record.deliverable and telemetry/ describing an uncaptioned run
    assert json.loads((ws.deliverable / "captions.json").read_text())["factory"] == GOOD["factory"]
    assert again.deliverable is not None and again.telemetry is not None
    assert "deliverable/captions.json" in {f.path for f in again.deliverable.files}


def test_caption_cmd_writes_the_captioner_row_into_the_run_ledger(fake_run, monkeypatch):
    """`3dcode flywheel caption` joins the run's ledger the way `3dcode judge` does —
    the captioner's priced call used to go to the per-process log instead."""
    from typer.testing import CliRunner

    import codeverse.models.registry as R
    from codeverse.cli.main import app
    from codeverse.cost.ledger import open_run_ledger

    ws, rec = fake_run
    ledger = open_run_ledger(ws.root)  # the run already keeps one (create=False appends)
    ledger.path.touch()
    monkeypatch.setattr(R, "_build_chat_model", lambda _mid: FakeModel([GOOD]))
    r = CliRunner().invoke(app, ["flywheel", "caption", ws.root.name, "--runs-dir", str(ws.root.parent),
                                 "--model", "fake:fake"])
    assert r.exit_code == 0, r.output
    rows = [json.loads(ln) for ln in ledger.path.read_text().splitlines() if ln.strip()]
    assert any(row.get("label") == "captioner" for row in rows), rows
    assert (ws.deliverable / "captions.json").is_file()


def test_caption_retry_then_fail(fake_run):
    ws, rec = fake_run
    bad = dict(GOOD, detailed="Made with bpy.ops primitives: a chair with four legs and a backrest.")
    m = FakeModel([bad, GOOD])
    caps = caption_sample(ws, rec, "fake:fake", model=m)
    assert caps.detailed == GOOD["detailed"] and len(m.requests) == 2
    assert "bpy" in m.requests[1].messages[-1].text
    m2 = FakeModel([bad, bad])
    with pytest.raises(CaptionError):
        caption_sample(ws, rec, "fake:fake", model=m2)


def test_validate_rules():
    from codeverse.contracts.common import Language

    c = Captions(**GOOD)
    assert validate_captions(c, Language.BLENDER) == []
    c2 = Captions(**dict(GOOD, instruction="Please model a wooden chair with four legs for me."))
    assert any("target language" in p for p in validate_captions(c2, Language.BLENDER))


@pytest.mark.live
def test_caption_live_gemini(fake_run):
    from codeverse.config import get_settings

    if not get_settings().gemini_api_keys:
        pytest.skip("no gemini keys")
    ws, rec = fake_run
    caps = caption_sample(ws, rec, "gemini:gemini-3.7-flash")
    assert len(caps.detailed) > 20 and "blender" in caps.instruction.lower()


# --------------------------------------------------------------------------- finding: Three.js phrase vs forbidden THREE.
GOOD_JS = {
    "detailed": "A desk lamp with a round base, an arched arm and a conical shade.",
    "instruction": "Write a Three.js module that builds a desk lamp with a round base and conical shade.",
    "factory": "Build the base cylinder, sweep the arm along an arc, add the cone shade, then merge the groups.",
}


def test_threejs_instruction_naming_threejs_is_valid():
    """The required 'Three.js' phrase must NOT trip the forbidden-API check (both js languages)."""
    from codeverse.addons.dataset.captions import Captions, validate_captions
    from codeverse.contracts.common import Language

    c = Captions(**GOOD_JS)
    assert validate_captions(c, Language.THREEJS) == []
    assert validate_captions(c, Language.SCENE_THREEJS) == []
    # the API namespace itself stays forbidden (case-sensitive THREE.<Symbol>)
    bad = Captions(**dict(GOOD_JS, factory="Build a THREE.Group holding THREE.Mesh boxes for every part."))
    probs = validate_captions(bad, Language.THREEJS)
    assert probs and "THREE." in probs[0]
    # and omitting the platform name still fails the phrase check
    off = Captions(**dict(GOOD_JS, instruction="Write a module that builds a desk lamp with a round base."))
    assert any("target language" in p for p in validate_captions(off, Language.THREEJS))


def test_caption_sample_threejs_run(tmp_path):
    """End to end: a threejs run captions successfully with a 'Three.js' instruction."""
    from codeverse.contracts.common import Language
    from tests.flywheel_cli.conftest import make_fake_run

    ws, rec = make_fake_run(tmp_path / "runs", "lamp_js", prompt="a desk lamp", language=Language.THREEJS)
    m = FakeModel([GOOD_JS])
    caps = caption_sample(ws, rec, "fake:fake", model=m)
    assert "Three.js" in caps.instruction and len(m.requests) == 1  # no retry needed


def test_caption_graphics_and_scene_phrases():
    """Graphics languages have phrases + phrase words (no KeyError, sane rules)."""
    from codeverse.addons.dataset.captions import Captions, validate_captions
    from codeverse.contracts.common import Language

    shader = Captions(
        detailed="Neon rain streaks down a dark window while blurred city lights pulse behind the glass.",
        instruction="Write a GLSL fragment shader showing neon rain running down a window at night.",
        factory="Layer a bokeh background pass, a grid-cell rain pass with per-cell offsets, then grade with a vignette.",
    )
    assert validate_captions(shader, Language.GLSL_SHADER) == []
    assert any("target language" in p
               for p in validate_captions(shader.model_copy(update={"instruction": "Write a shader with rain."}),
                                          Language.GLSL_SHADER))
    gl = shader.model_copy(update={"instruction": "Write an OpenGL Python program showing neon rain on a window."})
    assert validate_captions(gl, Language.OPENGL_PYTHON) == []
    leaked = shader.model_copy(update={"factory": "Uses moderngl FBOs for the feedback pass."})
    assert any("moderngl" in p for p in validate_captions(leaked, Language.OPENGL_PYTHON))


def test_caption_graphics_run_from_frames(tmp_path):
    """(f) graphics runs (no GLB) caption from the sheet/frames renders."""
    from codeverse.contracts.common import Language
    from tests.flywheel_cli.conftest import make_fake_run

    ws, rec = make_fake_run(tmp_path / "runs", "rain_glsl", prompt="neon rain", language=Language.GLSL_SHADER)
    reply = {
        "detailed": "Bright neon streaks slide down a dark pane while soft coloured discs drift behind it.",
        "instruction": "Write a GLSL fragment shader with neon rain streaking down a dark window.",
        "factory": "Hash-place bokeh discs in three depth layers, add per-cell rain trails, then tonemap and vignette.",
    }
    m = FakeModel([reply])
    caps = caption_sample(ws, rec, "fake:fake", model=m)
    assert caps.instruction.lower().count("glsl")
    req = m.requests[0]
    from codeverse.contracts.chat import ImagePart as IP

    imgs = [p for p in req.messages[0].parts if isinstance(p, IP)]
    assert len(imgs) == 3  # sheet + 2 frame views
