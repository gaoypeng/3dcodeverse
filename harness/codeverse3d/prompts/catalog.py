"""What prompt material exists, and how each piece reaches the model.

ONE answer to a question that had six.  `<lang>/cookbook.md` was once resolved in
`languages/_docs.py`, `tracks/common.py`, `spatial/cookbook_tool.py`, `agents/materialize.py`
and `tracks/scene_assets.py`, each with its own path arithmetic — and the
``urdf_blender`` → ``prompts/urdf/`` mapping was written out three times and missing in a
fourth, which is why the articulated agent received no cookbook at all for weeks.  Until
2026-09-22 the contract still came through the runtime (``contract_doc``), the cookbook
through ``tracks/common``, the system prompt and the effects catalog through
``tracks/prompting``: every per-language prompt file is now found by :func:`language_prompt`.

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

from codeverse3d.contracts.common import Language
from codeverse3d.prompts import load_text

#: ``Language.URDF_BLENDER`` is ``"urdf_blender"``; its prompts live in ``prompts/urdf/``.
PROMPT_DIRS: dict[Language, str] = {Language.URDF_BLENDER: "urdf"}


def language_prompt(language: Language, name: str) -> str:
    """``<dir>/<name>`` under ``prompts/`` — THE path of a language's ``contract.md``,
    ``cookbook.md`` and ``system.md`` (and scene_threejs's ``effects_catalog.md`` / ``asset.md``)."""
    return f"{PROMPT_DIRS.get(language, language.value)}/{name}"


def language_text(language: Language, name: str) -> str:
    """The text of :func:`language_prompt`, or ``""`` for a file the language does not ship
    (only scene_threejs ships an effects catalog)."""
    try:
        return load_text(language_prompt(language, name))
    except FileNotFoundError:
        return ""
