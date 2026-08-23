"""AntigravityAgent: envelope parsing, argv, fake binary, live smoke."""

from __future__ import annotations

import json
import shutil

import pytest

from codeverse.agents.antigravity import AntigravityAgent, parse_agy_json, usage_from_agy
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
