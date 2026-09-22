"""A synthetic skill library on disk — the machinery is tested without the real bodies.

The 14 real bundles are written by a different phase; the plumbing must be green before
they land, and must keep working when they change.  So every unit test here builds the
library it needs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.skills.registry import ROUTED_SKILLS

FRONTMATTER = """---
name: {name}
description: {description}
license: Apache-2.0
metadata:
  evidence: {evidence}
  verified: "2026-08-25"
---

# {name}

{body}
"""


def write_bundle(root: Path, name: str, *, description: str = "", evidence: str = "measured",
                 body: str = "Rule one: prove it with `measure`.", references: dict[str, str] | None = None) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(FRONTMATTER.format(
        name=name, description=description or f"Rules for {name}. Use when the harness routes it.",
        evidence=evidence, body=body))
    refs = {"worked_example.md": "A worked example.\n"} if references is None else references
    for rel, text in refs.items():
        (d / "references").mkdir(exist_ok=True)
        (d / "references" / rel).write_text(text)
    return d


@pytest.fixture
def library_dir(tmp_path: Path) -> Path:
    """A library holding every skill the route table can name, all `measured`."""
    root = tmp_path / "library"
    root.mkdir()
    for name in ROUTED_SKILLS:
        write_bundle(root, name)
    return root


@pytest.fixture
def library(library_dir: Path) -> dict:
    from codeverse3d.skills import all_skills

    return all_skills(library_dir, strict=True)
