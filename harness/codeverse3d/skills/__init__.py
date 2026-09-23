"""The 3dcode skill library: task-scoped rule sheets, routed automatically, read-measured.

A *skill* is not a renamed cookbook chapter — the two sit on opposite sides of the split
in ``prompts/catalog.py``.  The cookbook is PROMPT material: the reference manual of
copyable code, resolved through that catalog, inlined into the generate templates and
materialised at ``.3dcode/cookbook.md`` for the agent to re-read (the ``read_cookbook`` MCP
tool that once served it a chapter at a time was deleted — 0 calls in 16 zone sessions).
A skill is AGENT-READ material the harness only materialises: <=350 lines of rules and
numbers for one recurring failure class, attached to a session only when the track /
language / kind / plan / **previous round's gate findings** say it applies.

Why the library lives inside the package: every backend reads it from a materialised
workspace copy, but the source of truth ships in the wheel, so it must be package data
(``pyproject.toml`` ``[tool.setuptools.package-data]``) and be resolved through
``importlib.resources`` — a sibling wave already shipped a wheel whose starter tree was
silently empty for exactly this reason.

Public surface for the rest of the harness::

    from codeverse3d.skills import select, attach_skills, probe_reads

``select`` is pure and testable; ``attach_skills`` writes the routed bundles into a
workspace and returns what it wrote; ``probe_reads`` says afterwards which of them were
actually opened — from the CLI's own tool calls where its backend recorded them.  Everything
is behind ``C3D_SKILLS``: ON by default since 2026-09-22, ``C3D_SKILLS=0`` turns it off.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from codeverse3d.skills.model import (
    SKILL_FILE,
    Skill,
    SkillError,
    parse_skill,
    validate_bundle,
)

log = logging.getLogger(__name__)

def skills_dir() -> Path:
    """The bundle root (``C3D_SKILLS_DIR`` points it elsewhere), resolved at call time so an
    installed wheel works too."""
    from codeverse3d.config import get_settings

    override = get_settings().skills_dir
    if override:
        return override.expanduser()
    try:
        from importlib.resources import files

        return Path(str(files("codeverse3d.skills")))
    except Exception as e:  # noqa: BLE001 — a namespace/zip install must not break a run
        log.debug("importlib.resources could not resolve the skill library (%s); using __file__", e)
        return Path(__file__).resolve().parent


def bundle_dirs(root: Path | None = None) -> list[Path]:
    """Every directory under the library that looks like a bundle, sorted by name.

    ``_claims/`` and ``__pycache__/`` are deliberately invisible: the library shares a
    directory with this package's modules, and a pure tree is what ``skills-ref
    validate`` and the CLIs expect to walk.
    """
    base = Path(root) if root is not None else skills_dir()
    if not base.is_dir():
        return []
    return sorted((p for p in base.iterdir()
                   if p.is_dir() and not p.name.startswith(("_", ".")) and (p / SKILL_FILE).is_file()),
                  key=lambda p: p.name)


def iter_skills(root: Path | None = None, *, strict: bool = False) -> Iterator[Skill]:
    """Every valid bundle.  A broken one is logged and skipped unless ``strict``.

    Skipping is the right default in a run: one malformed bundle must cost that skill,
    not the battery.  ``3dcode skills validate`` and the test suite pass ``strict=True``.
    """
    for d in bundle_dirs(root):
        try:
            yield parse_skill(d)
        except SkillError as e:
            if strict:
                raise
            log.warning("skill %s is invalid and will not be routed: %s", d.name, e)


def all_skills(root: Path | None = None, *, strict: bool = False) -> dict[str, Skill]:
    """name → Skill for the whole library (empty dict when no bundles ship yet)."""
    return {s.name: s for s in iter_skills(root, strict=strict)}


def load_skill(name: str, root: Path | None = None) -> Skill:
    """One bundle by name; raises :class:`SkillError` when it is missing or invalid."""
    base = Path(root) if root is not None else skills_dir()
    d = base / name
    if not (d / SKILL_FILE).is_file():
        raise SkillError(f"no skill {name!r} in {base}")
    return parse_skill(d)


from codeverse3d.skills.materialize import attach_skills, materialize_skills  # noqa: E402
from codeverse3d.skills.registry import plan_signals, select  # noqa: E402
from codeverse3d.skills.telemetry import probe_reads  # noqa: E402

__all__ = ["Skill", "SkillError", "all_skills", "attach_skills", "bundle_dirs",
           "iter_skills", "load_skill", "materialize_skills", "plan_signals", "probe_reads", "select",
           "skills_dir", "validate_bundle"]
