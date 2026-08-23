"""FileTools confinement + run_shell policy (allow-list, no inline code, workspace-only paths, never raises)."""

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
    (tmp_ws.src / "exit3.py").write_text("import sys; sys.exit(3)\n")
    bad = t.call("run_shell", {"cmd": "python src/exit3.py"})
    assert bad.is_error and "rc=3" in bad.text
    (tmp_ws.src / "slow.py").write_text("import time; time.sleep(30)\n")
    slow = t.call("run_shell", {"cmd": "python src/slow.py", "timeout_s": 5})
    assert slow.is_error and "timeout" in slow.text


def test_run_shell_rejects_inline_code_and_escaping_paths(tmp_ws: Workspace):
    """Regression: `python -c` / `node -e` / `..` paths used to bypass write_roots and the path check entirely."""
    t = _tools(tmp_ws)
    outside = tmp_ws.root.parent / "outside.txt"
    denied = [
        f"python -c \"open('{outside}','w').write('x')\"",
        "python3 -c 'import subprocess; subprocess.run([\"id\"])'",
        "python -Bc 'print(1)'",  # combined short flags
        "python -i",
        "python -",
        "python -m http.server",
        "python -mhttp.server",
        "python -cm py_compile",  # = python -c "m"
        "python -mc py_compile",
        "node -e \"require('fs').writeFileSync('../outside2.txt','y')\"",
        "node -pe 1",
        "node --eval 1",
        "node --eval=1",
        "node --require=../x.js src/a.js",
        "node -r ../x.js src/a.js",
        "cat ../../../../../../etc/hostname",
        "cat /etc/hostname",
        "cat .git/HEAD",
        "ls ~",
        "find src -exec rm {} ;",
        "find src -delete",
    ]
    for cmd in denied:
        out = t.call("run_shell", {"cmd": cmd})
        assert out.is_error and "rc=" not in out.text, (cmd, out.text)
    assert not outside.exists() and not (tmp_ws.root.parent / "outside2.txt").exists()
    # allowed shapes keep working
    (tmp_ws.src / "a.js").write_text("const x = 1;\n")
    (tmp_ws.src / "m.py").write_text("y = 2\n")
    for cmd in ("node --check src/a.js", "python -m py_compile src/m.py", "python3 -m json.tool --help",
                "grep -rn const src", "find src -name '*.js'", "ls -la src", "wc -l src/a.js", "head -n 1 src/a.js",
                f"cat {tmp_ws.root}/src/a.js"):
        out = t.call("run_shell", {"cmd": cmd})
        assert "rc=0" in out.text, (cmd, out.text)
    # symlink inside the workspace that points outside is resolved and refused
    os.symlink("/etc", tmp_ws.src / "etc_link")
    assert t.call("run_shell", {"cmd": "cat src/etc_link/hostname"}).is_error


def test_call_never_raises(tmp_ws: Workspace):
    """ValueError / UnicodeDecodeError used to escape call() and crash ApiAgent.run."""
    t = _tools(tmp_ws)
    (tmp_ws.src / "b.bin").write_bytes(b"a\xffb")
    (tmp_ws.src / "a.js").write_text("hello")
    for name, args in [("run_shell", {"cmd": "ls src", "timeout_s": "soon"}),
                       ("run_shell", {"cmd": "ls src", "timeout_s": "60.0"}),
                       ("run_shell", {"cmd": "ls src", "timeout_s": None}),
                       ("edit_file", {"path": "src/b.bin", "old": "a", "new": "b"}),
                       ("edit_file", {"path": "src/a.js", "old": "hello", "new": None}),
                       ("write_file", {"path": "src/c.js", "content": None}),
                       ("list_files", {"glob": None}),
                       ("read_file", {"path": 7})]:
        out = t.call(name, args)  # must not raise
        assert isinstance(out.text, str)
    assert not t.call("run_shell", {"cmd": "ls src", "timeout_s": "soon"}).is_error
    e = t.call("edit_file", {"path": "src/b.bin", "old": "a", "new": "b"})
    assert e.is_error and "UTF-8" in e.text and (tmp_ws.src / "b.bin").read_bytes() == b"a\xffb"


def test_unknown_tool_and_bad_args(tmp_ws: Workspace):
    t = _tools(tmp_ws)
    assert t.call("nope", {}).is_error
    assert t.call("read_file", {"wrong": 1}).is_error
