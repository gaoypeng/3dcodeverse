"""The open Agent Skills spec (agentskills.io, Dec 2025), checked WITHOUT our loader.

WHY a second implementation instead of reusing ``parse_skill``: the loader is the thing
under test.  Four of the five agent kinds this harness drives read these directories with
their own parsers, and a bundle our loader accepts because of a bug in our loader is a
bundle those CLIs silently drop — invisible, because the run still passes and the read
rate simply reports a zero.  So this file re-reads the raw bytes and asserts the spec's
own field rules, and the reference validator (``pip install skills-ref`` → the
``agentskills`` binary) is run over every bundle when it is installed.
"""

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
IDS = [d.name for d in BUNDLES]

#: the spec's field table, transcribed from research/EXTERNAL.md §1
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NAME_MAX = 64
DESCRIPTION_MAX = 1024
COMPATIBILITY_MAX = 500
BODY_MAX_LINES = 500          # the spec's own recommendation; ours is stricter (350)
SPEC_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
OPTIONAL_DIRS = {"scripts", "references", "assets"}

#: `skills-ref` on PyPI installs its CLI as `agentskills`; accept either spelling
REF_VALIDATOR = shutil.which("agentskills") or shutil.which("skills-ref")


def _raw(d: Path) -> tuple[dict, str]:
    text = (d / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", text, re.S)
    assert m, f"{d.name}: SKILL.md must open with a '---' frontmatter block"
    return yaml.safe_load(m.group(1)) or {}, text[m.end():]


@pytest.mark.parametrize("d", BUNDLES, ids=IDS)
def test_the_bundle_is_a_directory_holding_skill_md(d: Path):
    assert (d / "SKILL.md").is_file(), f"{d.name}: the spec's entry point is SKILL.md"


@pytest.mark.parametrize("d", BUNDLES, ids=IDS)
def test_name_matches_the_character_rules_and_the_directory(d: Path):
    fm, _ = _raw(d)
    name = fm.get("name")
    assert isinstance(name, str) and name, f"{d.name}: `name` is required"
    assert 1 <= len(name) <= NAME_MAX
    assert NAME_RE.match(name), f"{name!r}: lowercase a-z0-9 and single hyphens only"
    assert "--" not in name, f"{name!r}: consecutive hyphens are forbidden"
    assert not name.startswith("-") and not name.endswith("-")
    assert name == d.name, f"{name!r} != directory {d.name!r}: every CLI keys discovery off the dir"


@pytest.mark.parametrize("d", BUNDLES, ids=IDS)
def test_description_is_present_bounded_and_says_what_and_when(d: Path):
    fm, _ = _raw(d)
    desc = fm.get("description")
    assert isinstance(desc, str) and 1 <= len(desc) <= DESCRIPTION_MAX
    assert re.search(r"\b(use|read|reach for)\b", desc, re.I), (
        f"{d.name}: the description is the ONLY text a CLI matches on — it must say WHEN")


@pytest.mark.parametrize("d", BUNDLES, ids=IDS)
def test_no_angle_bracket_anywhere_in_the_frontmatter(d: Path):
    """The spec's prompt-injection rule: frontmatter is pasted into a system prompt."""
    text = (d / "SKILL.md").read_text(encoding="utf-8")
    fm_text = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---", text, re.S).group(1)
    assert "<" not in fm_text and ">" not in fm_text


@pytest.mark.parametrize("d", BUNDLES, ids=IDS)
def test_only_spec_keys_at_the_top_level_and_metadata_is_a_string_map(d: Path):
    fm, _ = _raw(d)
    assert set(fm) <= SPEC_KEYS, f"{d.name}: {sorted(set(fm) - SPEC_KEYS)} belong under `metadata`"
    meta = fm.get("metadata") or {}
    assert isinstance(meta, dict)
    for k, v in meta.items():
        assert isinstance(k, str) and isinstance(v, (str, int, float, bool)), (
            f"{d.name}: metadata.{k} must be a scalar (the spec allows string→string)")
    if "compatibility" in fm:
        assert len(str(fm["compatibility"])) <= COMPATIBILITY_MAX


@pytest.mark.parametrize("d", BUNDLES, ids=IDS)
def test_the_body_stays_under_the_spec_line_budget(d: Path):
    _, body = _raw(d)
    assert len(body.splitlines()) <= BODY_MAX_LINES


@pytest.mark.parametrize("d", BUNDLES, ids=IDS)
def test_resources_are_one_level_deep_in_the_directories_the_spec_names(d: Path):
    for p in d.iterdir():
        if p.is_file():
            assert p.name == "SKILL.md", f"{d.name}: stray file {p.name}; depth belongs in references/"
            continue
        assert p.name in OPTIONAL_DIRS, f"{d.name}: {p.name}/ is not one of {sorted(OPTIONAL_DIRS)}"
        for child in p.rglob("*"):
            assert child.is_file(), f"{d.name}: {child.relative_to(d)} is a second level of nesting"


@pytest.mark.parametrize("d", BUNDLES, ids=IDS)
def test_every_relative_link_in_the_body_resolves_inside_the_bundle(d: Path):
    """Progressive disclosure only works if the pointer points at something."""
    _, body = _raw(d)
    for target in re.findall(r"\]\((?!https?:|#)([^)]+)\)", body):
        rel = target.split("#", 1)[0].strip()
        if not rel:
            continue
        assert not rel.startswith("/") and ".." not in Path(rel).parts, f"{d.name}: {rel} escapes the bundle"
        assert (d / rel).exists(), f"{d.name}: links to {rel}, which does not exist"


@pytest.mark.skipif(REF_VALIDATOR is None,
                    reason="the reference validator is not installed (pip install skills-ref)")
@pytest.mark.parametrize("d", BUNDLES, ids=IDS)
def test_the_reference_implementation_validates_the_bundle(d: Path):
    p = subprocess.run([REF_VALIDATOR, "validate", str(d)], capture_output=True, text=True, check=False)
    assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.skipif(REF_VALIDATOR is None, reason="the reference validator is not installed")
@pytest.mark.parametrize("d", BUNDLES, ids=IDS)
def test_our_loader_and_the_reference_implementation_read_the_same_fields(d: Path):
    """A disagreement here is the failure mode that matters: we index one description,
    the CLI indexes another, and the read rate measures a skill nobody was offered."""
    from codeverse.skills.loader import parse_skill

    p = subprocess.run([REF_VALIDATOR, "read-properties", str(d)],
                       capture_output=True, text=True, check=False)
    assert p.returncode == 0, p.stdout + p.stderr
    theirs = json.loads(p.stdout)
    ours = parse_skill(d)
    assert theirs["name"] == ours.name
    assert theirs["description"] == ours.description
    assert dict(theirs.get("metadata") or {}) == dict(ours.metadata)
