"""What prompt material exists, and how each piece reaches the model.

ONE answer to a question that had six.  Before this, `<lang>/cookbook.md` was resolved in
`languages/_docs.py`, `tracks/common.py`, `spatial/cookbook_tool.py`, `agents/materialize.py`
and `tracks/scene_assets.py`, each with its own path arithmetic — and the
``urdf_blender`` → ``prompts/urdf/`` mapping was written out three times and missing in a
fourth, which is why the articulated agent received no cookbook at all for weeks.

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

#: ``Language.URDF_BLENDER`` is ``"urdf_blender"``; its prompts live in ``prompts/urdf/``.
#: THE mapping — importing this is the only supported way to ask.
PROMPT_DIRS: dict[Language, str] = {Language.URDF_BLENDER: "urdf"}


def prompt_dir_for(language: Language) -> str:
    return PROMPT_DIRS.get(language, language.value)

