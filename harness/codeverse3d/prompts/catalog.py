"""What prompt material exists, and how each piece reaches the model.

ONE answer to a question that had six.  A per-language prompt file was once resolved in
`languages/_docs.py`, `tracks/common.py`, `agents/materialize.py` and `tracks/scene_assets.py`,
each with its own path arithmetic — and the ``urdf_blender`` → ``prompts/urdf/`` mapping was
written out three times and missing in a fourth, which is why the articulated agent received
part of its prompt material late for weeks.  Until 2026-09-22 the contract still came through
the runtime (``contract_doc``), the system prompt and the effects catalog through
``tracks/prompting``: every per-language prompt file (``contract.md`` first) is now found by
:func:`language_prompt`.

The split this file encodes (owner, 2026-08-28):

* **PROMPT material** — everything the harness writes and the harness puts in a message.
  It lives here, under ``codeverse3d/prompts/``, and modules load it on demand through this
  catalog rather than building paths.
* **AGENT-read bundles** — ``codeverse3d/skills/c3d-*/`` (SKILL.md + references/ + a
  ``_claims`` file pinning its numbers to live constants).  The agent decides to read
  those; the harness only materialises them.

A file that tries to be both is the thing this split exists to prevent.
"""

from __future__ import annotations

from collections.abc import Sequence

from codeverse3d.contracts.common import Language
from codeverse3d.prompts import load_text

#: ``Language.URDF_BLENDER`` is ``"urdf_blender"``; its prompts live in ``prompts/urdf/``.
PROMPT_DIRS: dict[Language, str] = {Language.URDF_BLENDER: "urdf"}


def language_prompt(language: Language, name: str) -> str:
    """``<dir>/<name>`` under ``prompts/`` — THE path of a language's ``contract.md`` and
    ``system.md`` (and the scene languages' ``effects_catalog.md`` / ``asset.md``)."""
    return f"{PROMPT_DIRS.get(language, language.value)}/{name}"


def language_text(language: Language, name: str) -> str:
    """The text of :func:`language_prompt`, or ``""`` for a file the language does not ship
    (only the two scene languages ship an effects catalog / ``asset.md``).  No language ships
    a cookbook (D107, owner 2026-10-15): interface and language facts are in ``contract.md`` /
    ``system.md``, the craft in the skills as short optional practices."""
    try:
        return load_text(language_prompt(language, name))
    except FileNotFoundError:
        return ""


def contract_sections(text: str, headings: Sequence[str]) -> str:
    """The ``## `` sections of a contract whose heading starts with one of ``headings``, in the
    contract's own order, under its preamble (the ``# `` title and anything before the first
    ``## ``).  A session that writes one asset module, or a planner that writes JSON, is handed
    the sections it acts on — the files, the gates, the lint — not the whole contract (the
    copyability review of 2026-10-15: the asset brief had grown 52 % by carrying the wiring
    example of modules it must not write)."""
    out: list[str] = []
    keep = True
    for line in text.splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            keep = any(title.startswith(h) for h in headings)
        if keep:
            out.append(line)
    return "\n".join(out).rstrip() + "\n"
