"""What prompt material exists, and how each piece reaches the model.

ONE answer to a question that had six.  Before this, `<lang>/cookbook.md` was resolved in
`languages/_docs.py`, `tracks/common.py`, `spatial/cookbook_tool.py`, `agents/materialize.py`
and `tracks/scene_assets.py`, each with its own path arithmetic — and the
``urdf_blender`` → ``prompts/urdf/`` mapping was written out three times and missing in a
fourth, which is why the articulated agent received no cookbook at all for weeks.

The split this file encodes (owner, 2026-08-28):

* **PROMPT material** — everything the harness writes and the harness puts in a message.
  It lives here, under ``codeverse/prompts/``, and modules load it on demand through this
  catalog rather than building paths.
* **AGENT-read bundles** — ``codeverse/skills/cv3d-*/`` (SKILL.md + references/ + a
  ``_claims`` file pinning its numbers to live constants).  The agent decides to read
  those; the harness only materialises them.

A file that tries to be both is the thing this split exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codeverse.contracts.common import Language
from codeverse.prompts import PROMPTS_DIR, load_text

#: ``Language.URDF_BLENDER`` is ``"urdf_blender"``; its prompts live in ``prompts/urdf/``.
#: THE mapping — importing this is the only supported way to ask.
PROMPT_DIRS: dict[Language, str] = {Language.URDF_BLENDER: "urdf"}


def prompt_dir_for(language: Language) -> str:
    return PROMPT_DIRS.get(language, language.value)


@dataclass(frozen=True)
class LanguagePrompts:
    """The four per-language pieces, and how each is delivered.

    ``system`` and ``contract`` are sent WHOLE in every generating message; ``cookbook``
    is sent whole in the round prompt and also copied to ``<ws>/.3dcv/cookbook.md`` so an
    agent session can re-read it without spending a turn.  None of them is truncated —
    delivering a byte prefix of material we wrote for the model was removed 2026-08-28.
    """

    language: Language
    dir: str

    @property
    def root(self) -> Path:
        return PROMPTS_DIR / self.dir

    def system(self, *, variant: str = "") -> str:
        return load_text(f"{self.dir}/system{variant}.md").strip()

    def contract(self) -> str:
        return load_text(f"{self.dir}/contract.md")

    def cookbook(self) -> str:
        return load_text(f"{self.dir}/cookbook.md")

    def cookbook_rel(self) -> str:
        return f"{self.dir}/cookbook.md"


def prompts_for(language: Language) -> LanguagePrompts:
    return LanguagePrompts(language=language, dir=prompt_dir_for(language))
