"""Prompt loader: markdown + jinja2 templates, content-hashed for provenance."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPTS_DIR = Path(__file__).resolve().parent

_env = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    undefined=StrictUndefined,
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
    """Render a jinja template under prompts/ with ``ctx``."""
    return _env.get_template(rel_path).render(**ctx)


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def list_prompts() -> list[str]:
    return sorted(str(p.relative_to(PROMPTS_DIR)) for p in PROMPTS_DIR.rglob("*") if p.suffix in (".md", ".j2", ".txt", ".py", ".js"))
