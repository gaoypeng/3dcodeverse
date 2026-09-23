"""Each backend's skill discovery root is one row in skills/prompting.py."""

from __future__ import annotations

from codeverse3d.skills.prompting import AGENTS_SKILL_ROOT, CLAUDE_SKILL_ROOT, skill_path


def test_claude_code_reads_its_own_root_and_nobody_else_does():
    """claude-code 2.1 has no `.agents` root, the others no `.claude` one; a model suffix never matters."""
    assert skill_path("c3d-x", agent_kind="claude-code:opus") == f"{CLAUDE_SKILL_ROOT}/c3d-x/SKILL.md"
    for kind in ("codex:gpt-5.6-sol", "gemini-cli", "agy", "some-future-cli", ""):
        assert skill_path("c3d-x", agent_kind=kind) == f"{AGENTS_SKILL_ROOT}/c3d-x/SKILL.md", kind
