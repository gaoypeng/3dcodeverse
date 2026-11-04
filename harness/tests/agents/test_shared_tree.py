"""D111: a session that shares its workspace with lock-free harness writers is credited with its own files.

Argon loop iteration 4 (scene_threejs, 2026-10-15): the env session read for 7-10 minutes and wrote
nothing in 12 of 15 runs; the asset single-shots wrote ``src/assets/*`` beside it, the whole-tree diff
credited the env session with those files, its stage read ``ok``, and the same asset writes kept its
idle clock alive until ~300 s after the asset stage ended.  ``AgentJob.shared_tree`` narrows both
the attribution and the activity watch to ``files_hint``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codeverse3d.agents.cli_common import (
    begin_session,
    finish_session,
)
from codeverse3d.contracts.agent import AgentJob
from codeverse3d.contracts.common import Usage
from codeverse3d.workspace import Workspace

ENV, ASSET = "src/env.js", "src/assets/lantern.js"


def _tree(ws: Workspace) -> None:
    (ws.src / "assets").mkdir(parents=True, exist_ok=True)
    (ws.root / ENV).write_text("// placeholder env\n")
    (ws.root / ASSET).write_text("// asset stub\n")
    ws.commit("skeleton")


def _job(ws: Workspace, **kw) -> AgentJob:
    return AgentJob(workspace=str(ws.root), prompt="p", label="env", round=0, files_hint=[ENV], **kw)




def test_a_shared_tree_session_that_wrote_nothing_is_not_credited_with_a_concurrent_write(tmp_ws: Workspace):
    _tree(tmp_ws)
    s = begin_session(_job(tmp_ws, shared_tree=True), "agy")
    (tmp_ws.root / ASSET).write_text("// the asset single-shot's work, written meanwhile\n")
    res = finish_session(s, ok=True, exit_reason="timeout", text="", usage=Usage())
    assert res.files_changed == [], res.files_changed
    assert (tmp_ws.root / ASSET).read_text().startswith("// the asset single-shot"), "never reverted: not edit_only"










def test_shared_tree_needs_a_hint_and_excludes_edit_only(tmp_ws: Workspace):
    with pytest.raises(ValidationError):
        AgentJob(workspace=str(tmp_ws.root), prompt="p", shared_tree=True)
    with pytest.raises(ValidationError):
        _job(tmp_ws, shared_tree=True, edit_only=True)
