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
import tomllib
from pathlib import Path

import pytest

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


#: `pytest ... -m "<expr>"` as published in the docs
_PYTEST_MARKER_EXPR = re.compile(r'pytest[^\n]*?-m\s+"([^"]+)"')
#: every file that publishes a pytest command a reader will paste
DOCS_WITH_TEST_COMMANDS = [INSTALL, HARNESS / "docs" / "RUNBOOK.md", HARNESS / "CLAUDE.md",
                           HARNESS / "README.md", REPO / "README.md"]


@pytest.mark.parametrize("doc", DOCS_WITH_TEST_COMMANDS, ids=lambda p: p.name)
def test_documented_pytest_subsets_never_re_enable_the_live_tests(doc: Path) -> None:
    """PORT-1: a command-line ``-m`` REPLACES ``addopts = ["-m", "not live"]``, it does not
    add to it.  The published "pure-python subset" line was ``-m "not blender and not
    node"``, which selects 23 live tests (2058 collected vs 2035) — run verbatim it opened
    real Gemini connections and hung against the intermittent provider, three lines above a
    sentence promising the opposite.  Every documented selection must spell ``not live``
    itself; the only exception is the deliberate ``-m live`` opt-in."""
    if not doc.is_file():  # the harness can be checked out without the repo README
        pytest.skip(f"{doc} not present")
    for expr in _PYTEST_MARKER_EXPR.findall(doc.read_text()):
        assert "not live" in expr or expr.strip() == "live", (
            f"{doc.name} publishes pytest -m \"{expr}\", which re-enables the live tests")


def test_doctor_checks_every_module_of_every_optional_extra(monkeypatch) -> None:
    """PORT-3: docs/INSTALL.md §4 says `3dcv doctor` lists every one of these modules under
    `python deps`, and docs/RUNBOOK.md:12 names moderngl explicitly — but neither moderngl
    nor cadquery was in ``_PY_DEPS``, so a venv installed with every extra EXCEPT
    [graphics] printed "21/21 importable" and exited 0, and the graphics track then died
    with "no usable OpenGL context: ModuleNotFoundError: No module named 'moderngl'" — a
    missing pip package reported as a GPU/driver problem."""
    import importlib

    from codeverse.doctor import _OPTIONAL_DEPS, _PY_DEPS, check_python_deps

    with (HARNESS / "pyproject.toml").open("rb") as fh:
        extras = tomllib.load(fh)["project"]["optional-dependencies"]
    wanted = {re.split(r"[<>=!\[ ]", req)[0].replace("-", "_")
              for name, reqs in extras.items() if name not in ("all", "dev") for req in reqs}
    assert not wanted - set(_PY_DEPS), f"extras doctor never checks: {sorted(wanted - set(_PY_DEPS))}"
    # ...and every one of them must also be ATTRIBUTED to its extra.  Checking without
    # attributing is what made a correct base install report itself broken: scipy (urdf),
    # networkx (mesh) and pyarrow (flywheel) were in _PY_DEPS but not in _OPTIONAL_DEPS, so
    # a fresh 3.10 clone installed exactly as docs/INSTALL.md §1 documents printed
    # `python deps FAIL 14/23` with a remedy that named four of the five extras and could
    # not clear the row.  Found on the sign-off clean-clone run, 2026-08-24.
    assert not wanted - set(_OPTIONAL_DEPS), (
        f"extras doctor checks but cannot name: {sorted(wanted - set(_OPTIONAL_DEPS))}")
    # the two lazily-imported tracks are a WARN naming their extra, not a FAIL: without them
    # the harness installs and starts fine and only that track raises (docs/INSTALL.md §4)
    assert _OPTIONAL_DEPS["moderngl"] == "graphics" and _OPTIONAL_DEPS["cadquery"] == "cad"

    real = importlib.import_module

    def no_graphics(name: str, *a, **kw):
        if name == "moderngl":
            raise ModuleNotFoundError("No module named 'moderngl'")
        return real(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", no_graphics)
    _, deps = check_python_deps()
    # the remedy names [graphics]; on a box that also lacks another extra (CI has no cadquery)
    # the extras merge into one `harness[cad,graphics]` string, which is still correct
    assert deps[1] == "WARN" and "moderngl" in deps[2] and re.search(r"harness\[[^\]]*\bgraphics\b", deps[2]), deps[2]

    # and the whole base install — every extra absent, which is what `pip install -e harness`
    # leaves behind — is a WARN whose remedy names EVERY missing extra, never a FAIL
    def base_install(name: str, *a, **kw):
        if name.split(".")[0] in _OPTIONAL_DEPS:
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", base_install)
    _, deps = check_python_deps()
    assert deps[1] == "WARN", f"a correct base install must not report FAIL: {deps[2]}"
    for extra in sorted(set(_OPTIONAL_DEPS.values())):
        assert extra in deps[2], f"the remedy does not name [{extra}]: {deps[2]}"


def test_doctor_rows_have_troubleshooting_entries() -> None:
    """Every check `3dcv doctor` can print must appear in the INSTALL troubleshooting table."""
    from codeverse import doctor as doctor_mod

    text = INSTALL.read_text()
    names = {"python", "python deps", "blender", "node", "three", "puppeteer", "chrome webgl",
             "gemini keys", "anthropic key", "openai key", "gemini quota", "gemini pool",
             "storm gate", "gemini-cli", "claude", "codex", "agy", "git", "ffmpeg", "mcp"}
    assert hasattr(doctor_mod, "run_doctor")
    missing = [n for n in sorted(names) if f"`{n}`" not in text]
    assert not missing, f"doctor rows undocumented in docs/INSTALL.md: {missing}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))


def test_architecture_package_map_covers_every_module():
    """`docs/ARCHITECTURE.md` is the maintained map of the codebase — a module that
    exists but is not on it is a hole in the map.

    Hand-maintained docs drift silently: three parallel waves on 2026-08-24 added six
    modules (brief, plan_budget, tokens, storm, health, _infra) and none reached the
    map.  A whole package (`reference/`, 11 modules) had never been on it at all.

    The doc's shorthand counts: an explicit name, a glob (`tools*.py`, `joints*.py`),
    or the bare stem in prose all satisfy it.  `wrappers/` subtrees are covered by the
    `wrappers/` entry, and private / dunder modules are exempt.
    """
    import fnmatch
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    arch = (root / "docs" / "ARCHITECTURE.md").read_text()
    globs = set(re.findall(r"[A-Za-z_][\w*]*\.py", arch))

    def documented(mod: Path) -> bool:
        return (any(fnmatch.fnmatch(mod.name, g) for g in globs)
                or re.search(rf"\b{re.escape(mod.stem)}\b", arch) is not None)

    missing = sorted(
        str(m.relative_to(root))
        for m in (root / "codeverse").rglob("*.py")
        if m.name != "__init__.py" and not m.name.startswith("_")
        and "wrappers" not in m.parts and not documented(m)
    )
    assert not missing, (
        "modules missing from the docs/ARCHITECTURE.md package map:\n  " + "\n  ".join(missing))


def test_setup_verifies_the_interpreter_it_installed_into(tmp_path) -> None:
    """PORT-6: the closing doctor is the installer's only verification, and it used to
    run `command -v 3dcodeverse` first.  With `--python` — the documented flag for
    installing into a NON-activated interpreter — that venv's entry point is not on PATH,
    so setup.sh ran a pre-existing install (here /home/.../miniconda3/bin/3dcodeverse,
    python 3.13) and printed an all-green table for an environment it never touched.
    An installer must verify what it installed."""
    import sys

    fake = tmp_path / "bin"
    fake.mkdir()
    impostor = fake / "3dcodeverse"
    impostor.write_text("#!/bin/sh\necho WRONG_INSTALL_ON_PATH\n")
    impostor.chmod(0o755)

    env = dict(os.environ, PATH=f"{fake}{os.pathsep}{os.environ['PATH']}")
    proc = subprocess.run(
        ["bash", str(SETUP), "--python", sys.executable, "--no-node", "--no-chrome", "--no-gpu", "--no-python"],
        capture_output=True, text=True, timeout=300, env=env)
    assert "WRONG_INSTALL_ON_PATH" not in proc.stdout + proc.stderr, (
        "setup.sh verified the 3dcodeverse first on PATH instead of the --python interpreter")
    assert "doctor" in proc.stdout


def test_setup_names_the_remedy_when_the_interpreter_has_no_pip(tmp_path) -> None:
    """PORT-7: on a stock Debian/Ubuntu box `/usr/bin/python3` ships without pip and is
    PEP 668 marked, so the primary documented install path passed the whole prerequisite
    block and then aborted on one unexplained line, `/usr/bin/python3: No module named
    pip`, exit 1.  setup.sh already die()s with actionable text for a missing python,
    git, npm and package-lock.json; pip was the gap in its own contract."""
    import sys

    # a stand-in that answers the version probes but refuses `-m pip`, like a distro python3
    nopip = tmp_path / "python-nopip"
    nopip.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do [ "$a" = "pip" ] && { echo "$0: No module named pip" >&2; exit 1; }; done\n'
        f'exec {sys.executable} "$@"\n')
    nopip.chmod(0o755)

    proc = subprocess.run(["bash", str(SETUP), "--python", str(nopip), "--no-node", "--no-chrome", "--no-doctor"],
                          capture_output=True, text=True, timeout=300)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, "a python that cannot pip must not look like a successful install"
    assert "has no pip" in out, f"the bare ModuleNotFoundError is not an explanation:\n{out}"
    assert "-m venv" in out and "python3-venv" in out, f"no actionable remedy in:\n{out}"


def test_install_docs_list_pip_and_venv_as_prerequisites() -> None:
    """The §2.2 table listed python/git/node/blender/ffmpeg but never pip or venv, and
    §3 called a virtualenv "recommended but not required" — untrue on the OS §2.1 names."""
    text = INSTALL.read_text()
    assert "python3-venv" in text and "EXTERNALLY-MANAGED" in text
    assert "pip + venv" in text, "the requirements table must name pip and venv"
