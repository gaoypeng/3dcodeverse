"""``AgentJob.read_only`` / ``FileTools(read_only=…)``: a harness-owned file inside the write roots is
readable, never writable.

Measured 2026-08-26 (bench/out/seed_v1, aurora brief, gemini-3.7-flash api-agent, seeding ON): the
``recipes.seeded`` event fired and the finished run's ``src/common.glsl`` carried no seeded block and
no ``curtain(`` — the agent overwrote the file with its own helpers.  Verified code the agent can
rewrite is not verified for long; ``src/recipes.glsl`` is therefore a file it can only call into.
"""

from __future__ import annotations

import pytest

from codeverse.agents.api_tools import FileTools, PathDenied
from codeverse.contracts.agent import AgentJob, AgentResult
from codeverse.contracts.common import HARNESS_OWNED_SRC, Language
from codeverse.tracks.generation import GenerationTask, run_agent_task
from codeverse.workspace import Workspace

RECIPES = "src/recipes.glsl"
SEEDED = "// harness-owned\nfloat curtain(vec2 p) { return 0.0; }\n"


def _ws(tmp_path):
    ws = Workspace(tmp_path / "run").create()
    ws.src.mkdir(parents=True, exist_ok=True)
    (ws.src / "recipes.glsl").write_text(SEEDED)
    (ws.src / "shader.frag").write_text("void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(1.0); }\n")
    return ws


def test_a_read_only_file_can_be_read_but_not_written_or_edited(tmp_path):
    ws = _ws(tmp_path)
    ft = FileTools(ws, ["src", "public"], read_only=[RECIPES])
    assert ft.read_file(RECIPES).text == SEEDED and not ft.read_file(RECIPES).is_error
    with pytest.raises(PathDenied) as ei:
        ft.write_file(RECIPES, "float curtain(vec2 p) { return 1.0; }\n")
    assert "harness-owned" in str(ei.value) and "call them" in str(ei.value) and RECIPES in str(ei.value)
    with pytest.raises(PathDenied, match="harness-owned"):
        ft.edit_file(RECIPES, "0.0", "1.0")
    assert (ws.src / "recipes.glsl").read_text() == SEEDED, "nothing was written"
    assert ft.read_only_denials == [RECIPES, RECIPES] and ft.scope_denials == []
    # the agent loop sees a tool error, not an exception; the other files stay writable
    out = ft.call("write_file", {"path": RECIPES, "content": "x"})
    assert out.is_error and "harness-owned" in out.text
    assert not ft.write_file("src/shader.frag", "void mainImage(out vec4 f, in vec2 c) { f = vec4(0.5); }\n").is_error
    assert not ft.write_file("src/common.glsl", "float mine(float x) { return x; }\n").is_error


def test_read_only_holds_under_edit_only_too(tmp_path):
    """A refine session scoped to the recipe file still cannot write it: read_only beats edit_only."""
    ws = _ws(tmp_path)
    ft = FileTools(ws, ["src", "public"], edit_only=[RECIPES], always_writable=[RECIPES], read_only=[RECIPES])
    with pytest.raises(PathDenied, match="harness-owned"):
        ft.write_file(RECIPES, "x")
    assert FileTools(ws, ["src"]).read_only == frozenset() and not FileTools(ws, ["src"]).write_file(RECIPES, SEEDED).is_error


class _CaptureAgent:
    kind = "fake"
    model = "fake"
    id = "fake:fake"

    def __init__(self):
        self.jobs: list[AgentJob] = []

    def available(self):
        return True, "ok"

    def run(self, job: AgentJob) -> AgentResult:
        self.jobs.append(job)
        return AgentResult(ok=False, exit_reason="completed")


@pytest.mark.parametrize(("language", "expected"), [("glsl_shader", [RECIPES]), ("blender", []), ("opengl_python", []), ("", [])])
def test_the_job_carries_the_languages_harness_owned_files(tmp_path, language, expected):
    ws = _ws(tmp_path)
    if language:
        ws.write_json(ws.spec_path, {"prompt": "aurora", "track": "graphics" if language != "blender" else "static_object", "language": language})
    agent = _CaptureAgent()
    run_agent_task(ws, agent=agent, task=GenerationTask(label="baseline", prompt="p", round=0, kind="baseline"), retry_silent_bail=False)
    assert agent.jobs[-1].read_only == expected
    assert AgentJob(workspace="w", prompt="p").read_only == [] and HARNESS_OWNED_SRC == {Language.GLSL_SHADER: (RECIPES,)}
