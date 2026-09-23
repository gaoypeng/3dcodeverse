"""One shared library, one place that knows how each backend receives it.

The owner's requirement: every coding agent shares ONE skill set, extending to a new
backend must be minimal, and the organisation has to hold up.  So the whole per-backend
policy is one row per backend in `skills/prompting.py` — which discovery root — and every
consumer asks it rather than re-deriving.  These tests pin that.
"""

from __future__ import annotations

from codeverse3d.skills.prompting import AGENTS_SKILL_ROOT, CLAUDE_SKILL_ROOT, skill_path


def test_a_qualified_kind_resolves_to_its_backend():
    """`codex:gpt-5.6-sol` is codex; the model suffix must not fall through to unknown."""
    assert skill_path("x", agent_kind="codex:gpt-5.6-sol") == skill_path("x", agent_kind="codex")
    assert skill_path("x", agent_kind="claude-code:opus") == skill_path("x", agent_kind="claude-code")


def test_claude_code_reads_its_own_root_and_nobody_else_does():
    """Read out of the shipped binaries, not assumed: claude-code 2.1 has no `.agents`
    skill root at all, and the other three have no `.claude` one; an unlisted kind (a
    test's fake agent) reads the shared one."""
    assert skill_path("c3d-x", agent_kind="claude-code") == f"{CLAUDE_SKILL_ROOT}/c3d-x/SKILL.md"
    for kind in ("codex", "gemini-cli", "agy", "some-future-cli", ""):
        assert skill_path("c3d-x", agent_kind=kind) == f"{AGENTS_SKILL_ROOT}/c3d-x/SKILL.md", kind


def test_adding_a_backend_is_one_row():
    """The guard behind the claim: if per-backend knowledge spreads back out of
    prompting.py, this catches it."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "codeverse3d"
    names = ("claude-code", "gemini-cli", "codex")
    offenders = []
    for py in (root / "skills").rglob("*.py"):
        if py.name == "prompting.py":
            continue
        text = py.read_text()
        if any(f'"{n}"' in text or f"'{n}'" in text for n in names):
            offenders.append(py.name)
    assert not offenders, f"per-backend knowledge leaked out of prompting.py into {offenders}"
