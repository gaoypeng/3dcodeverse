"""Prompt loader: markdown + jinja2 templates, content-hashed for provenance.

Plain files under ``prompts/``; ``render`` runs a jinja template with strict undefined variables.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPTS_DIR = Path(__file__).resolve().parent

_FLAGS: dict[str, Any] = {"undefined": StrictUndefined, "trim_blocks": True, "lstrip_blocks": True,
                          "keep_trailing_newline": True, "autoescape": False}

_env = Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), **_FLAGS)


@lru_cache(maxsize=512)
def load_text(rel_path: str) -> str:
    p = PROMPTS_DIR / rel_path
    if not p.is_file():
        raise FileNotFoundError(f"prompt not found: {rel_path}")
    return p.read_text()


def render(rel_path: str, **ctx: Any) -> str:
    """Render a jinja template under prompts/ with ``ctx``; a missing name raises (StrictUndefined)."""
    return _env.get_template(rel_path).render(**ctx)


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]
