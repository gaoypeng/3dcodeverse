"""Content-addressed cache for synthesized reference sets.

``~/.cache/codeverse/references/<key>/`` holds ``set.json`` plus the PNGs it
names.  The key covers the brief, the models and the hash of every prompt
constant in :mod:`codeverse.reference.prompts`, so a wording change or a model
change misses the cache instead of silently reusing an old picture.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from pydantic import ValidationError

from codeverse.config import get_settings
from codeverse.contracts.common import Usage
from codeverse.contracts.spec import Spec
from codeverse.prompts import prompt_hash
from codeverse.reference import prompts as P
from codeverse.reference.spec_text import brief_text
from codeverse.reference.types import ReferenceSet

log = logging.getLogger(__name__)

CACHE_SUBDIR = "references"


def template_hash() -> str:
    """One hash over every prompt constant that shapes a reference image."""
    blob = "\x00".join([
        P.IMAGE_PROMPT_SYSTEM, P.IMAGE_PROMPT_USER, P.STUDIO_SUFFIX, P.GATE_SYSTEM, P.GATE_USER,
        *(f"{k}={v}" for k, v in sorted(P.VIEW_CLAUSE.items())),
    ])
    return prompt_hash(blob)


def cache_key(spec: Spec, *, n_views: int, text_model: str, image_model: str) -> str:
    h = hashlib.sha256()
    for part in (template_hash(), brief_text(spec), str(n_views), text_model, image_model):
        h.update(part.encode())
        h.update(b"\x00")
    return h.hexdigest()[:20]


def cache_root(cache_dir: Path | None = None) -> Path:
    return (cache_dir or get_settings().cache_dir) / CACHE_SUBDIR


def load(key: str, *, cache_dir: Path | None = None) -> ReferenceSet | None:
    """Cached set whose image files all still exist, else ``None`` (self-healing)."""
    path = cache_root(cache_dir) / key / "set.json"
    if not path.is_file():
        return None
    try:
        rs = ReferenceSet.model_validate_json(path.read_text())
    except (ValidationError, ValueError) as e:
        log.warning("reference cache %s unreadable (%s); ignoring", key, e)
        return None
    if any(not Path(v.path).is_file() for v in rs.views):
        log.info("reference cache %s lost its images; regenerating", key)
        return None
    rs.source, rs.usage = "cache", Usage()
    return rs


def store(rs: ReferenceSet, *, cache_dir: Path | None = None) -> Path:
    d = cache_root(cache_dir) / rs.key
    d.mkdir(parents=True, exist_ok=True)
    out = d / "set.json"
    out.write_text(rs.model_dump_json(indent=2))
    return out


def image_dir(key: str, *, cache_dir: Path | None = None) -> Path:
    d = cache_root(cache_dir) / key
    d.mkdir(parents=True, exist_ok=True)
    return d


__all__ = ["cache_key", "cache_root", "image_dir", "load", "store", "template_hash"]
