"""What ``tracks.generation.run_agent_task`` hands a CodingAgent: the task's images as a
prompt section, a hard-clock session window, the backend's turn count."""

from __future__ import annotations

from codeverse3d.contracts.agent import AgentJob, AgentResult, FileChange
from codeverse3d.contracts.chat import ImagePart
from codeverse3d.contracts.common import Usage
from codeverse3d.tracks.generation import GenerationTask, run_agent_task
from codeverse3d.workspace import Workspace


class StubAgent:
    kind, model = "stub", "m"

    def __init__(self):
        self.jobs: list[AgentJob] = []

    def run(self, job: AgentJob) -> AgentResult:
        self.jobs.append(job)
        ws = Workspace(job.workspace)
        (ws.src / "a.txt").write_text("x\n")
        return AgentResult(ok=True, exit_reason="completed", turns=0,
                           files_changed=[FileChange(path="src/a.txt", status="added")],
                           usage=Usage(backend="stub", input_tokens=10, cost_usd=0.01))


def test_images_reach_the_agent_as_paths_in_the_prompt(tmp_ws: Workspace, tmp_path):
    """Images are staged into the workspace and listed in the prompt — no CLI takes one on argv."""
    sheet = tmp_path / "contact_sheet.png"
    sheet.write_bytes(b"png")
    task = GenerationTask(label="refine", prompt="fix the horn", round=1, kind="refine",
                          images=[ImagePart(path=str(sheet), label="the contact sheet the judge scored (round 0)"),
                                  ImagePart(path="/refs/photo.jpg", label="reference (target)")])
    agent = StubAgent()
    run_agent_task(tmp_ws, agent=agent, task=task)
    (job,) = agent.jobs
    assert job.prompt.startswith("fix the horn") and "## Images for this task" in job.prompt
    assert "`.3dcode/images/00_contact_sheet.png`" in job.prompt
    assert (tmp_ws.root / ".3dcode/images/00_contact_sheet.png").read_bytes() == b"png"
    # an unreadable source is still named, never silently dropped
    assert "reference (target): `/refs/photo.jpg`" in job.prompt
    # and only the readable one is staged: the named-but-uncopied path stays a host path
    assert sorted(p.name for p in (tmp_ws.root / ".3dcode/images").iterdir()) == ["00_contact_sheet.png"]
    run_agent_task(tmp_ws, agent=agent, task=task.model_copy(update={"images": [], "label": "r2"}))
    assert "Images for this task" not in agent.jobs[1].prompt












