"""FileTools sandbox + run_shell allowlist."""

from __future__ import annotations

import os

from codeverse.agents.api_tools import FileTools
from codeverse.workspace import Workspace


def _tools(ws: Workspace) -> FileTools:
    return FileTools(ws, ["src", "public"])


def test_write_read_edit_list(tmp_ws: Workspace):
    t = _tools(tmp_ws)
    out = t.call("write_file", {"path": "src/a.txt", "content": "hello world\n"})
    assert not out.is_error and "created src/a.txt" in out.text
    assert t.call("read_file", {"path": "src/a.txt"}).text == "hello world\n"
    e = t.call("edit_file", {"path": "src/a.txt", "old": "world", "new": "there"})
    assert not e.is_error and t.call("read_file", {"path": "src/a.txt"}).text == "hello there\n"
    assert t.call("edit_file", {"path": "src/a.txt", "old": "nope", "new": "x"}).is_error
    t.call("write_file", {"path": "src/b.txt", "content": "aa aa"})
    assert t.call("edit_file", {"path": "src/b.txt", "old": "aa", "new": "b"}).is_error  # ambiguous
    assert not t.call("edit_file", {"path": "src/b.txt", "old": "aa", "new": "b", "all": True}).is_error
    listing = t.call("list_files", {"glob": "src/*.txt"}).text
    assert "src/a.txt" in listing and "src/b.txt" in listing
    assert t.writes == ["src/a.txt", "src/a.txt", "src/b.txt", "src/b.txt"]


def test_path_sandbox(tmp_ws: Workspace):
    t = _tools(tmp_ws)
    assert t.call("write_file", {"path": "../escape.txt", "content": "x"}).is_error
    assert t.call("write_file", {"path": "artifacts/x.txt", "content": "x"}).is_error
    assert t.call("write_file", {"path": "/etc/passwd", "content": "x"}).is_error
    assert t.call("read_file", {"path": ".git/HEAD"}).is_error
    assert t.call("read_file", {"path": "../../etc/hostname"}).is_error
    (tmp_ws.artifacts / "build.json").write_text("{}")
    assert t.call("read_file", {"path": "artifacts/build.json"}).text == "{}"  # reads outside write roots are fine
    # symlink escape
    os.symlink("/etc", tmp_ws.src / "etc_link")
    assert t.call("write_file", {"path": "src/etc_link/x", "content": "x"}).is_error


def test_run_shell_allowlist(tmp_ws: Workspace):
    t = _tools(tmp_ws)
    (tmp_ws.src / "ok.py").write_text("x = 1\n")
    out = t.call("run_shell", {"cmd": "python -m py_compile src/ok.py"})
    assert not out.is_error and "rc=0" in out.text
    assert t.call("run_shell", {"cmd": "rm -rf src"}).is_error
    assert t.call("run_shell", {"cmd": "ls src | cat"}).is_error
    assert t.call("run_shell", {"cmd": "cat /etc/passwd"}).is_error
    bad = t.call("run_shell", {"cmd": "python -c 'import sys; sys.exit(3)'"})
    assert bad.is_error and "rc=3" in bad.text
    slow = t.call("run_shell", {"cmd": "python -c 'import time; time.sleep(30)'", "timeout_s": 5})
    assert slow.is_error and "timeout" in slow.text


def test_unknown_tool_and_bad_args(tmp_ws: Workspace):
    t = _tools(tmp_ws)
    assert t.call("nope", {}).is_error
    assert t.call("read_file", {"wrong": 1}).is_error
