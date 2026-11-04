"""What prompt material exists and how each piece reaches the model.

* PROMPT material: what the harness writes into a message; lives under ``codeverse3d/prompts/`` and
  is found through :func:`language_prompt`.
* AGENT-read bundles: ``codeverse3d/skills/c3d-*/``, which the agent chooses to read.
"""

from __future__ import annotations

from collections.abc import Sequence

from codeverse3d.contracts.common import Language
from codeverse3d.prompts import load_text

PROMPT_DIRS: dict[Language, str] = {Language.URDF_BLENDER: "urdf"}


def language_prompt(language: Language, name: str) -> str:
    """``<dir>/<name>`` under ``prompts/``: the path of a language's ``contract.md`` / ``system.md`` etc."""
    return f"{PROMPT_DIRS.get(language, language.value)}/{name}"


def language_text(language: Language, name: str) -> str:
    """The text of :func:`language_prompt`, or ``""`` for a file the language does not ship."""
    try:
        return load_text(language_prompt(language, name))
    except FileNotFoundError:
        return ""


def contract_sections(text: str, headings: Sequence[str]) -> str:
    """The ``## `` sections of a contract whose heading starts with one of ``headings``, in the
    contract's order, under its preamble."""
    out: list[str] = []
    keep = True
    for line in text.splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            keep = any(title.startswith(h) for h in headings)
        if keep:
            out.append(line)
    return "\n".join(out).rstrip() + "\n"
