"""Prompt loader: markdown + jinja2 templates, content-hashed for provenance.

Every name resolves through the ACTIVE VARIANT first (``prompts/variants.py``, D105): a file
``variants/<name>/<rel>`` shadows ``<rel>``, a missing overlay file falls back to the base, and
with no variant active nothing here changes — the base environment is the single
``FileSystemLoader`` it always was, so base rendering is byte-identical (the manifest pins it).
Callers do not change: ``load_text`` / ``render`` / ``catalog.language_text`` take the same names.
"""

from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import ChainableUndefined, ChoiceLoader, Environment, FileSystemLoader, StrictUndefined
from jinja2.exceptions import UndefinedError

from codeverse3d.prompts.variants import active_variant, variant_root

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent

#: the flags every prompt environment renders with — the base one and each variant's
_FLAGS: dict[str, Any] = {"undefined": StrictUndefined, "trim_blocks": True, "lstrip_blocks": True,
                          "keep_trailing_newline": True, "autoescape": False}

_env = Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), **_FLAGS)

#: same environment (loader + flags; its own template cache), but an unknown name renders
#: empty instead of raising — used only as the fallback in :func:`render`, never as the default.
_lenient_env = _env.overlay(undefined=ChainableUndefined)


@lru_cache(maxsize=16)
def _env_for(variant: str) -> tuple[Environment, Environment]:
    """``(strict, lenient)`` environments: the base pair for ``""``; for a variant a ``ChoiceLoader``
    pair — the overlay directory first, the base second, so an overlay template may ``{% include %}``
    or ``{% extends %}`` a base file, and a name the overlay does not ship renders from the base."""
    if not variant:
        return _env, _lenient_env
    env = Environment(loader=ChoiceLoader([FileSystemLoader(str(variant_root(variant))),
                                           FileSystemLoader(str(PROMPTS_DIR))]), **_FLAGS)
    return env, env.overlay(undefined=ChainableUndefined)


def _resolve(variant: str, rel_path: str) -> Path:
    """The file ``rel_path`` reads from under ``variant``: its overlay copy when one exists, else the
    base path (which may not exist either — the caller decides what a missing file means)."""
    if variant:
        overlay = variant_root(variant) / rel_path
        if overlay.is_file():
            return overlay
    return PROMPTS_DIR / rel_path


def prompt_root(rel_path: str) -> Path:
    """The file a prompt name resolves to right now (the active variant's overlay, else the base)."""
    return _resolve(active_variant(), rel_path)


@lru_cache(maxsize=512)
def _load(variant: str, rel_path: str) -> str:
    p = _resolve(variant, rel_path)
    if not p.is_file():
        raise FileNotFoundError(f"prompt not found: {rel_path}")
    return p.read_text()


def load_text(rel_path: str) -> str:
    """Raw prompt text, e.g. ``load_text("blender/cookbook.md")`` — the active variant's copy first."""
    return _load(active_variant(), rel_path)


def render(rel_path: str, **ctx: Any) -> str:
    """Render a jinja template under prompts/ with ``ctx``.

    StrictUndefined is deliberate: a template that silently drops a section because a
    caller renamed a variable is worse than a loud failure at development time.  But a
    template edited while a run is IN FLIGHT is a different matter — the run reloads the
    file at its next round boundary and dies on a variable its caller has never heard of.
    So a missing name degrades the prompt (that one section renders empty) instead of
    killing the task, and says so in the log.  Everything else still raises.
    """
    env, lenient = _env_for(active_variant())
    template = env.get_template(rel_path)
    try:
        return template.render(**ctx)
    except UndefinedError as e:
        log.error("prompt %s wants a variable this caller does not pass (%s); rendering it "
                  "leniently — the prompt loses that section, the run continues", rel_path, e)
        return lenient.get_template(rel_path).render(**ctx)


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]
