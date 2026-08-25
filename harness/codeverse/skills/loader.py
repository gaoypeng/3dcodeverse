"""Parse and validate one SKILL.md bundle against the open Agent Skills spec.

WHY this is strict: four of the five agent kinds this harness drives (claude-code,
codex, gemini-cli, agy) discover ``SKILL.md`` **themselves**.  A bundle we accept but
they reject is invisible — the run still passes, the skill simply never appears, and
the read-rate metric reports a zero we would misread as "the model ignored it".  So the
loader enforces the spec's own rules here, where a test can see them, rather than
finding out from a CLI upgrade six batteries later.

Enforced (agentskills.io, Dec 2025):
  * ``name``: 1-64 chars, ``^[a-z0-9]+(-[a-z0-9]+)*$``, and EQUAL to the parent
    directory name — every discovery implementation keys off the directory.
  * ``description``: 1-1024 chars, and it is the only text a CLI indexes, so it must
    carry both halves (what / when).
  * no ``<`` or ``>`` anywhere in the frontmatter — the spec's prompt-injection rule.
  * only spec keys at the top level; our own fields live under ``metadata`` (a
    string→string map), which is what keeps ``skills-ref validate`` happy.
Budget and evidence rules (ours, design §3/§5.2) are reported by :func:`validate_bundle`
rather than raised, so `3dcv skills validate` can list every problem in one pass.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from codeverse.skills.model import EVIDENCE_LEVELS, Skill

log = logging.getLogger(__name__)

SKILL_FILE = "SKILL.md"
REFERENCES_DIR = "references"

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX = 64
DESCRIPTION_MAX = 1024
COMPATIBILITY_MAX = 500
#: our body cap: half the spec's recommended 500 lines / 5,000 tokens, because
#: docs/COST.md §4 shows we pay for every prompt byte on every turn, not once.
BODY_MAX_LINES = 350
BODY_MAX_TOKENS = 2500
#: the spec's top-level keys.  Anything else belongs in `metadata`.
SPEC_KEYS = frozenset({"name", "description", "license", "compatibility", "metadata", "allowed-tools"})

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.S)


class SkillError(ValueError):
    """A bundle that the spec — or a CLI implementing it — would reject."""


def split_frontmatter(text: str) -> tuple[str, str]:
    """``(frontmatter_yaml, body)``; raises when the leading ``---`` block is missing."""
    m = _FRONTMATTER.match(text.lstrip("﻿"))
    if not m:
        raise SkillError("missing YAML frontmatter: the file must start with a '---' line")
    return m.group(1), text[m.end():]


def _as_str_map(value: object, where: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SkillError(f"{where} must be a mapping of strings, got {type(value).__name__}")
    out: dict[str, str] = {}
    for k, v in value.items():
        if isinstance(v, (dict, list)):
            raise SkillError(f"{where}.{k} must be a scalar (the spec allows string→string only)")
        out[str(k)] = "" if v is None else str(v)
    return out


def parse_skill(path: Path) -> Skill:
    """Read ``<dir>/SKILL.md`` into a :class:`Skill`, or raise :class:`SkillError`."""
    path = Path(path)
    if path.is_dir():
        path = path / SKILL_FILE
    if not path.is_file():
        raise SkillError(f"no {SKILL_FILE} at {path}")
    raw = path.read_text(encoding="utf-8")
    fm_text, body = split_frontmatter(raw)
    if "<" in fm_text or ">" in fm_text:
        raise SkillError("frontmatter contains '<' or '>': the spec forbids them (prompt-injection rule)")
    try:
        data = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as e:
        raise SkillError(f"frontmatter is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise SkillError("frontmatter must be a YAML mapping")

    unknown = sorted(set(data) - SPEC_KEYS)
    if unknown:
        raise SkillError(f"unknown frontmatter key(s) {unknown}; put your own fields under `metadata`")

    name = str(data.get("name") or "").strip()
    dir_name = path.parent.name
    if not name:
        raise SkillError("frontmatter has no `name`")
    if len(name) > NAME_MAX or not NAME_RE.match(name):
        raise SkillError(f"name {name!r} must match {NAME_RE.pattern} and be <= {NAME_MAX} chars")
    if name != dir_name:
        raise SkillError(f"name {name!r} != directory name {dir_name!r}; every CLI keys discovery off the directory")

    description = str(data.get("description") or "").strip()
    if not description:
        raise SkillError(f"{name}: frontmatter has no `description` (a CLI indexes nothing else)")
    if len(description) > DESCRIPTION_MAX:
        raise SkillError(f"{name}: description is {len(description)} chars, the spec allows {DESCRIPTION_MAX}")

    compatibility = str(data.get("compatibility") or "").strip()
    if len(compatibility) > COMPATIBILITY_MAX:
        raise SkillError(f"{name}: compatibility is {len(compatibility)} chars, the spec allows {COMPATIBILITY_MAX}")

    return Skill(
        name=name,
        description=description,
        body=body,
        license=str(data.get("license") or "").strip(),
        compatibility=compatibility,
        metadata=_as_str_map(data.get("metadata"), "metadata"),
        dir=path.parent,
        references=_references(path.parent),
    )


def _references(bundle: Path) -> tuple[str, ...]:
    d = bundle / REFERENCES_DIR
    if not d.is_dir():
        return ()
    return tuple(sorted(f"{REFERENCES_DIR}/{p.name}" for p in d.iterdir() if p.is_file()))


def validate_bundle(bundle: Path) -> list[str]:
    """Every problem with one bundle directory, as human lines (empty == clean).

    Structural problems come back as one line; budget/evidence/layout problems are
    listed together so `3dcv skills validate` fixes a bundle in one round trip.
    """
    bundle = Path(bundle)
    try:
        skill = parse_skill(bundle)
    except SkillError as e:
        return [str(e)]
    issues: list[str] = []
    if skill.body_lines > BODY_MAX_LINES:
        issues.append(f"body is {skill.body_lines} lines, cap is {BODY_MAX_LINES} — move depth into {REFERENCES_DIR}/")
    if skill.body_tokens > BODY_MAX_TOKENS:
        issues.append(f"body is ~{skill.body_tokens} tokens, cap is {BODY_MAX_TOKENS}")
    if not skill.body.strip():
        issues.append("body is empty")
    if skill.evidence not in EVIDENCE_LEVELS:
        issues.append(f"metadata.evidence must be one of {list(EVIDENCE_LEVELS)}, got {skill.evidence!r}")
    if not skill.metadata.get("verified"):
        issues.append("metadata.verified (ISO date the claims were last checked) is missing")
    refs = bundle / REFERENCES_DIR
    if refs.is_dir():
        for p in refs.iterdir():
            if p.is_dir():
                issues.append(f"{REFERENCES_DIR}/{p.name}/ is a second level; the spec allows one level of references")
    for p in bundle.rglob("*"):
        if p.is_symlink():
            issues.append(f"{p.relative_to(bundle)} is a symlink; codex refuses symlinks inside a skill tree")
    return issues


__all__ = ["BODY_MAX_LINES", "BODY_MAX_TOKENS", "DESCRIPTION_MAX", "NAME_MAX", "NAME_RE", "REFERENCES_DIR",
           "SKILL_FILE", "SPEC_KEYS", "SkillError", "parse_skill", "split_frontmatter", "validate_bundle"]
