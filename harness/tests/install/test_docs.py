"""Offline guards for the install documentation.

Covers only the files the install guide owns (``docs/INSTALL.md``, the three
READMEs, ``scripts/setup.sh``) so a doc change elsewhere cannot fail them:

* ``docs/INSTALL.md`` exists and is non-empty;
* every command in a fenced *shell* block starts with a binary we expect a
  reader to have (catches copy-paste typos and invented tools);
* every internal markdown link, backticked ``*.md`` path and same-page anchor
  resolves;
* ``scripts/setup.sh`` is executable, parses (``bash -n``) and answers ``--help``;
* every extra declared in ``pyproject.toml`` is documented in INSTALL.md.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from codeverse._compat import tomllib

HARNESS = Path(__file__).resolve().parents[2]
REPO = HARNESS.parent
INSTALL = HARNESS / "docs" / "INSTALL.md"
SETUP = HARNESS / "scripts" / "setup.sh"

#: docs owned by the install guide (repo README is optional: the harness can be checked out alone)
OWNED_DOCS = [INSTALL, HARNESS / "README.md", HARNESS / "runtime_js" / "README.md", REPO / "README.md"]

SHELL_LANGS = {"bash", "sh", "shell", "console", "zsh"}

#: first token of a documented command must be one of these (or have one as its basename)
ALLOWED_BINARIES = {
    # shell builtins / coreutils
    "bash", "sh", "cd", "ls", "cat", "echo", "printf", "mkdir", "rm", "cp", "mv", "chmod", "ln",
    "export", "source", "exec", "test", "time", "which", "du", "df", "head", "tail", "grep",
    "sed", "awk", "xargs", "tee", "ldd", "tar", "curl", "wget", "unzip", "open",
    # toolchain
    "git", "python", "python3", "pip", "pip3", "pytest", "conda", "uv", "node", "npm", "npx", "nvm",
    "sudo", "apt-get", "apt", "brew", "dnf", "pacman",
    # this project
    "3dcv", "3dcodeverse", "setup.sh", "blender", "ffmpeg", "gemini", "claude", "codex", "agy",
}

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)")
_BACKTICK_MD = re.compile(r"`([\w./~-]+\.md)`")


# --------------------------------------------------------------------------- helpers
def fenced_blocks(text: str) -> list[tuple[str, str]]:
    """[(language, body)] for every ``` fenced block (language ``""`` when unlabelled)."""
    blocks: list[tuple[str, str]] = []
    lang: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            if lang is None:
                lang = line.lstrip()[3:].strip().lower()
                body = []
            else:
                blocks.append((lang, "\n".join(body)))
                lang = None
                body = []
        elif lang is not None:
            body.append(line)
    return blocks


def shell_commands(body: str) -> list[str]:
    """First token of every command in a shell block (continuations joined, comments and
    leading ``VAR=value`` assignments stripped, ``&&``/``||``/``;``/``|`` split)."""
    out: list[str] = []
    text = re.sub(r"\\\n\s*", " ", body)  # join line continuations
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("$ #"):
            continue
        line = line.removeprefix("$ ").strip()
        line = re.sub(r"\s+#.*$", "", line)  # trailing comment
        for part in re.split(r"&&|\|\||[;|]", line):
            part = part.strip().strip("()").strip()
            if not part or part in {"then", "else", "fi", "do", "done"}:
                continue
            tokens = part.split()
            while tokens and _ENV_ASSIGN.match(tokens[0]):
                tokens.pop(0)
            if tokens:
                out.append(tokens[0])
    return out


def slugify(heading: str) -> str:
    """GitHub-style anchor slug for a markdown heading."""
    text = heading.strip().lstrip("#").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text.replace("`", ""))
    return re.sub(r"\s", "-", text)


def existing_docs() -> list[Path]:
    return [p for p in OWNED_DOCS if p.is_file()]


# --------------------------------------------------------------------------- tests
def test_install_guide_exists() -> None:
    assert INSTALL.is_file(), f"{INSTALL} is missing"
    text = INSTALL.read_text()
    assert len(text) > 2000, "docs/INSTALL.md looks truncated"
    for needle in ("runtime_js", "npm ci", "3dcodeverse doctor", "GEMINI_API_KEYS", "scripts/setup.sh"):
        assert needle in text, f"docs/INSTALL.md never mentions {needle!r}"


@pytest.mark.parametrize("doc", existing_docs(), ids=lambda p: str(p.relative_to(REPO)))
def test_shell_commands_use_known_binaries(doc: Path) -> None:
    bad: list[str] = []
    for lang, body in fenced_blocks(doc.read_text()):
        if lang not in SHELL_LANGS:
            continue
        for cmd in shell_commands(body):
            name = cmd.split("/")[-1]
            if cmd not in ALLOWED_BINARIES and name not in ALLOWED_BINARIES:
                bad.append(cmd)
    assert not bad, f"{doc}: unknown command(s) in shell blocks: {sorted(set(bad))}"


@pytest.mark.parametrize("doc", existing_docs(), ids=lambda p: str(p.relative_to(REPO)))
def test_internal_links_resolve(doc: Path) -> None:
    text = doc.read_text()
    headings = {slugify(ln) for ln in text.splitlines() if ln.startswith("#")}
    missing: list[str] = []
    for target in _MD_LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "<")):
            continue
        if target.startswith("#"):
            if target[1:] not in headings:
                missing.append(f"anchor {target}")
            continue
        path, _, anchor = target.partition("#")
        if path and not (doc.parent / path).exists():
            missing.append(f"link {target}")
    assert not missing, f"{doc}: unresolved {missing}"


@pytest.mark.parametrize("doc", existing_docs(), ids=lambda p: str(p.relative_to(REPO)))
def test_backticked_markdown_paths_resolve(doc: Path) -> None:
    """`docs/INSTALL.md`-style references must point at a file that exists."""
    missing = []
    for ref in sorted(set(_BACKTICK_MD.findall(doc.read_text()))):
        if ref.startswith("~"):
            continue
        if not any((base / ref).exists() for base in (doc.parent, HARNESS, REPO)):
            missing.append(ref)
    assert not missing, f"{doc}: backticked markdown path(s) do not exist: {missing}"


def test_setup_script_is_executable_and_parses() -> None:
    assert SETUP.is_file(), f"{SETUP} is missing"
    assert os.access(SETUP, os.X_OK), f"{SETUP} is not executable (chmod +x)"
    assert SETUP.read_text().startswith("#!"), "scripts/setup.sh has no shebang"
    proc = subprocess.run(["bash", "-n", str(SETUP)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"bash -n failed:\n{proc.stderr}"


def test_setup_script_help_works() -> None:
    proc = subprocess.run(["bash", str(SETUP), "--help"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"setup.sh --help exited {proc.returncode}:\n{proc.stderr}"
    assert "--extras" in proc.stdout


def test_every_pyproject_extra_is_documented() -> None:
    with (HARNESS / "pyproject.toml").open("rb") as fh:
        extras = tomllib.load(fh)["project"]["optional-dependencies"]
    text = INSTALL.read_text()
    undocumented = [name for name in extras if f"`{name}`" not in text]
    assert not undocumented, f"extras missing from docs/INSTALL.md: {undocumented}"


def test_doctor_rows_have_troubleshooting_entries() -> None:
    """Every check `3dcv doctor` can print must appear in the INSTALL troubleshooting table."""
    from codeverse.cli import doctor as doctor_mod

    text = INSTALL.read_text()
    names = {"python", "python deps", "blender", "node", "three", "puppeteer", "chrome webgl",
             "gemini keys", "anthropic key", "openai key", "gemini-cli", "claude", "codex", "agy",
             "git", "ffmpeg", "mcp"}
    assert hasattr(doctor_mod, "run_doctor")
    missing = [n for n in sorted(names) if f"`{n}`" not in text]
    assert not missing, f"doctor rows undocumented in docs/INSTALL.md: {missing}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
