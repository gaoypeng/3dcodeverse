"""D38: ``record.environment.harness_git_sha`` names the checkout the harness ran from."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from codeverse.record import record as record_mod
from codeverse.record.record import _harness_git_sha, environment_versions

SHA_RE = re.compile(r"^[0-9a-f]{40}(-dirty)?$")


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _in_a_checkout() -> bool:
    here = Path(record_mod.__file__).resolve()
    return shutil.which("git") is not None and _git("ls-files", "--error-unmatch", here.name, cwd=here.parent).returncode == 0


@pytest.mark.skipif(not _in_a_checkout(), reason="harness is not running from a git checkout (wheel / sdist install)")
def test_harness_git_sha_is_populated_inside_the_repo():
    # the repo's .git lives ABOVE harness/ — the old probe (harness/.git) never matched
    sha = _harness_git_sha()
    assert SHA_RE.match(sha), sha
    assert environment_versions()["harness_git_sha"] == sha


def test_harness_git_sha_from_a_tracked_file_and_dirty_marker(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    repo = tmp_path / "repo"
    pkg = repo / "harness" / "codeverse" / "record"
    pkg.mkdir(parents=True)
    mod = pkg / "record.py"
    mod.write_text("# tracked\n")
    renderer = repo / "harness" / "runtime_js" / "render.cjs"
    renderer.parent.mkdir()
    renderer.write_text("// tracked\n")
    _git("init", "-q", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init", cwd=repo)
    head = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    assert _harness_git_sha(mod) == head
    # a modified tracked file under harness/ marks the sha dirty; an untracked file does not
    (repo / "harness" / "notes.txt").write_text("scratch")
    assert _harness_git_sha(mod) == head
    mod.write_text("# edited\n")
    assert _harness_git_sha(mod) == head + "-dirty"
    # harness/ is the tree, not harness/codeverse: an edited renderer changes what a run measures
    _git("checkout", "-q", "--", ".", cwd=repo)
    assert _harness_git_sha(mod) == head
    renderer.write_text("// edited\n")
    assert _harness_git_sha(mod) == head + "-dirty"


def test_harness_git_sha_is_empty_for_an_untracked_copy(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    # a venv / wheel copy inside SOME repo must not borrow that repo's sha
    repo = tmp_path / "other"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "tracked.txt").write_text("x")
    _git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init", cwd=repo)
    copy = repo / ".venv" / "lib" / "codeverse" / "record" / "record.py"
    copy.parent.mkdir(parents=True)
    copy.write_text("# untracked copy\n")
    assert _harness_git_sha(copy) == ""
    # and outside any repository
    loose = tmp_path / "loose" / "record.py"
    loose.parent.mkdir()
    loose.write_text("# no git\n")
    assert _harness_git_sha(loose) == ""
