"""T7 — the smoke test that a CLI upgrade cannot pass by accident (`pytest -m live`).

Everything else in this package proves we WROTE the bundles correctly.  Only a real CLI
can prove it FINDS them, and that is the failure mode with no symptom: a moved discovery
root ships an empty index, the run still passes, and the read rate reads 0% as if the
model had ignored the skill.  The design's discovery table was read out of the shipped
binaries; this test is what keeps it true after the next `npm -g update`.

The assertion is the read probe itself, so one test covers both halves: if the CLI found
and opened the bundle, ``atime > mtime`` on its ``references/`` file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.agents.registry import get_coding_agent
from codeverse3d.contracts.agent import AgentJob
from codeverse3d.skills.materialize import materialize_skills
from codeverse3d.skills.telemetry import probe_reads
from tests.skills.conftest import write_bundle

pytestmark = pytest.mark.live

PROMPT = ("Your workspace has a skill installed whose description matches this task. "
          "Activate it, read the whole skill INCLUDING every file under its references/ "
          "directory, then reply with the single magic word the reference file contains. "
          "Do not write any file.")
MAGIC = "ORTHOGONAL-KUMQUAT"
#: the model each CLI is driven with here.  ``codex`` must name a model the local login
#: is entitled to — a 400 "not supported when using Codex with a ChatGPT account" is an
#: auth problem, not a discovery problem, and would read as a discovery failure.
BACKENDS = [("gemini-cli", "gemini-3.7-flash"), ("codex", "gpt-5.6-luna"),
            ("claude-code", "claude-opus-5"), ("agy", "gemini-3.7-flash")]


@pytest.mark.parametrize("kind, model", BACKENDS, ids=[k for k, _ in BACKENDS])
def test_the_cli_discovers_and_opens_a_materialised_skill(tmp_path: Path, kind: str, model: str):
    # the binary is NOT named after the agent kind (`gemini-cli` runs `gemini`,
    # `claude-code` runs `claude`), so ask the adapter, which owns that mapping
    agent = get_coding_agent(f"{kind}:{model}")
    ok, why = agent.available()
    if not ok:
        pytest.skip(f"{kind} unavailable: {why}")
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    for name in ("AGENTS.md", "GEMINI.md", "CLAUDE.md"):
        (ws / name).write_text("# 3dcode workspace\n\nRead this file before acting.\n")

    lib = tmp_path / "library"
    write_bundle(lib, "c3d-live-probe",
                 description="Answer the magic-word question. Use when a task asks for the magic word.",
                 body="Read `references/magic.md` and reply with the word it contains.",
                 references={"magic.md": f"The magic word is {MAGIC}.\n"})
    from codeverse3d.skills import load_skill
    from codeverse3d.skills.materialize import write_index
    from codeverse3d.skills.prompting import index_block

    skill = load_skill("c3d-live-probe", lib)
    materialize_skills(ws, [skill])
    write_index(ws, index_block([skill], kind))

    res = agent.run(AgentJob(workspace=str(ws), prompt=PROMPT, spatial_tools=False,
                             max_turns=8, timeout_s=300, label="skills_live"))

    from codeverse3d.skills.model import Selection, SkillsMaterialized

    got = SkillsMaterialized(
        listed=[skill.name],
        selections=[Selection(skill=skill, priority=99, rules=("live",), reason="live smoke")])
    usage = probe_reads(ws, got)
    assert usage.surfaced == [skill.name], f"{kind} never opened SKILL.md — discovery root drift? ({res.exit_reason})"
    assert usage.deep == [skill.name], f"{kind} opened SKILL.md but not references/ ({res.text[:300]})"
    assert MAGIC in (res.text or ""), f"{kind} read the skill but did not follow it: {res.text[:300]}"
