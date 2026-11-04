"""Prompt loader: markdown + jinja2 templates, content-hashed for provenance.

Names resolve through the active variant first (``prompts/variants.py``): an overlay file shadows the
base file, a missing overlay file falls back to the base.
"""

from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined

from codeverse3d.prompts.variants import active_variant, variant_root

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent

_FLAGS: dict[str, Any] = {"undefined": StrictUndefined, "trim_blocks": True, "lstrip_blocks": True,
                          "keep_trailing_newline": True, "autoescape": False}

_env = Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), **_FLAGS)


@lru_cache(maxsize=16)
def _env_for(variant: str) -> Environment:
    """The base environment for ``""``, else overlay-first then base."""
    if not variant:
        return _env
    return Environment(loader=ChoiceLoader([FileSystemLoader(str(variant_root(variant))),
                                            FileSystemLoader(str(PROMPTS_DIR))]), **_FLAGS)


def _resolve(variant: str, rel_path: str) -> Path:
    """The file ``rel_path`` reads from under ``variant``: its overlay copy if any, else the base path."""
    if variant:
        overlay = variant_root(variant) / rel_path
        if overlay.is_file():
            return overlay
    return PROMPTS_DIR / rel_path


@lru_cache(maxsize=512)
def _load(variant: str, rel_path: str) -> str:
    p = _resolve(variant, rel_path)
    if not p.is_file():
        raise FileNotFoundError(f"prompt not found: {rel_path}")
    return p.read_text()


def load_text(rel_path: str) -> str:
    return _load(active_variant(), rel_path)


def render(rel_path: str, **ctx: Any) -> str:
    """Render a jinja template under prompts/ with ``ctx``; a missing name raises (StrictUndefined)."""
    return _env_for(active_variant()).get_template(rel_path).render(**ctx)


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]
