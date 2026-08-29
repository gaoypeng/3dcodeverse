"""Where a language's prompt text lives — one resolver for all seven runtimes.

Each runtime used to spell this out itself, and in three different shapes: a
``Path.is_file()`` check falling back to a packaged ``CONTRACT.md``; a
``try: load_text(...) except FileNotFoundError`` falling back to an inline string;
and urdf's variant of the second.  The only thing that actually differed was the
directory name, so it is a declaration now (2026-08-28).

The packaged ``languages/<lang>/CONTRACT.md`` fallbacks went with the merge: they
were unreachable in any installed state (``pyproject`` ships ``prompts/**/*``, and
every ``contract_doc`` read ``prompts/`` first), and two of their numbers had drifted
out of agreement with ``conventions.py`` — 500 k triangles against
``MAX_TRIS_OBJECT = 600_000``, and a 120 s build budget against 300 s.  A second home
for a fact is a second answer, which is what law 2 exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

from codeverse.contracts.common import Language
from codeverse.prompts import PROMPTS_DIR, load_text
from codeverse.prompts.catalog import PROMPT_DIRS as _PROMPT_DIRS
from codeverse.prompts.catalog import prompt_dir_for as _prompt_dir_for

#: re-exported for the callers that already import it from here; the mapping itself
#: now lives with the rest of the prompt-resolution policy in prompts/catalog.py.
PROMPT_DIRS = _PROMPT_DIRS

#: only reachable if the wheel shipped without its prompt data
MINIMAL_CONTRACT = "Write raw code in the language's native frame; meters; named parts."


prompt_dir_for = _prompt_dir_for


class RuntimeDocs:
    """``contract_doc`` / ``cookbook_path`` for every ``LanguageRuntime``."""

    language: Language

    @property
    def prompt_dir(self) -> str:
        return prompt_dir_for(self.language)

    def contract_doc(self) -> str:
        try:
            return load_text(f"{self.prompt_dir}/contract.md")
        except FileNotFoundError:
            return MINIMAL_CONTRACT

    def cookbook_path(self) -> Path:
        return PROMPTS_DIR / self.prompt_dir / "cookbook.md"
