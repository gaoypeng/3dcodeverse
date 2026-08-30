"""What ``tracks.generation.run_agent_task`` hands a CodingAgent: the task's images as a
prompt section, a hard-clock session window, the backend's turn count."""

from __future__ import annotations

from codeverse.contracts.agent import AgentJob, AgentResult
from codeverse.contracts.chat import ImagePart
from codeverse.contracts.common import Budget, Usage
from codeverse.orchestrator import BudgetGuard
from codeverse.tracks.generation import GenerationTask, run_agent_task
from codeverse.workspace import Workspace


class StubAgent:
    kind, model = "stub", "m"

    def __init__(self, turns: int = 0):
        self.jobs: list[AgentJob] = []
        self.turns = turns

    def run(self, job: AgentJob) -> AgentResult:
        self.jobs.append(job)
        ws = Workspace(job.workspace)
        (ws.src / "a.txt").write_text("x\n")
        return AgentResult(ok=True, exit_reason="completed", turns=self.turns,
                           usage=Usage(backend="stub", input_tokens=10, cost_usd=0.01))


def test_images_reach_the_agent_as_paths_in_the_prompt(tmp_ws: Workspace, tmp_path):
    """No vendor CLI takes an image on argv: the contact sheet the judge scored (and the
    reference photos) are copied into the workspace and listed for the agent's own file
    tools — until 2026-08-29 ``AgentJob.images`` was filled and read by nobody.  The copy
    is what makes the read possible: a host path is outside every CLI's workspace, and the
    sheet's own home (``artifacts/renders/``) is hidden by ``.geminiignore``."""
    sheet = tmp_path / "contact_sheet.png"
    sheet.write_bytes(b"png")
    task = GenerationTask(label="refine", prompt="fix the horn", round=1, kind="refine",
                          images=[ImagePart(path=str(sheet), label="the contact sheet the judge scored (round 0)"),
                                  ImagePart(path="/refs/photo.jpg", label="reference (target)")])
    agent = StubAgent()
    run_agent_task(tmp_ws, agent=agent, task=task)
    (job,) = agent.jobs
    assert job.prompt.startswith("fix the horn") and "## Images for this task" in job.prompt
    assert "`.3dcv/images/00_contact_sheet.png`" in job.prompt
    assert (tmp_ws.root / ".3dcv/images/00_contact_sheet.png").read_bytes() == b"png"
    # an unreadable source is still named, never silently dropped
    assert "reference (target): `/refs/photo.jpg`" in job.prompt
    assert job.images and job.images[0].path == str(sheet)
    run_agent_task(tmp_ws, agent=agent, task=task.model_copy(update={"images": [], "label": "r2"}))
    assert "Images for this task" not in agent.jobs[1].prompt


def test_session_window_is_clipped_to_the_hard_clock_not_the_soft_share(tmp_ws: Workspace):
    """A scene refine asks for its window with soft=False (after the 0.55 soft share is
    spent); re-clipping it against the SOFT share handed it the 120 s floor (2026-08-29)."""
    guard = BudgetGuard(Budget(max_minutes=100), soft_fraction=0.5)
    guard.start_time -= 60 * 60  # 60 of 100 minutes gone: the soft share is spent, 40 min remain
    assert guard.soft_remaining()["minutes"] == 0
    agent = StubAgent()
    run_agent_task(tmp_ws, agent=agent, task=GenerationTask(label="refine", prompt="p", timeout_s=900), budget=guard)
    assert agent.jobs[0].timeout_s == 900


def test_turns_come_from_the_typed_result(tmp_ws: Workspace):
    res = run_agent_task(tmp_ws, agent=StubAgent(turns=7), task=GenerationTask(label="baseline", prompt="p"))
    assert res.turns == 7
