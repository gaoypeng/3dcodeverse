"""CLI write-scope enforcement: per-workspace serialisation + post-hoc edit_only restore.

No backend has a write-time file gate (the in-process one that did was deleted
2026-08-28), and both session snapshots run
``git add -A``, so two concurrent CLI sessions in one workspace make provenance (and
any per-path rollback) unfixable.  ``begin_session`` therefore serialises
``EXCLUSIVE_KINDS`` per workspace, and ``finish_session`` reverts writes outside
``job.write_roots`` (always) and outside ``files_hint`` (``edit_only``) to the session's
own ``pre:`` commit and fails the session.
"""

from __future__ import annotations

import threading
import time

from codeverse.agents.cli_common import EXCLUSIVE_KINDS, begin_session, finish_session
from codeverse.contracts.agent import AgentJob
from codeverse.contracts.common import Usage
from codeverse.workspace import Workspace


def _job(ws: Workspace, label: str, **kw) -> AgentJob:
    return AgentJob(workspace=str(ws.root), prompt="p", label=label, round=0, **kw)


def _finish(s, *, ok: bool = True):
    return finish_session(s, ok=ok, exit_reason="completed", text="", usage=Usage())


# --------------------------------------------------------------------------- serialisation
def test_every_vendor_cli_kind_is_exclusive():
    assert {"claude-code", "codex", "gemini-cli", "agy"} == EXCLUSIVE_KINDS
    assert "fake" not in EXCLUSIVE_KINDS  # test doubles stay lock-free


def test_two_cli_sessions_on_one_workspace_are_serialized(tmp_ws: Workspace):
    """Two threads, one workspace: the begin→finish windows must not overlap."""
    windows: dict[str, tuple[float, float]] = {}
    started = threading.Barrier(2)

    def session(name: str) -> None:
        started.wait(timeout=30)  # both threads race begin_session together
        s = begin_session(_job(tmp_ws, name), "codex")
        t0 = time.monotonic()
        (tmp_ws.src / f"{name}.py").write_text(f"# {name}\n")
        time.sleep(0.15)  # long enough that overlapping windows would be seen
        t1 = time.monotonic()
        _finish(s)
        windows[name] = (t0, t1)

    threads = [threading.Thread(target=session, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert set(windows) == {"a", "b"}
    (a0, a1), (b0, b1) = windows["a"], windows["b"]
    assert a1 <= b0 or b1 <= a0, f"sessions interleaved: a={windows['a']} b={windows['b']}"


def test_an_unknown_kind_takes_no_lock(tmp_ws: Workspace):
    s1 = begin_session(_job(tmp_ws, "s1"), "fake")
    assert s1.ws_lock is None
    s2 = begin_session(_job(tmp_ws, "s2"), "fake")  # would deadlock if locked
    _finish(s2)
    _finish(s1)


def test_same_thread_can_begin_again_without_finishing(tmp_ws: Workspace):
    """The lock is re-entrant: argv-building tests begin sessions they never finish."""
    s1 = begin_session(_job(tmp_ws, "s1"), "codex")
    assert s1.ws_lock is not None
    s2 = begin_session(_job(tmp_ws, "s2"), "codex")
    _finish(s2)
    _finish(s1)  # double release stays idempotent via release_session


# --------------------------------------------------------------------------- post-hoc scope
def test_out_of_scope_cli_write_is_restored_and_fails_the_session(scoped_ws: Workspace):
    ws = scoped_ws
    job = _job(ws, "detail_seat", edit_only=True, files_hint=["src/parts/seat.py"])
    s = begin_session(job, "codex")
    (ws.src / "parts" / "seat.py").write_text("# new seat\n")     # hinted: kept
    (ws.src / "parts" / "leg.py").write_text("# rewritten leg\n")  # out of scope: restored
    (ws.src / "parts" / "bolt.py").write_text("# new part\n")      # new file: kept
    res = _finish(s, ok=True)
    assert not res.ok, "an out-of-scope write must fail the session"
    assert any("out-of-scope" in e and "src/parts/leg.py" in e for e in res.errors), res.errors
    assert (ws.src / "parts" / "leg.py").read_text() == "# leg\n", "restored to head_before"
    assert (ws.src / "parts" / "seat.py").read_text() == "# new seat\n"
    assert (ws.src / "parts" / "bolt.py").read_text() == "# new part\n"
    assert sorted(f.path for f in res.files_changed) == ["src/parts/bolt.py", "src/parts/seat.py"]


def test_write_roots_are_enforced_without_edit_only(scoped_ws: Workspace):
    """``write_roots`` was enforced for the api-agent (api_tools) and single-shot but NOT
    for CLI agents: ``_enforce_scope`` returned early unless edit_only+files_hint, so a
    plain codex session wrote and COMMITTED root-level evil.py / conftest.py and
    ``attribute_changes`` then hid them from files_changed (audit 2026-08-27)."""
    ws = scoped_ws
    (ws.root / "notes.md").write_text("original\n")
    s = begin_session(_job(ws, "baseline"), "codex")  # edit_only=False, write_roots=["src", "public"]
    (ws.src / "parts" / "leg.py").write_text("# in scope\n")
    (ws.root / "evil.py").write_text("import os\n")        # added outside write_roots → deleted
    (ws.root / "conftest.py").write_text("# hook\n")       # added outside write_roots → deleted
    (ws.root / "notes.md").write_text("tampered\n")        # modified outside write_roots → restored
    res = _finish(s)
    assert not res.ok, "an out-of-scope write must fail the session"
    assert not (ws.root / "evil.py").exists() and not (ws.root / "conftest.py").exists()
    assert (ws.root / "notes.md").read_text() == "original\n"
    assert "evil.py" not in ws._git("ls-files").stdout, "and it must never be committed"
    assert any("evil.py" in e and "conftest.py" in e for e in res.errors), res.errors
    assert (ws.src / "parts" / "leg.py").read_text() == "# in scope\n", "in-scope work is kept"
    assert [f.path for f in res.files_changed] == ["src/parts/leg.py"]


def test_entry_file_writes_need_ownership(scoped_ws: Workspace):
    ws = scoped_ws
    job = _job(ws, "part", edit_only=True, files_hint=["src/parts/seat.py"])
    s = begin_session(job, "gemini-cli")
    (ws.src / "model.py").write_text("# hijacked entry\n")
    res = _finish(s)
    assert not res.ok and (ws.src / "model.py").read_text() == "# entry\n"

    owned = _job(ws, "assemble", edit_only=True, files_hint=["src/parts/seat.py"],
                 always_writable=["src/model.py"])
    s2 = begin_session(owned, "gemini-cli")
    (ws.src / "model.py").write_text("# entry + import\n")
    res2 = _finish(s2)
    assert res2.ok and (ws.src / "model.py").read_text() == "# entry + import\n"


def test_unscoped_cli_sessions_keep_the_old_behaviour(scoped_ws: Workspace):
    ws = scoped_ws
    s = begin_session(_job(ws, "baseline"), "codex")  # edit_only=False
    (ws.src / "parts" / "leg.py").write_text("# fine\n")
    res = _finish(s)
    assert res.ok and (ws.src / "parts" / "leg.py").read_text() == "# fine\n"


def test_a_harness_owned_file_is_restored_when_the_agent_rewrites_it(tmp_ws, monkeypatch):
    """src/recipes.glsl is written by the harness for the agent to CALL.  FileTools used
    to refuse the write outright; with the in-process agent deleted (2026-08-28) the
    post-hoc check is the only guard, and without this layer the file was unprotected."""
    owned = "src/recipes.glsl"
    (tmp_ws.root / owned).parent.mkdir(parents=True, exist_ok=True)
    (tmp_ws.root / owned).write_text("float aurora(vec2 p){return 0.0;}\n")
    tmp_ws.commit("harness recipes")
    job = AgentJob(workspace=str(tmp_ws.root), prompt="p", label="gfx",
                   write_roots=["src/"], read_only=[owned])
    s = begin_session(job, "codex")
    (tmp_ws.root / owned).write_text("// clobbered by the agent\n")
    (tmp_ws.src / "shader.frag").write_text("void main(){}\n")
    res = finish_session(s, ok=True, exit_reason="completed", text="done", usage=Usage(backend="fake"))
    assert (tmp_ws.root / owned).read_text().startswith("float aurora"), "harness file must be restored"
    assert (tmp_ws.src / "shader.frag").is_file(), "the agent's own file survives"
    assert not res.ok and any(owned in e for e in res.errors)
