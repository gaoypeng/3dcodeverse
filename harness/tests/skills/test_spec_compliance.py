"""Validate raw bundles independently of our loader and, when installed, with skills-ref."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from codeverse.skills import bundle_dirs, skills_dir

BUNDLES = bundle_dirs()
pytestmark = pytest.mark.skipif(not BUNDLES, reason=f"no bundles in {skills_dir()} yet")

DESCRIPTION_MAX = 1024
OPTIONAL_DIRS = {"scripts", "references", "assets"}

#: `skills-ref` on PyPI installs its CLI as `agentskills`; accept either spelling
REF_VALIDATOR = shutil.which("agentskills") or shutil.which("skills-ref")


def _raw(d: Path) -> tuple[dict, str]:
    text = (d / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", text, re.S)
    assert m, f"{d.name}: SKILL.md must open with a '---' frontmatter block"
    return yaml.safe_load(m.group(1)) or {}, text[m.end():]


def test_every_bundle_conforms_to_the_raw_spec():
    for bundle in BUNDLES:
        frontmatter, body = _raw(bundle)
        description = frontmatter.get("description")
        assert isinstance(description, str) and 1 <= len(description) <= DESCRIPTION_MAX, (
            f"{bundle.name}: description must contain 1..{DESCRIPTION_MAX} characters"
        )
        assert re.search(r"\b(use|read|reach for)\b", description, re.I), (
            f"{bundle.name}: description must say when the CLI should match it"
        )

        for path in bundle.iterdir():
            if path.is_file():
                assert path.name == "SKILL.md", f"{bundle.name}: stray file {path.name}"
                continue
            assert path.name in OPTIONAL_DIRS, (
                f"{bundle.name}: {path.name}/ is not one of {sorted(OPTIONAL_DIRS)}"
            )
            for child in path.rglob("*"):
                assert child.is_file(), (
                    f"{bundle.name}: {child.relative_to(bundle)} is nested too deeply"
                )

        for target in re.findall(r"\]\((?!https?:|#)([^)]+)\)", body):
            relative = target.split("#", 1)[0].strip()
            if not relative:
                continue
            assert not relative.startswith("/") and ".." not in Path(relative).parts, (
                f"{bundle.name}: link {relative} escapes the bundle"
            )
            assert (bundle / relative).exists(), (
                f"{bundle.name}: link {relative} does not exist"
            )


@pytest.mark.skipif(REF_VALIDATOR is None,
                    reason="the reference validator is not installed (pip install skills-ref)")
def test_reference_implementation_validates_and_agrees_with_our_loader():
    from codeverse.skills.model import parse_skill

    for bundle in BUNDLES:
        result = subprocess.run(
            [REF_VALIDATOR, "validate", str(bundle)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{bundle.name}: {result.stdout}{result.stderr}"

        result = subprocess.run(
            [REF_VALIDATOR, "read-properties", str(bundle)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{bundle.name}: {result.stdout}{result.stderr}"
        theirs = json.loads(result.stdout)
        ours = parse_skill(bundle)
        assert theirs["name"] == ours.name, bundle.name
        assert theirs["description"] == ours.description, bundle.name
        assert dict(theirs.get("metadata") or {}) == dict(ours.metadata), bundle.name
