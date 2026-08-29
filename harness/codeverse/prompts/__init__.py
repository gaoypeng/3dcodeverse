"""Prompt loader: markdown + jinja2 templates, content-hashed for provenance."""

from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, StrictUndefined
from jinja2.exceptions import UndefinedError

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent

_env = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    autoescape=False,
)

#: same environment, but an unknown name renders empty instead of raising — used only
#: as the fallback above, never as the default.
_lenient_env = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    undefined=ChainableUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    autoescape=False,
)


@lru_cache(maxsize=256)
def load_text(rel_path: str) -> str:
    """Raw prompt text, e.g. ``load_text("blender/cookbook.md")``."""
    p = PROMPTS_DIR / rel_path
    if not p.is_file():
        raise FileNotFoundError(f"prompt not found: {rel_path}")
    return p.read_text()


def render(rel_path: str, **ctx: Any) -> str:
    """Render a jinja template under prompts/ with ``ctx``.

    StrictUndefined is deliberate: a template that silently drops a section because a
    caller renamed a variable is worse than a loud failure at development time.  But a
    template edited while a run is IN FLIGHT is a different matter — the run reloads the
    file at its next round boundary and dies on a variable its caller has never heard of.
    So a missing name degrades the prompt (that one section renders empty) instead of
    killing the task, and says so in the log.  Everything else still raises.
    """
    template = _env.get_template(rel_path)
    try:
        return template.render(**ctx)
    except UndefinedError as e:
        log.error("prompt %s wants a variable this caller does not pass (%s); rendering it "
                  "leniently — the prompt loses that section, the run continues", rel_path, e)
        return _lenient_env.get_template(rel_path).render(**ctx)


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def list_prompts() -> list[str]:
    return sorted(str(p.relative_to(PROMPTS_DIR)) for p in PROMPTS_DIR.rglob("*") if p.suffix in (".md", ".j2", ".txt", ".py", ".js"))
