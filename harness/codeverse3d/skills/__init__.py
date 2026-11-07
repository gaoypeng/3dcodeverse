"""The 3dcode skill library: task-scoped rule sheets, routed automatically, read-measured.

A skill is AGENT-READ material (<=350 lines of requirements and short optional practices for one
failure class) that the harness only materialises, when track / language / kind / plan / the
previous round's gate findings say it applies.  The library ships as package data.

Public surface: ``select`` (pure routing), ``attach_skills`` (write the routed bundles),
``probe_reads`` (which were opened).  Controlled by ``C3D_SKILLS`` (default on).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from codeverse3d.config import get_settings
from codeverse3d.skills.model import (
    SKILL_FILE,
    Skill,
    SkillError,
    parse_skill,
    validate_bundle,
)

log = logging.getLogger(__name__)


def skills_dir() -> Path:
    override = get_settings().skills_dir
    if override:
        return override.expanduser()
    return Path(__file__).resolve().parent


def bundle_dirs(root: Path | None = None) -> list[Path]:
    """Every directory under the library that looks like a bundle, sorted by name.

    ``_claims/`` and ``__pycache__/`` are invisible.
    """
    base = Path(root) if root is not None else skills_dir()
    if not base.is_dir():
        return []
    return sorted((p for p in base.iterdir()
                   if p.is_dir() and not p.name.startswith(("_", ".")) and (p / SKILL_FILE).is_file()),
                  key=lambda p: p.name)


def iter_skills(root: Path | None = None, *, strict: bool = False) -> Iterator[Skill]:
    for d in bundle_dirs(root):
        try:
            yield parse_skill(d)
        except SkillError as e:
            if strict:
                raise
            log.warning("skill %s is invalid and will not be routed: %s", d.name, e)


def all_skills(root: Path | None = None, *, strict: bool = False) -> dict[str, Skill]:
    return {s.name: s for s in iter_skills(root, strict=strict)}


def load_skill(name: str, root: Path | None = None) -> Skill:
    base = Path(root) if root is not None else skills_dir()
    if not (base / name / SKILL_FILE).is_file():
        raise SkillError(f"no skill {name!r} in {base}")
    return parse_skill(base / name)


from codeverse3d.skills.materialize import attach_skills, materialize_skills  # noqa: E402
from codeverse3d.skills.registry import plan_signals, select  # noqa: E402
from codeverse3d.skills.telemetry import probe_reads  # noqa: E402

__all__ = ["Skill", "SkillError", "all_skills", "attach_skills", "bundle_dirs", "iter_skills", "load_skill",
           "materialize_skills", "plan_signals", "probe_reads", "select", "skills_dir", "validate_bundle"]
