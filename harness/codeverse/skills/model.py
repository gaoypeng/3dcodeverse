"""Typed shapes for the skill library — one bundle, one selection, one session's usage.

Frozen on purpose: a ``Skill`` is parsed from a file on disk once and then flows into
prompts, workspaces and the run record.  If any of those could mutate it, the body we
measured (``body_tokens``) and the body we materialised could silently differ, and the
telemetry in §6.4 of the design would be measuring a file nobody sent.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from codeverse.contracts.skills import SkillRead, SkillsUsage
from codeverse.cost.guard import text_tokens

#: `metadata.evidence` values.  A bundle whose claims are carried over from a reference
#: library but which our own corpus cannot back yet is `inherited-unverified`, and the
#: router refuses it unless CV3D_SKILLS_UNVERIFIED is on (design §5.2 law 3).
EVIDENCE_MEASURED = "measured"
EVIDENCE_INHERITED = "inherited-unverified"
EVIDENCE_MIXED = "mixed"
EVIDENCE_LEVELS = (EVIDENCE_MEASURED, EVIDENCE_MIXED, EVIDENCE_INHERITED)


class Skill(BaseModel):
    """One SKILL.md bundle, parsed and validated against the open spec (agentskills.io)."""

    model_config = {"frozen": True}

    name: str = Field(description="lowercase-hyphen id; equals the parent directory name")
    description: str = Field(description="what it does AND when to use it; the only text a CLI indexes")
    body: str = Field(default="", description="everything after the frontmatter")
    license: str = ""
    compatibility: str = ""
    metadata: dict[str, str] = Field(default_factory=dict)
    dir: Path = Field(description="the bundle directory (contains SKILL.md)")
    references: tuple[str, ...] = Field(default_factory=tuple, description="references/*.md, bundle-relative")

    @property
    def path(self) -> Path:
        return self.dir / "SKILL.md"

    @property
    def body_tokens(self) -> int:
        """Estimated prompt tokens of the body — the number the budget test asserts."""
        return text_tokens(self.body)

    @property
    def body_lines(self) -> int:
        return len(self.body.splitlines())

    @property
    def evidence(self) -> str:
        return self.metadata.get("evidence", EVIDENCE_INHERITED)

    @property
    def verified(self) -> str:
        """ISO date the bundle's claims were last checked against the harness."""
        return self.metadata.get("verified", "")

    @property
    def when_to_use(self) -> str:
        """Optional long form of the *when* half of ``description`` (metadata key)."""
        return self.metadata.get("when_to_use", "")

    def ref(self) -> SkillRef:
        return SkillRef(name=self.name, description=self.description, body_tokens=self.body_tokens)


class SkillRef(BaseModel):
    """A skill without its body — what the index block and the run record carry."""

    model_config = {"frozen": True}

    name: str
    description: str
    body_tokens: int = 0


class Selection(BaseModel):
    """One routed skill **with the reason it was routed**, so telemetry can say WHY.

    astra3d had a human type ``skills: [...]``; we derive the set, which is only an
    improvement if the derivation is auditable after the fact.
    """

    model_config = {"frozen": True}

    skill: Skill
    priority: int
    rules: tuple[str, ...] = Field(description="rule ids that fired, e.g. ('R1', 'R2')")
    reason: str = Field(description="one human line: which rule, and which finding if any")

    @property
    def name(self) -> str:
        return self.skill.name

    @property
    def gate_fired(self) -> bool:
        """True when a previous-round gate finding routed this (design §5.2 law 2)."""
        return self.priority >= 90


class SkillsMaterialized(BaseModel):
    """What :func:`codeverse.skills.materialize.materialize_skills` wrote into a workspace."""

    listed: list[str] = Field(default_factory=list, description="skill names, in attach order")
    selections: list[Selection] = Field(default_factory=list, description="the routed rows, with the reason each fired")
    paths: list[str] = Field(default_factory=list, description="every SKILL.md written (both roots)")
    reasons: dict[str, str] = Field(default_factory=dict, description="name → why it was attached")
    index_tokens: int = Field(default=0, description="estimated tokens the index/mandate adds to message 0")
    inlined: str = Field(default="", description="single-shot: the one body inlined, '' otherwise")
    warnings: list[str] = Field(default_factory=list)


__all__ = ["EVIDENCE_INHERITED", "EVIDENCE_LEVELS", "EVIDENCE_MEASURED", "EVIDENCE_MIXED",
           "Selection", "Skill", "SkillRead", "SkillRef", "SkillsMaterialized", "SkillsUsage"]
