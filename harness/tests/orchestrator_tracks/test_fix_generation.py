"""Generation parsing, retry, write filtering, and agent attribution."""

from __future__ import annotations

from codeverse3d.contracts.agent import AgentResult
from codeverse3d.contracts.chat import ChatResponse
from codeverse3d.contracts.common import Usage
from codeverse3d.proc import EventLog
from codeverse3d.tracks.generation import (
    GenerationTask,
    generate_files,
    parse_multifile,
    run_agent_task,
)
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.fakes import FakeChatModel

FULL = "=== FILE: src/model.py ===\nimport bpy\nprint('ok')\n=== END FILE ==="
TRUNCATED = "=== FILE: src/model.py ===\nimport bpy\nbpy.ops.mesh.primitive_cube_add(size=0.4, location=(0, 0,"


def scripted(answers) -> FakeChatModel:
    """A chat model answering ``(text, finish_reason)`` pairs in order."""
    return FakeChatModel([ChatResponse(text=t, usage=Usage(backend="fake", cost_usd=0.001), finish_reason=fr) for t, fr in answers])


# --------------------------------------------------------------------- finding: truncated single-shot answer written verbatim
def test_parse_multifile_strips_header_from_unterminated_block():
    files = parse_multifile(TRUNCATED, expected_files=["src/model.py"])
    body = files["src/model.py"]
    assert body.startswith("import bpy"), body  # NOT the '=== FILE:' header line
    assert "=== FILE:" not in body


def test_generate_files_truncation_policy(tmp_path):
    ws = Workspace(tmp_path / "success").create()
    model = scripted([(TRUNCATED, "MAX_TOKENS"), (FULL, "STOP")])
    events = EventLog(tmp_path / "success.jsonl")
    task = GenerationTask(label="baseline", prompt="p", files_hint=["src/model.py"], max_output_tokens=32000)
    res = generate_files(ws, model=model, task=task, events=events)
    assert res.ok and (ws.root / "src" / "model.py").read_text().startswith("import bpy")
    assert len(model.requests) == 2 and model.requests[1].max_output_tokens == 64000
    assert "generate.truncated" in [e["event"] for e in events.read()]

    ws = Workspace(tmp_path / "failure").create()
    model = scripted([(TRUNCATED, "MAX_TOKENS"), (TRUNCATED, "length")])
    task = GenerationTask(label="baseline", prompt="p", files_hint=["src/model.py"], max_output_tokens=32000)
    res = generate_files(ws, model=model, task=task, events=EventLog(tmp_path / "failure.jsonl"))
    assert not res.ok and res.notes.startswith("truncated")
    assert not (ws.root / "src" / "model.py").exists()

    ws = Workspace(tmp_path / "ceiling").create()
    model = scripted([(TRUNCATED, "MAX_TOKENS")])
    task = GenerationTask(label="baseline", prompt="p", files_hint=["src/model.py"])
    events = EventLog(tmp_path / "ceiling.jsonl")
    res = generate_files(ws, model=model, task=task, events=events)
    assert not res.ok and res.notes.startswith("truncated")
    assert len(model.requests) == 1
    assert [e["event"] for e in events.read()].count("generate.truncated") == 1   # one cut, one event


# --------------------------------------------------------------------- finding: out-of-root path aborted the whole write
def test_out_of_root_paths_are_skipped_not_fatal(tmp_path):
    ws = Workspace(tmp_path / "ws").create()
    answer = (
        "=== FILE: src/model.py ===\nimport bpy\n=== END FILE ===\n"
        "=== FILE: README.md ===\n# notes\n=== END FILE ===\n"
        "=== FILE: package.json ===\n{}\n=== END FILE ===\n"
        "=== FILE: ../evil.py ===\nx = 1\n=== END FILE ==="
    )
    events = EventLog(tmp_path / "e.jsonl")
    task = GenerationTask(label="baseline", prompt="p", files_hint=["src/model.py"])
    res = generate_files(ws, model=scripted([(answer, "STOP")]), task=task, events=events)
    assert res.ok, res.notes  # the good file was paid for — never abort the round on a README
    assert (ws.root / "src" / "model.py").is_file()
    assert not (ws.root / "README.md").exists() and not (tmp_path / "evil.py").exists()
    assert [c.path for c in res.files_changed] == ["src/model.py"]
    assert "README.md" in res.notes
    skipped = [e for e in events.read() if e["event"] == "generate.skipped_path"]
    assert {e["path"] for e in skipped} == {"README.md", "package.json", "../evil.py"}
    done = next(e for e in events.read() if e["event"] == "generate.done")
    assert done["files"] == ["src/model.py"]


# --------------------------------------------------------------------- finding: whole-workspace diff claimed sibling tasks' files
class NonReportingAgent:
    """An agent that writes its own file but reports no files_changed (fallback path)."""

    kind = "fake"
    model = "m"

    def __init__(self, files):
        self.files = files
        self.jobs = []

    def run(self, job):
        self.jobs.append(job)
        ws = Workspace(job.workspace)
        for rel, content in self.files.items():
            p = ws.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return AgentResult(ok=True, exit_reason="completed", files_changed=[], usage=Usage(backend="fake", cost_usd=0.001))


def test_run_agent_task_passes_files_hint_and_attributes_fallback_diff(tmp_path):
    ws = Workspace(tmp_path / "ws").create()
    ws.commit("init")
    # noise a fallback whole-tree diff used to claim as the task's own work
    (ws.root / "events.jsonl").write_text("{}\n")
    (ws.root / "notes.md").write_text("scratch\n")
    (ws.root / "artifacts").mkdir(exist_ok=True)
    (ws.root / "artifacts" / "x.json").write_text("{}")
    agent = NonReportingAgent({"src/parts/seat.js": "export function buildSeat(){}\n"})
    task = GenerationTask(label="refine_seat", prompt="p", files_hint=["src/parts/seat.js"], round=1, kind="refine")
    res = run_agent_task(ws, agent=agent, task=task)
    assert agent.jobs[0].files_hint == ["src/parts/seat.js"]  # per-session attribution key
    assert res.ok and [c.path for c in res.files_changed] == ["src/parts/seat.js"]


def test_run_agent_task_that_wrote_nothing_is_not_ok(tmp_path):
    ws = Workspace(tmp_path / "ws").create()
    ws.commit("init")
    (ws.root / "events.jsonl").write_text("{}\n")  # harness noise only
    agent = NonReportingAgent({})
    task = GenerationTask(label="asset_stone_lantern", prompt="p", files_hint=["src/assets/stone_lantern.js"])
    res = run_agent_task(ws, agent=agent, task=task)
    assert not res.ok and res.files_changed == []
