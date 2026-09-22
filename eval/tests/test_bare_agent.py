"""The bare-agent arm: same brief and contract as one-shot, a working session instead of an answer (offline)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench._bare_agent import bare_agent_prompt, run_bare_agent  # noqa: E402
from bench._oneshot import output_rule  # noqa: E402
from bench.compare_backends import parse_arm  # noqa: E402
from codeverse3d.contracts.agent import AgentJob, AgentResult  # noqa: E402
from codeverse3d.contracts.common import Language, Track  # noqa: E402
from codeverse3d.contracts.spec import Constraints, Spec  # noqa: E402
from codeverse3d.workspace import Workspace  # noqa: E402


def _spec(track: Track = Track.STATIC_OBJECT, language: Language = Language.BLENDER) -> Spec:
    return Spec(id="t/stool", track=track, language=language, prompt="a three-legged stool",
                constraints=Constraints(must_have=["round seat"]))


def test_parse_agent_arm():
    arm = parse_arm("agent:gemini-cli:gemini-3.7-flash")
    assert arm.kind == "agent" and arm.target == "gemini-cli:gemini-3.7-flash"
    assert arm.slug == "agent_gemini-cli_gemini-3.7-flash"
    with pytest.raises(ValueError):
        parse_arm("agent:nope:model")


@pytest.mark.parametrize("language,track,entry", [
    (Language.BLENDER, Track.STATIC_OBJECT, "src/model.py"),
    (Language.URDF_BLENDER, Track.ARTICULATED_OBJECT, "src/robot.urdf"),
    (Language.GLSL_SHADER, Track.GRAPHICS, "src/shader.frag"),
    (Language.SCENE_THREEJS, Track.SCENE, "src/scene.js"),
])
def test_prompt_keeps_brief_and_contract_but_not_the_no_tools_rule(language, track, entry):
    p = bare_agent_prompt(_spec(track, language), 45)
    assert "a three-legged stool" in p and "MUST HAVE: round seat" in p and entry in p
    assert output_rule(language) not in p and "NO tools" not in p and "no second chance" not in p
    assert "You have a shell" in p and "45 minutes" in p
    # nothing of the harness: no plan, no MCP tools, no cookbook, no starter library
    assert "cookbook" not in p.lower() and "mcp" not in p.lower() and "src/lib" not in p


class _FakeAgent:
    def __init__(self) -> None:
        self.jobs: list[AgentJob] = []

    def run(self, job: AgentJob) -> AgentResult:
        self.jobs.append(job)
        (Path(job.workspace) / "src" / "model.py").write_text("import bpy\n")
        return AgentResult(ok=True, exit_reason="completed")


def test_run_copies_the_deliverable_and_resumes(tmp_path, monkeypatch):
    agent = _FakeAgent()
    monkeypatch.setattr("bench._bare_agent.get_coding_agent", lambda target: agent)
    eval_ws = Workspace(tmp_path / "eval").create()
    r = run_bare_agent(_spec(), "gemini-cli:gemini-3.7-flash", tmp_path, eval_ws, minutes=10)
    assert r.ok and (eval_ws.root / "src" / "model.py").read_text() == "import bpy\n"
    job = agent.jobs[0]
    assert job.spatial_tools is False and job.timeout_s == 600 and job.write_roots == ["src", "tmp"]
    run_bare_agent(_spec(), "gemini-cli:gemini-3.7-flash", tmp_path, eval_ws, minutes=10)
    assert len(agent.jobs) == 1   # a finished session is not re-run
