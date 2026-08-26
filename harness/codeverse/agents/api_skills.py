"""``read_skill`` — the routed skill bundles as a TOOL, for the in-process api-agent.

WHY a tool and not prose.  Every CLI backend has a native skill loader, and measured on a
real build task codex, claude-code and agy each read **5 of 5** routed bundles unprompted.
api-agent read **0 of 5** on the same library, in the same workspace, while making 52
``read_file`` calls — so it was reading plenty, just never a skill.  Two reasons it could
not, both structural rather than a matter of persuasion:

* the bundles live in ``.agents/skills/`` and ``.claude/skills/``, which are HIDDEN
  directories, and ``list_files`` deliberately skips hidden paths — the agent cannot
  discover them by looking;
* everything else it knew came from tool specs, while the skills were a paragraph in a
  2.3 kB system prompt competing with the contract, the rules and the cookbook pointer.

So the index becomes an affordance: the routed names are in the tool's own description,
where the model reads its options, and opening one is a tool call like any other.  The
AGENTS.md index stays as well — belt and braces, and it is what a native loader reads.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from codeverse.agents.api_tools import ToolOutcome, _schema
from codeverse.contracts.chat import ToolSpec

#: every discovery root the library writes to; first match wins (the same bytes go to all
#: of them, so a loaderless backend can read whichever exists)
from codeverse.skills.delivery import AGENTS_SKILL_ROOT, CLAUDE_SKILL_ROOT
from codeverse.workspace import Workspace

SKILL_ROOTS = (AGENTS_SKILL_ROOT, CLAUDE_SKILL_ROOT)
#: a body is capped like read_file's — a skill that cannot fit is a skill to split
READ_CAP_CHARS = 24_000
#: how much of each description the tool spec repeats, per skill
SPEC_SUMMARY_CHARS = 150

__all__ = ["SKILL_ROOTS", "SkillTools"]


class SkillTools:
    """``read_skill`` over the bundles already materialised into ``ws``."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.listed, self.summaries = self._discover()
        self.reads: list[str] = []  # names actually opened, in order — the read-rate ground truth

    def _discover(self) -> tuple[list[str], dict[str, str]]:
        """Read the routed set off the workspace rather than being told it.

        The bundles are already there — materialize.py wrote them for this round — so
        discovering them here keeps the tool from ever disagreeing with what is on disk,
        and needs no field threaded through AgentJob and the generation task.  The read
        CONTROL bundle is excluded: it exists to catch a probe that reports a read nobody
        made, and offering it as a choice would be inviting exactly that.
        """
        from codeverse.skills.materialize import CONTROL_NAME

        names: dict[str, str] = {}
        for root in SKILL_ROOTS:
            d = Path(self.ws.root) / root
            if not d.is_dir():
                continue
            for bundle in sorted(d.iterdir()):
                name = bundle.name
                if name == CONTROL_NAME or name in names or not (bundle / "SKILL.md").is_file():
                    continue
                names[name] = _description_of(bundle / "SKILL.md")
        return list(names), names

    # ------------------------------------------------------------------ specs
    def specs(self) -> list[ToolSpec]:
        if not self.listed:
            return []
        lines = [f"  - {n}: {self.summaries.get(n, '')[:SPEC_SUMMARY_CHARS]}".rstrip(": ") for n in self.listed]
        desc = (
            "Read one of the skill bundles the harness routed for THIS task — chosen from your "
            "track, language, plan and the previous round's gate findings. Read every one before "
            "writing code; they carry the rules this harness's gates actually enforce.\n"
            + "\n".join(lines)
        )
        return [ToolSpec(
            name="read_skill", description=desc,
            parameters=_schema({"name": {"type": "string", "description": "skill name, exactly as listed",
                                         "enum": list(self.listed)}}, ["name"]),
        )]

    # ------------------------------------------------------------------ tool
    def read_skill(self, name: str) -> ToolOutcome:
        if not isinstance(name, str) or not name.strip():
            return ToolOutcome("name must be one of: " + ", ".join(self.listed), is_error=True)
        name = name.strip()
        if name not in self.listed:
            return ToolOutcome(f"{name!r} was not routed for this task; available: {', '.join(self.listed)}",
                               is_error=True)
        body = self._body(name)
        if body is None:
            return ToolOutcome(f"skill {name!r} is listed but its SKILL.md is missing from the workspace",
                               is_error=True)
        self.reads.append(name)
        return ToolOutcome(body)

    def dispatch(self, name: str, args: dict[str, Any]) -> ToolOutcome | None:
        """``None`` when this toolbox does not own ``name`` (the caller keeps looking)."""
        if name != "read_skill":
            return None
        return self.read_skill(str(args.get("name", "")))

    # ------------------------------------------------------------------ internals
    def _body(self, name: str) -> str | None:
        for root in SKILL_ROOTS:
            p = Path(self.ws.root) / root / name / "SKILL.md"
            if p.is_file():
                text = p.read_text(errors="replace")
                extra = self._references(p.parent)
                if extra:
                    text += "\n\n" + extra
                return text[:READ_CAP_CHARS] + (
                    f"\n... [truncated at {READ_CAP_CHARS} chars]" if len(text) > READ_CAP_CHARS else "")
        return None

    @staticmethod
    def _references(bundle: Path) -> str:
        """The spec puts depth in ``references/``; name them so a body is not a dead end."""
        ref = bundle / "references"
        if not ref.is_dir():
            return ""
        names = sorted(p.name for p in ref.iterdir() if p.is_file())
        if not names:
            return ""
        rel = os.path.relpath(ref, bundle.parents[2]) if len(bundle.parents) > 2 else str(ref)
        return "Further reading in this bundle (open with read_file):\n" + "\n".join(
            f"  {rel}/{n}" for n in names)


def _description_of(skill_md: Path) -> str:
    """The frontmatter ``description``, without importing the loader (which validates the
    whole bundle and would turn one malformed skill into a session with no tool at all)."""
    try:
        text = skill_md.read_text(errors="replace")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    front = text[3:end] if end > 0 else ""
    out, grabbing = [], False
    for line in front.splitlines():
        if line.startswith("description:"):
            out.append(line.split(":", 1)[1].strip())
            grabbing = True
        elif grabbing and line[:1] in (" ", "\t"):
            out.append(line.strip())
        elif grabbing:
            break
    return " ".join(out).strip().strip("'\"")
