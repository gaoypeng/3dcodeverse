"""Control characters in model output (codeverse/models/schema_utils.strip_control_chars).

Regression (2026-08-24): a gemini-3.7-flash plan emitted five ``\\u0000`` escapes where the
model meant glyphs it could not encode — "0.078 × 0.300 × 0.240 m" became
"0.078 \\x00 0.300 \\x00 0.240 m", and "0.240 m ± 0.005 m" became "0.240 m \\x00 0.005 m".
That is legal JSON, so it passed the parser, passed pydantic validation, and was written to
plan.json.  It detonated four stages later in a codex cell: every generation task died with
``ValueError: embedded null byte`` raised by subprocess.Popen, an error naming no file, no
field and no value.  The whole cell was lost.
"""

from __future__ import annotations

import json

from codeverse.models.schema_utils import parse_json_lenient, strip_control_chars

THE_REAL_PAYLOAD = (
    '{"summary": "Overall envelope is 0.078 \\u0000 0.300 \\u0000 0.240 m",'
    ' "acceptance": [{"text": "height is 0.240 m \\u0000 0.005 m"}]}'
)


def test_the_plan_that_killed_a_cell_now_parses_clean():
    out = parse_json_lenient(THE_REAL_PAYLOAD)
    blob = json.dumps(out)
    assert "\x00" not in blob and "\\u0000" not in blob
    # a space, never deletion: "0.078" and "0.300" must not fuse into "0.0780.300"
    assert out["summary"] == "Overall envelope is 0.078   0.300   0.240 m"
    assert out["acceptance"][0]["text"] == "height is 0.240 m   0.005 m"


def test_every_string_in_a_nested_structure_is_cleaned():
    dirty = {"a": "x\x00y", "b": ["p\x07q", {"c": "r\x1bs"}], "n": 3, "ok": None, "t": True}
    assert strip_control_chars(dirty) == {
        "a": "x y", "b": ["p q", {"c": "r s"}], "n": 3, "ok": None, "t": True}


def test_tab_newline_and_carriage_return_survive():
    """They carry meaning in a description or an embedded code snippet."""
    keep = "def f():\n\tx = 1\r\n"
    assert strip_control_chars(keep) == keep


def test_ordinary_unicode_is_untouched():
    """Only C0 controls go.  The glyphs the model FAILED to write are perfectly fine
    when it writes them properly, and so is every other non-ASCII character."""
    text = "0.078 × 0.300 m ± 0.005 — café 日本語 🔧"
    assert strip_control_chars(text) == text


def test_a_null_byte_can_no_longer_reach_a_subprocess_argv():
    """The end-to-end property: what comes out of the parser is safe to exec with."""
    import subprocess

    out = parse_json_lenient(THE_REAL_PAYLOAD)
    # would raise ValueError("embedded null byte") before the fix
    proc = subprocess.run(["printf", "%s", out["summary"]], capture_output=True, text=True, check=True)
    assert "0.078" in proc.stdout and "\x00" not in proc.stdout


def test_the_subprocess_backstop_names_the_offender(tmp_path):
    """If a NUL ever leaks again, the error must say WHERE — the bare
    "embedded null byte" from Popen names no argument, offset or value."""
    import pytest

    from codeverse.agents.watchdog import run_with_watchdog

    poisoned = "harness_instructions: envelope is 0.078 \x00 0.300 m"
    with pytest.raises(ValueError, match=r"argv\[2\] contains a NUL at offset \d+"):
        run_with_watchdog(["echo", "ok", poisoned], cwd=tmp_path, env=None, soft_timeout_s=5)


def test_the_anthropic_submit_tool_path_is_sanitised_too():
    """CP-2: the SDK hands `submit` tool input back already parsed, so it never met
    parse_json_lenient.  gemini/openai set ``parsed = parse_json_lenient(text)``
    unconditionally and were clean; anthropic set ``parsed = submit`` raw, so a model
    NUL reached plan.json (tracks/planner.py reads resp.parsed first) and detonated at
    Popen four stages later — the very failure this module's fix was written to end."""
    from types import SimpleNamespace as NS

    from codeverse.contracts.chat import ChatMessage, ChatRequest
    from codeverse.models.anthropic import SUBMIT_TOOL, AnthropicModel

    block = NS(type="tool_use", id="t1", name=SUBMIT_TOOL,
               input={"description": "0.078 \x00 0.300 slab"})
    reply = NS(id="m1", model="claude-opus-5", content=[block], stop_reason="tool_use",
               stop_details=None,
               usage=NS(input_tokens=10, output_tokens=2,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0))

    class FakeClient:
        def __init__(self):
            self.messages = NS(create=lambda **kw: reply)

    model = AnthropicModel("claude-opus-5", client=FakeClient(), sleep=lambda s: None)
    resp = model.generate(ChatRequest(
        messages=[ChatMessage.user("hi")],
        response_schema={"type": "object", "properties": {"description": {"type": "string"}}},
    ))
    assert "\x00" not in resp.parsed["description"]
    assert resp.parsed["description"] == "0.078   0.300 slab"
    # the text mirror the harness derives from it must be clean as well
    assert "\x00" not in (resp.text or "")


def test_the_brief_text_fallback_is_sanitised(tmp_path):
    """CP-2 (second instance): tracks/brief.py fell back to ``json.loads(resp.text)``,
    which is neither lenient nor sanitised — so a NUL in a brief field reached the plan
    the same way the anthropic submit path did.  Uses the TEXT path (parsed=None), which
    is what every provider returns when the model answers without the submit tool."""
    from codeverse.contracts.chat import ChatResponse
    from codeverse.contracts.common import Language, Track, Usage
    from codeverse.contracts.plan import EngineeringBrief, RefDimension, SubAssembly
    from codeverse.contracts.spec import Spec
    from codeverse.tracks.planner import expand_brief

    clean = EngineeringBrief(
        object_name="Grinder", reference="Peugeot 1920s box grinder",
        one_line="a wooden box grinder",
        dimensions_m=[RefDimension(name="width", meters=0.14)],
        sub_assemblies=[SubAssembly(name="burr", purpose="grinds", parts=["burr"])],
        mechanism="the crank turns the shaft",
        visible_from_outside=["the drawer seam"],
        signature_features=["open hopper", "front drawer"], materials=["body: beech"])
    payload = json.loads(clean.model_dump_json())
    payload["mechanism"] = "the crank turns 0.078 \x00 0.300 the shaft"

    class TextOnlyModel:
        """Returns JSON as TEXT with parsed=None — the json.loads fallback branch."""

        provider, model = "fake", "fake-1"

        def supports_vision(self) -> bool:
            return True

        def generate(self, request):
            return ChatResponse(text=json.dumps(payload), parsed=None, usage=Usage())

    spec = Spec(id="s1", track=Track.STATIC_OBJECT, language=Language.BLENDER,
                prompt="a hand-crank coffee grinder")
    brief, _ = expand_brief(spec, "fake:planner", model=TextOnlyModel(), cache_dir=tmp_path)
    assert brief is not None, "the brief must still be produced, only cleaned"
    assert "\x00" not in brief.mechanism
    assert brief.mechanism == "the crank turns 0.078   0.300 the shaft"
