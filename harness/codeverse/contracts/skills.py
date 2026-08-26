"""What a round did with its skills — the record shape, kept in contracts with the rest.

Lives here rather than in ``codeverse/skills`` so ``contracts`` stays a leaf package that
``flywheel``, ``gallery`` and the CLI can import without pulling the router in, and so a
stored ``record.json`` can be re-read by a build that has no skill library at all.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillRead(BaseModel):
    """The read probe's verdict for one materialised bundle (design §6.4)."""

    name: str
    surfaced: bool = Field(default=False, description="SKILL.md atime > mtime: opened by someone (a discovery scan counts)")
    deep: bool = Field(default=False, description="a references/*.md atime > mtime: the body was read and followed")
    deep_measurable: bool = Field(default=True, description="False when the bundle ships no references/ file to probe")
    body_tokens: int = 0
    first_seen_turn: int | None = Field(default=None, description="api-agent only: exact turn of the first read")
    reason: str = Field(default="", description="which route attached it, and which finding")


class SkillsUsage(BaseModel):
    """``RoundRecord.skills`` — listed vs read vs what it cost, per round.

    The metric that replaces "0 of 16 read_cookbook calls" is :attr:`deep_read_rate`.
    """

    listed: list[str] = Field(default_factory=list)
    reads: list[SkillRead] = Field(default_factory=list)
    index_tokens: int = 0
    body_tokens_read: int = Field(default=0, description="tokens of the bodies the probe says were read deep")
    inlined: str = Field(default="", description="single-shot: the body inlined into the prompt, '' otherwise")
    control_read: bool = Field(
        default=False,
        description="the never-routed control bundle was 'read' too, so this session's atime "
                    "evidence proves nothing and every rate below is an upper bound")
    control_present: bool = Field(default=False, description="a control bundle was materialised at all")

    @property
    def probe_trustworthy(self) -> bool:
        """False when the control fired — the only honest reading of the numbers below.

        A run computes ``files_changed`` through ``git add -A -N`` + ``git diff``, and git
        reads every untracked file to do it, which bumps atime on the whole bundle tree.
        CLI activation opens ``references/`` too.  Either way the probe says "read" when
        nobody chose to read, and only the control can tell you which session you are in.
        """
        return self.control_present and not self.control_read

    @property
    def surfaced(self) -> list[str]:
        return [r.name for r in self.reads if r.surfaced]

    @property
    def deep(self) -> list[str]:
        return [r.name for r in self.reads if r.deep]

    @property
    def deep_read_rate(self) -> float | None:
        """None when there is nothing listed, or when the control says the probe is blind."""
        if not self.listed or (self.control_present and self.control_read):
            return None
        return len(self.deep) / len(self.listed)


__all__ = ["SkillRead", "SkillsUsage"]
