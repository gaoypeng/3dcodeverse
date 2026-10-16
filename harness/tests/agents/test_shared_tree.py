"""D111: a session that shares its workspace with lock-free harness writers is credited with its own files.

Argon loop iteration 4 (scene_threejs, 2026-10-15): the env session read for 7-10 minutes and wrote
nothing in 12 of 15 runs; the asset single-shots wrote ``src/assets/*`` beside it, the whole-tree diff
credited the env session with those files, its stage read ``ok``, and the same asset writes kept its
idle clock alive until ~300 s after the asset stage ended.  ``AgentJob.shared_tree`` narrows both
the attribution and the activity watch to ``files_hint``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from codeverse3d.agents.cli_common import (
    activity_paths,
    attribute_changes,
    begin_session,
    finish_session,
    run_with_watchdog,
)
from codeverse3d.contracts.agent import AgentJob, FileChange
from codeverse3d.contracts.common import Usage
from codeverse3d.tracks.generation import GenerationTask, _attributed_fallback
from codeverse3d.workspace import Workspace

ENV, ASSET = "src/env.js", "src/assets/lantern.js"


def _tree(ws: Workspace) -> None:
    (ws.src / "assets").mkdir(parents=True, exist_ok=True)
    (ws.root / ENV).write_text("// placeholder env\n")
    (ws.root / ASSET).write_text("// asset stub\n")
    ws.commit("skeleton")


def _job(ws: Workspace, **kw) -> AgentJob:
    return AgentJob(workspace=str(ws.root), prompt="p", label="env", round=0, files_hint=[ENV], **kw)


def test_attribution_under_only_drops_what_the_hint_does_not_name():
    files = [FileChange(path=ENV, status="modified"), FileChange(path=ASSET, status="modified"),
             FileChange(path="src/lib/x.js", status="added")]
    assert [f.path for f in attribute_changes(files, write_roots=["src"])] == [ENV, ASSET, "src/lib/x.js"]
    assert [f.path for f in attribute_changes(files, write_roots=["src"], only=[ENV])] == [ENV]
    assert [f.path for f in attribute_changes(files, write_roots=["src"], only=["src/assets/"])] == [ASSET]


def test_a_shared_tree_session_that_wrote_nothing_is_not_credited_with_a_concurrent_write(tmp_ws: Workspace):
    _tree(tmp_ws)
    s = begin_session(_job(tmp_ws, shared_tree=True), "agy")
    (tmp_ws.root / ASSET).write_text("// the asset single-shot's work, written meanwhile\n")
    res = finish_session(s, ok=True, exit_reason="timeout", text="", usage=Usage())
    assert res.files_changed == [], res.files_changed
    assert (tmp_ws.root / ASSET).read_text().startswith("// the asset single-shot"), "never reverted: not edit_only"


def test_a_shared_tree_session_that_wrote_its_file_is_credited_with_it_alone(tmp_ws: Workspace):
    _tree(tmp_ws)
    s = begin_session(_job(tmp_ws, shared_tree=True), "agy")
    (tmp_ws.root / ENV).write_text("// the real env\n")
    (tmp_ws.root / ASSET).write_text("// concurrent\n")
    res = finish_session(s, ok=True, exit_reason="completed", text="", usage=Usage())
    assert [f.path for f in res.files_changed] == [ENV]


def test_a_plain_session_still_owns_the_whole_diff(tmp_ws: Workspace):
    """Not shared_tree: a refine session that also fixed a file outside its hint keeps it (a narrower
    attribution would turn real work into ``ok=False`` and a RoundFailed)."""
    _tree(tmp_ws)
    s = begin_session(_job(tmp_ws), "agy")
    (tmp_ws.root / ASSET).write_text("// fixed here too\n")
    res = finish_session(s, ok=True, exit_reason="completed", text="", usage=Usage())
    assert [f.path for f in res.files_changed] == [ASSET]


def test_the_generation_fallback_attributes_the_same_way(tmp_ws: Workspace):
    _tree(tmp_ws)
    before = tmp_ws.head()
    (tmp_ws.root / ASSET).write_text("// concurrent\n")
    shared = GenerationTask(label="env", prompt="p", files_hint=[ENV], shared_tree=True)
    plain = shared.model_copy(update={"shared_tree": False})
    assert _attributed_fallback(tmp_ws, shared, before) == []
    assert [f.path for f in _attributed_fallback(tmp_ws, plain, before)] == [ASSET]


def test_a_shared_tree_session_watches_only_its_own_files(tmp_ws: Workspace):
    _tree(tmp_ws)
    s = begin_session(_job(tmp_ws, shared_tree=True), "agy")
    try:
        assert activity_paths(s) == [tmp_ws.root / ENV]
    finally:
        finish_session(s, ok=False, exit_reason="error", text="", usage=Usage())
    s = begin_session(_job(tmp_ws), "agy")
    try:
        assert activity_paths(s) == [tmp_ws.src, tmp_ws.public]
    finally:
        finish_session(s, ok=False, exit_reason="error", text="", usage=Usage())


def test_writes_to_other_files_do_not_keep_a_watched_file_session_alive(tmp_path: Path):
    """The watchdog with a FILE as its activity path: a sibling's writes beside it are not activity."""
    src = tmp_path / "src"
    src.mkdir()
    own = src / "env.js"
    own.write_text("x")
    code = ("import time\n"
            f"p = {str(src / 'asset.js')!r}\n"
            "for i in range(40):\n open(p, 'a').write('x'); time.sleep(0.25)\n")
    res = run_with_watchdog([sys.executable, "-c", code], cwd=tmp_path, env=None, soft_timeout_s=0.5, idle_grace_s=1.0,
                            hard_timeout_s=30, poll_s=0.1, scan_s=0.2, activity_dirs=[own])
    assert res.killed_reason == "idle" and res.duration_s < 8, (res.killed_reason, res.duration_s)


def test_shared_tree_needs_a_hint_and_excludes_edit_only(tmp_ws: Workspace):
    with pytest.raises(ValidationError):
        AgentJob(workspace=str(tmp_ws.root), prompt="p", shared_tree=True)
    with pytest.raises(ValidationError):
        _job(tmp_ws, shared_tree=True, edit_only=True)
