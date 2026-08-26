"""One shared library, one place that knows how each backend receives it.

The owner's requirement: every coding agent shares ONE skill set, extending to a new
backend must be minimal, and the organisation has to hold up.  So the whole per-backend
policy is two bits in `skills/delivery.py` — has a native loader, and which discovery root
— and every consumer asks it rather than re-deriving.  These tests pin that.
"""

from __future__ import annotations

import pytest

from codeverse.skills.delivery import (
    AGENTS_SKILL_ROOT,
    CLAUDE_SKILL_ROOT,
    delivery_for,
    known_backends,
)


def test_a_native_loader_is_never_handed_a_second_index():
    """It lists the bundles itself; ours on top would double-index the same skills."""
    for kind in ("claude-code", "codex", "gemini-cli", "agy"):
        d = delivery_for(kind)
        assert d.native_loader and not d.needs_index and not d.needs_tool, kind


def test_a_loaderless_backend_gets_both_the_index_and_the_tool():
    """Measured 2026-08-25: the three native loaders read 5 of 5 routed bundles; api-agent
    read 0 of 5 from a MANDATORY paragraph while making 52 read_file calls.  Prose is not
    an affordance, so a backend without a loader gets the routed set as a tool."""
    d = delivery_for("api-agent")
    assert not d.native_loader and d.needs_index and d.needs_tool


def test_an_unclassified_backend_gets_the_safe_answer():
    """Over-delivering costs tokens; under-delivering costs the skill entirely."""
    for kind in ("some-future-cli", "", "typo-agent"):
        d = delivery_for(kind)
        assert d.needs_index and d.needs_tool, kind
        assert d.root == AGENTS_SKILL_ROOT


def test_a_qualified_kind_resolves_to_its_backend():
    """`codex:gpt-5.6-sol` is codex; the model suffix must not fall through to unknown."""
    assert delivery_for("codex:gpt-5.6-sol") == delivery_for("codex")
    assert delivery_for("api-agent:gemini:gemini-3.7-flash") == delivery_for("api-agent")


def test_claude_code_reads_its_own_root_and_nobody_else_does():
    """Read out of the shipped binaries, not assumed: claude-code 2.1 has no `.agents`
    skill root at all, and the other three have no `.claude` one."""
    assert delivery_for("claude-code").root == CLAUDE_SKILL_ROOT
    for kind in ("codex", "gemini-cli", "agy", "api-agent"):
        assert delivery_for(kind).root == AGENTS_SKILL_ROOT, kind


@pytest.mark.parametrize("kind", known_backends())
def test_every_consumer_agrees_with_the_policy(kind):
    """`prompting` must not re-derive what `delivery` decides — one source, or they drift."""
    from codeverse.skills.prompting import NATIVE_LOADERS, skill_path

    d = delivery_for(kind)
    assert (kind in NATIVE_LOADERS) is d.native_loader
    assert skill_path("cv3d-x", agent_kind=kind) == f"{d.root}/cv3d-x/SKILL.md"


def test_adding_a_backend_is_one_row():
    """The guard behind the claim: if per-backend knowledge spreads back out of
    delivery.py, this catches it.  `prompting` may name the backends it re-exports."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "codeverse"
    names = ("claude-code", "gemini-cli", "api-agent")
    offenders = []
    for py in (root / "skills").rglob("*.py"):
        if py.name in ("delivery.py", "prompting.py"):
            continue
        text = py.read_text()
        if any(f'"{n}"' in text or f"'{n}'" in text for n in names):
            offenders.append(py.name)
    assert not offenders, f"per-backend knowledge leaked out of delivery.py into {offenders}"


# ------------------------------------------------------------------ the read_skill tool
def _workspace_with_skills(tmp_path, agent_kind="api-agent"):
    from codeverse.agents.materialize import materialize_workspace
    from codeverse.skills import attach_skills
    from codeverse.workspace import Workspace

    ws = Workspace(tmp_path / "ws")
    ws.create()
    materialize_workspace(ws, agent_kind=agent_kind, contract_md="(contract)",
                          cookbook_rel=".c3v/cookbook.md", spatial_tools=False, mcp_command=[])
    attach_skills(ws.root, track="static_object", language="blender", kind="baseline",
                  agent_kind=agent_kind)
    return ws


def test_the_tool_discovers_the_routed_set_off_the_workspace(tmp_path):
    """Not threaded through AgentJob: the bundles are already on disk for this round, so
    the tool reads what was actually written and cannot disagree with it."""
    from codeverse.agents.api_skills import SkillTools

    st = SkillTools(_workspace_with_skills(tmp_path))
    assert st.listed, "nothing discovered"
    spec = st.specs()[0]
    assert spec.name == "read_skill"
    assert spec.parameters["properties"]["name"]["enum"] == st.listed
    for name in st.listed:
        assert name in spec.description, "the model must be able to choose without opening one"


def test_the_read_control_is_never_offered(tmp_path):
    """It exists to catch a probe reporting a read nobody made; offering it would invite
    exactly that."""
    from codeverse.agents.api_skills import SkillTools
    from codeverse.skills.materialize import CONTROL_NAME

    assert CONTROL_NAME not in SkillTools(_workspace_with_skills(tmp_path)).listed


def test_reading_records_ground_truth_and_rejects_an_unrouted_name(tmp_path):
    from codeverse.agents.api_skills import SkillTools

    st = SkillTools(_workspace_with_skills(tmp_path))
    first = st.listed[0]
    out = st.read_skill(first)
    assert not out.is_error and len(out.text) > 200
    assert st.reads == [first], "the read rate needs ground truth, not a probe"
    bad = st.read_skill("cv3d-not-routed")
    assert bad.is_error and "not routed" in bad.text
    assert st.reads == [first], "a rejected call must not count as a read"


def test_a_workspace_with_no_skills_advertises_no_tool(tmp_path):
    """The feature being off must not leave a tool that always errors."""
    from codeverse.agents.api_skills import SkillTools
    from codeverse.workspace import Workspace

    ws = Workspace(tmp_path / "bare")
    ws.create()
    assert SkillTools(ws).specs() == []
