"""AntigravityAgent: envelope parsing, argv, fake binary, live smoke."""

from __future__ import annotations

import json
import shutil

import pytest

from codeverse.agents.backends import (
    AntigravityAgent,
    available_models,
    parse_agy_json,
    resolve_model,
    usage_from_agy,
)
from codeverse.agents.cli_common import begin_session
from codeverse.contracts.agent import AgentJob
from codeverse.workspace import Workspace

ENVELOPE = {"conversation_id": "c1", "status": "SUCCESS", "response": "DONE\n", "duration_seconds": 3.0, "num_turns": 1,
            "usage": {"input_tokens": 18589, "output_tokens": 494, "thinking_tokens": 413, "cache_read_tokens": 12199, "total_tokens": 19083}}

FAKE_AGY = r'''
args = sys.argv[1:]
prompt = args[args.index("--print") + 1]
assert "--dangerously-skip-permissions" in args and "--add-dir" in args
assert "FAKE_SERVICE_API_KEY" not in os.environ
os.makedirs("src", exist_ok=True)
open("src/hello.txt", "w").write("hi")
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "text":
    print("plain text answer")
else:
    print(''' + repr(json.dumps(ENVELOPE)) + r''')
'''


#: `agy models` output, as agy 1.1.19 prints it: a model with reasoning efforts is
#: listed ONLY in its effort-suffixed spellings, one that has none is listed bare.
FAKE_AGY_MODELS = r"""
args = sys.argv[1:]
if args[:1] == ["models"]:
    print("Fetching available models...")
    print("gemini-3.7-flash-high\tGemini 3.7 Flash (High)")
    print("gemini-3.7-flash-medium\tGemini 3.7 Flash (Medium)")
    print("gemini-3.7-flash-low\tGemini 3.7 Flash (Low)")
    print("claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)")
    sys.exit(0)
# agy 1.1.19 refuses a bare id that has efforts, and refuses --effort for one that has none
model = args[args.index("--model") + 1] if "--model" in args else ""
if model in ("gemini-3.7-flash",):
    print(json.dumps({"conversation_id": "", "status": "ERROR", "response": "",
                      "error": 'invalid model selection (--model "%s" --effort ""): '
                               "--model %s requires --effort (available: low, medium, high)" % (model, model)}))
    sys.exit(0)
os.makedirs("src", exist_ok=True)
open("src/hello.txt", "w").write("hi")
print(""" + repr(json.dumps(ENVELOPE)) + r""")
"""


def test_parse_and_usage():
    env = parse_agy_json(json.dumps(ENVELOPE))
    assert env["status"] == "SUCCESS"
    assert parse_agy_json("just text") is None
    u = usage_from_agy(env, "gemini-3.6-flash-high")
    assert (u.input_tokens, u.output_tokens, u.thoughts_tokens, u.cached_tokens) == (18589, 494, 413, 12199)
    assert u.cost_usd == 0.0 and u.latency_ms == 3000


def test_argv(tmp_ws: Workspace):
    a = AntigravityAgent("gemini-3.6-flash-high", binary="agy")
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", timeout_s=1800), "agy")
    argv = a.build_argv(s, "p")
    assert argv[:3] == ["agy", "--print", "p"] and argv[argv.index("--print-timeout") + 1] == "31m"
    assert argv[argv.index("--model") + 1] == "gemini-3.6-flash-high" and argv[argv.index("--add-dir") + 1] == str(tmp_ws.root)
    assert argv[argv.index("--output-format") + 1] == "json"


def test_fake_run_json_and_text(tmp_ws: Workspace, fake_bin, monkeypatch):
    a = AntigravityAgent("gemini-3.6-flash-high", binary=fake_bin("agy", FAKE_AGY))
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="a", timeout_s=30))
    assert res.ok and res.text.strip() == "DONE" and res.usage.input_tokens == 18589, res.errors
    assert [f.path for f in res.files_changed] == ["src/hello.txt"]
    monkeypatch.setenv("FAKE_MODE", "text")
    res2 = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="b", timeout_s=30))
    assert res2.ok and res2.text == "plain text answer" and res2.usage.input_tokens == 0


@pytest.mark.live
def test_live_agy_tiny(tmp_ws: Workspace):
    if not shutil.which("agy"):
        pytest.skip("agy not installed")
    a = AntigravityAgent("gemini-3.6-flash-low")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="Use your file-writing tool to create src/hello.txt containing exactly 'hi'. Reply DONE.",
                         timeout_s=300, label="live", spatial_tools=False))
    assert res.ok, res.errors
    assert (tmp_ws.src / "hello.txt").read_text().strip() == "hi"


# --------------------------------------------------------------------------- SMOKE3
def test_a_bare_model_id_gains_the_effort_agy_requires(tmp_ws: Workspace, fake_bin):
    """SMOKE3: build_argv appended --model but never an effort, so `agy:gemini-3.7-flash`
    died with rc=1 'invalid model selection ... requires --effort'.  The effort cannot be
    a blanket --effort flag: agy rejects that outright for a model that has none, so it
    has to go into the model ID — the only spelling `agy models` lists."""
    available_models.cache_clear()
    a = AntigravityAgent("gemini-3.7-flash", binary=fake_bin("agy", FAKE_AGY_MODELS))
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", timeout_s=60), "agy")

    argv = a.build_argv(s, "p")

    assert argv[argv.index("--model") + 1] == "gemini-3.7-flash-medium"
    assert "--effort" not in argv


def test_an_effort_suffixed_or_effortless_id_is_left_alone(tmp_ws: Workspace, fake_bin):
    """Both forms already worked and must not be touched: a suffixed id is passed
    through without a lookup, and claude-sonnet-4-6 has no effort at all."""
    available_models.cache_clear()
    binary = fake_bin("agy", FAKE_AGY_MODELS)
    assert resolve_model("gemini-3.7-flash-low", binary) == "gemini-3.7-flash-low"
    assert resolve_model("claude-sonnet-4-6", binary) == "claude-sonnet-4-6"
    assert resolve_model("", binary) == ""


def test_an_unknown_model_and_an_unreachable_agy_are_passed_through(tmp_ws: Workspace, fake_bin):
    """Resolution can only turn a guaranteed failure into a run, never the reverse."""
    available_models.cache_clear()
    binary = fake_bin("agy", FAKE_AGY_MODELS)
    assert resolve_model("some-future-model", binary) == "some-future-model"
    available_models.cache_clear()
    assert resolve_model("gemini-3.7-flash", "/nonexistent/agy") == "gemini-3.7-flash"


def test_the_run_uses_and_records_the_resolved_id(tmp_ws: Workspace, fake_bin):
    """The fake refuses the bare id exactly as agy 1.1.19 does, so a green run here
    means the resolved id really was the one sent."""
    available_models.cache_clear()
    a = AntigravityAgent("gemini-3.7-flash", binary=fake_bin("agy", FAKE_AGY_MODELS))

    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="a", timeout_s=60))

    assert res.ok and res.text.strip() == "DONE", res.errors
    assert res.usage.model == "gemini-3.7-flash-medium"
