"""Typed shapes for the skill library (frozen): one bundle, one selection, one session's usage."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from codeverse3d.cost.guard import text_tokens

#: ``metadata.evidence`` values; the router refuses ``inherited-unverified`` when C3D_SKILLS_UNVERIFIED is 0.
EVIDENCE_MEASURED = "measured"
EVIDENCE_INHERITED = "inherited-unverified"
EVIDENCE_MIXED = "mixed"
EVIDENCE_LEVELS = (EVIDENCE_MEASURED, EVIDENCE_MIXED, EVIDENCE_INHERITED)


class Skill(BaseModel):
    """One SKILL.md bundle, parsed and validated against the open spec (agentskills.io)."""

    model_config = {"frozen": True}

    name: str = Field(description="lowercase-hyphen id; equals the parent directory name")
    description: str = Field(description="what it does AND when to use it; the only text a CLI indexes")
    body: str = Field(default="", description="everything after the frontmatter")
    license: str = ""
    compatibility: str = ""
    metadata: dict[str, str] = Field(default_factory=dict)
    dir: Path = Field(description="the bundle directory (contains SKILL.md)")
    references: tuple[str, ...] = Field(default_factory=tuple, description="references/*.md, bundle-relative")

    @property
    def path(self) -> Path:
        return self.dir / "SKILL.md"

    @property
    def body_tokens(self) -> int:
        return text_tokens(self.body)

    @property
    def body_lines(self) -> int:
        return len(self.body.splitlines())

    @property
    def evidence(self) -> str:
        return self.metadata.get("evidence", EVIDENCE_INHERITED)

    @property
    def verified(self) -> str:
        return self.metadata.get("verified", "")


class Selection(BaseModel):
    """One routed skill with the reason it was routed."""

    model_config = {"frozen": True}

    skill: Skill
    priority: int
    rules: tuple[str, ...] = Field(description="rule ids that fired, e.g. ('R1', 'R2')")
    reason: str = Field(description="one human line: which rule, and which finding if any")

    @property
    def name(self) -> str:
        return self.skill.name

    @property
    def gate_fired(self) -> bool:
        """True when a previous-round gate finding routed this."""
        return self.priority >= 90


class SkillsMaterialized(BaseModel):

    listed: list[str] = Field(default_factory=list, description="skill names, in attach order")
    selections: list[Selection] = Field(default_factory=list, description="the routed rows, with the reason each fired")
    reasons: dict[str, str] = Field(default_factory=dict, description="name → why it was attached")
    index_tokens: int = Field(default=0, description="estimated tokens the index/mandate adds to message 0")
    inlined: str = Field(default="", description="single-shot: the one body inlined, '' otherwise")
    attached_at: float = Field(default=0.0, description="epoch seconds of the attach: the read probe credits "
                               "only tool calls a CLI session logged after it (telemetry.probe_reads)")
    variant: str = Field(default="", description="the prompt/skill variant active at the attach ('' = base; "
                         "D105) — its overlay bundles and routes.toml shaped `selections`")


# ===================================================================== loader
log = logging.getLogger(__name__)

SKILL_FILE = "SKILL.md"
REFERENCES_DIR = "references"

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX = 64
DESCRIPTION_MAX = 1024
COMPATIBILITY_MAX = 500
#: body cap, kept small because every prompt byte is paid on every turn
BODY_MAX_LINES = 350
BODY_MAX_TOKENS = 2500
#: the spec's top-level keys; anything else belongs in `metadata`
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


def nonstring_metadata(fm: dict) -> list[str]:
    """metadata values YAML did not hand back as strings (an unquoted ``verified: 2020-01-01`` is a date)."""
    meta = fm.get("metadata")
    if not isinstance(meta, dict):
        return []
    return [f"metadata.{k} is a {type(v).__name__}, not a string — quote it (the spec allows string→string)"
            for k, v in meta.items() if not isinstance(v, str) and v is not None]


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
    """Every problem with one bundle directory, as human lines (empty == clean)."""
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
    try:
        fm_text, _ = split_frontmatter((bundle / SKILL_FILE).read_text(encoding="utf-8"))
        issues += nonstring_metadata(yaml.safe_load(fm_text) or {})
    except (OSError, SkillError, yaml.YAMLError):  # already reported by parse_skill above
        pass
    refs = bundle / REFERENCES_DIR
    if refs.is_dir():
        for p in refs.iterdir():
            if p.is_dir():
                issues.append(f"{REFERENCES_DIR}/{p.name}/ is a second level; the spec allows one level of references")
    for p in bundle.rglob("*"):
        if p.is_symlink():
            issues.append(f"{p.relative_to(bundle)} is a symlink; codex refuses symlinks inside a skill tree")
    return issues
