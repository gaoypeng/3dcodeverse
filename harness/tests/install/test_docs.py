"""Offline guards for the install guide, READMEs, and setup script."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

HARNESS = Path(__file__).resolve().parents[2]
REPO = HARNESS.parent
INSTALL = HARNESS / "docs" / "INSTALL.md"
SETUP = HARNESS / "setup.sh"

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
    "3dcode", "3dcodeverse", "setup.sh", "blender", "gemini", "claude", "codex", "agy",
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
    for needle in ("runtime_js", "npm ci", "3dcodeverse doctor", "GEMINI_API_KEYS", "setup.sh"):
        assert needle in text, f"docs/INSTALL.md never mentions {needle!r}"


@pytest.mark.parametrize("doc", existing_docs(), ids=lambda p: str(p.relative_to(REPO)))
def test_owned_docs_have_valid_commands_links_and_paths(doc: Path) -> None:
    bad: list[str] = []
    text = doc.read_text()
    for lang, body in fenced_blocks(text):
        if lang not in SHELL_LANGS:
            continue
        for cmd in shell_commands(body):
            name = cmd.split("/")[-1]
            if cmd not in ALLOWED_BINARIES and name not in ALLOWED_BINARIES:
                bad.append(cmd)
    assert not bad, f"{doc}: unknown shell commands: {sorted(set(bad))}"

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
    assert not missing, f"{doc}: unresolved links: {missing}"

    missing_paths = []
    for ref in sorted(set(_BACKTICK_MD.findall(text))):
        if ref.startswith("~"):
            continue
        if not any((base / ref).exists() for base in (doc.parent, HARNESS, REPO)):
            missing_paths.append(ref)
    assert not missing_paths, f"{doc}: unresolved backticked paths: {missing_paths}"


def test_setup_script_is_executable_parses_and_has_help() -> None:
    assert SETUP.is_file(), f"{SETUP} is missing"
    assert os.access(SETUP, os.X_OK), f"{SETUP} is not executable (chmod +x)"
    assert SETUP.read_text().startswith("#!"), "setup.sh has no shebang"
    proc = subprocess.run(["bash", "-n", str(SETUP)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"bash -n failed:\n{proc.stderr}"
    proc = subprocess.run(["bash", str(SETUP), "--help"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"setup.sh --help exited {proc.returncode}:\n{proc.stderr}"
    assert "--extras" in proc.stdout


def test_every_pyproject_extra_is_documented() -> None:
    with (HARNESS / "pyproject.toml").open("rb") as fh:
        extras = tomllib.load(fh)["project"]["optional-dependencies"]
    text = INSTALL.read_text()
    undocumented = [name for name in extras if f"`{name}`" not in text]
    assert not undocumented, f"extras missing from docs/INSTALL.md: {undocumented}"


def test_bare_pytest_keeps_live_tests_opt_in() -> None:
    """A default local run must never spend API quota or wait on a provider."""
    with (HARNESS / "pyproject.toml").open("rb") as fh:
        addopts = tomllib.load(fh)["tool"]["pytest"]["ini_options"]["addopts"]
    assert "-m" in addopts
    assert addopts[addopts.index("-m") + 1] == "not live"


#: `pytest ... -m "<expr>"` as published in the docs
_PYTEST_MARKER_EXPR = re.compile(r'pytest[^\n]*?-m\s+"([^"]+)"')
#: every file that publishes a pytest command a reader will paste
DOCS_WITH_TEST_COMMANDS = [INSTALL, HARNESS / "docs" / "RUNBOOK.md", HARNESS / "CLAUDE.md",
                           HARNESS / "README.md", REPO / "README.md"]


@pytest.mark.parametrize("doc", DOCS_WITH_TEST_COMMANDS, ids=lambda p: p.name)
def test_documented_pytest_subsets_never_re_enable_the_live_tests(doc: Path) -> None:
    """Every documented marker override must keep live tests opt-in."""
    if not doc.is_file():  # the harness can be checked out without the repo README
        pytest.skip(f"{doc} not present")
    for expr in _PYTEST_MARKER_EXPR.findall(doc.read_text()):
        assert "not live" in expr or expr.strip() == "live", (
            f"{doc.name} publishes pytest -m \"{expr}\", which re-enables the live tests")


def test_doctor_checks_every_module_of_every_optional_extra(monkeypatch) -> None:
    """Every optional dependency is checked and attributed to its install extra."""
    import importlib

    from codeverse.doctor import _OPTIONAL_DEPS, _PY_DEPS, check_python_deps

    with (HARNESS / "pyproject.toml").open("rb") as fh:
        extras = tomllib.load(fh)["project"]["optional-dependencies"]
    wanted = {re.split(r"[<>=!\[ ]", req)[0].replace("-", "_")
              for name, reqs in extras.items() if name not in ("all", "dev") for req in reqs}
    assert not wanted - set(_PY_DEPS), f"extras doctor never checks: {sorted(wanted - set(_PY_DEPS))}"
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


def test_doctor_rows_have_troubleshooting_entries(monkeypatch) -> None:
    """Run real checks under deterministic stand-ins; require every row in the fix table."""
    from codeverse import doctor as doctor_mod
    from codeverse.config import Settings
    from codeverse.models.health import PoolBudget
    from codeverse.proc import ProcResult

    settings = SimpleNamespace(
        gemini_api_keys=("fake",), anthropic_api_key="", openai_api_key="",
        rate=SimpleNamespace(rpm_per_key=1, tpm_per_key=1_000, max_in_flight=1),
        binaries=SimpleNamespace(node="node", gemini_cli="gemini", claude_cli="claude",
                                 codex_cli="codex", agy_cli="agy"),
        resolve_blender=lambda: "blender",
        runtime_js_dir=lambda: HARNESS / "runtime_js",
    )
    monkeypatch.setattr(doctor_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(doctor_mod, "_ver", lambda *args, **kwargs: (True, "v1"))
    monkeypatch.setattr(
        "codeverse.proc.run_subprocess",
        lambda *args, **kwargs: ProcResult(0, '{"gpu": true, "renderer": "fake"}', "", False, 1),
    )
    monkeypatch.setattr("codeverse.models.health.pool_budget", lambda: PoolBudget(1, 64))
    monkeypatch.setattr(
        "codeverse.models.get_chat_model",
        lambda model: SimpleNamespace(generate=lambda request: SimpleNamespace(
            text="pong", usage=SimpleNamespace(cost_usd=0.0))),
    )
    gate = SimpleNamespace(snapshot=lambda: {
        "name": "gemini:test", "storming": False, "storms": 0, "hits": 0,
        "probes": 0, "parked_s": 0, "closed_for_s": 0,
    })
    monkeypatch.setattr("codeverse.models.retry.all_gates", lambda: [gate])
    monkeypatch.setattr("codeverse.skills.registry.ROUTED_SKILLS", ("missing",))

    rows = doctor_mod.run_doctor(live=True, gpu=True, skills=True)

    def no_runtime() -> Path:
        raise RuntimeError("runtime_js unavailable")

    monkeypatch.setattr(
        doctor_mod, "get_settings",
        lambda: SimpleNamespace(binaries=settings.binaries, runtime_js_dir=no_runtime),
    )
    rows += doctor_mod.check_node()  # the alternate row when runtime_js cannot resolve

    pool_detail = next(detail for name, _, detail in rows if name == "pool sharing")
    mentioned_settings = set(re.findall(r"\b(CV3D_[A-Z0-9_]+)=", pool_detail))
    assert mentioned_settings and mentioned_settings <= set(Settings._FLAT_ALIASES), (
        f"doctor recommends setting unknown variables: {sorted(mentioned_settings - set(Settings._FLAT_ALIASES))}")

    troubleshooting = INSTALL.read_text().split("### Troubleshooting table", 1)[1].split("\n---", 1)[0]
    documented = {
        name
        for line in troubleshooting.splitlines() if line.startswith("|")
        for name in re.findall(r"`([^`]+)`", line.split("|", 2)[1])
        if not name.startswith("--")
    }
    names = {"storm gate" if name.startswith("storm gate ") else name for name, _, _ in rows}
    missing = sorted(names - documented)
    assert not missing, f"doctor rows undocumented in docs/INSTALL.md: {missing}"


def test_architecture_package_map_covers_every_module():
    """Every public module appears in the architecture package-map code block."""
    import fnmatch

    root = Path(__file__).resolve().parents[2]
    arch = (root / "docs" / "ARCHITECTURE.md").read_text()
    _, marker, rest = arch.partition("## 2. Package map (as built)")
    assert marker, "docs/ARCHITECTURE.md has no package-map section"
    blocks = fenced_blocks(rest)
    assert blocks, "docs/ARCHITECTURE.md package map has no fenced code block"
    package_map = blocks[0][1]
    globs = set(re.findall(r"[A-Za-z_][\w*]*\.py", package_map))

    def documented(mod: Path) -> bool:
        return (any(fnmatch.fnmatch(mod.name, g) for g in globs)
                or re.search(rf"\b{re.escape(mod.stem)}\b", package_map) is not None)

    missing = sorted(
        str(m.relative_to(root))
        for m in (root / "codeverse").rglob("*.py")
        if m.name != "__init__.py" and not m.name.startswith("_")
        and "wrappers" not in m.parts and not documented(m)
    )
    assert not missing, (
        "modules missing from the docs/ARCHITECTURE.md package map:\n  " + "\n  ".join(missing))


def test_setup_verifies_the_interpreter_it_installed_into(tmp_path) -> None:
    """The closing doctor uses ``--python``, not an unrelated executable on PATH."""
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
    """A Python without pip fails with an actionable distro/venv remedy."""
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
    text = INSTALL.read_text()
    assert "python3-venv" in text and "EXTERNALLY-MANAGED" in text
    assert "pip + venv" in text, "the requirements table must name pip and venv"
