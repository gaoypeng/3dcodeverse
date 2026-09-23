"""materialize_workspace: bodies, MCP configs, ignore files, cookbook, codex overrides."""

from __future__ import annotations

import json

from codeverse3d.agents.cli_common import default_mcp_command
from codeverse3d.agents.materialize import materialize_workspace
from codeverse3d.workspace import Workspace

CONTRACT = "## blender contract\nWrite pure bpy into src/model.py."


def _mat(ws: Workspace, kind: str = "gemini-cli", spatial: bool = True, cookbook: str = ""):
    return materialize_workspace(ws, agent_kind=kind, contract_md=CONTRACT, cookbook_text=cookbook,
                                 spatial_tools=spatial, mcp_command=default_mcp_command(ws))


def test_bodies_document_only_this_tracks_tools(tmp_ws: Workspace):
    """Scene/graphics agents were once taught dead-end object-GLB tools (V11a)."""
    tmp_ws.spec_path.write_text(json.dumps({"id": "t", "track": "scene", "language": "scene_threejs", "prompt": "p"}))
    _mat(tmp_ws, kind="agy")
    body = (tmp_ws.root / "AGENTS.md").read_text()
    for absent in ("`joint_sweep`", "`gl_probe`", "`texture_pass`", "`render_sheet`", "`check_contract`", "`cross_section`"):
        assert absent not in body, absent
    assert "`scene_views`" in body and "`scene_probe`" in body and "`build`" in body
    assert "check_contract` to prove" not in body, "the object workflow sentence must not survive filtering"
    # an object workspace keeps the full object toolset and its workflow sentence
    tmp_ws.spec_path.write_text(json.dumps({"id": "t", "track": "static_object", "language": "blender", "prompt": "p"}))
    _mat(tmp_ws, kind="agy")
    body = (tmp_ws.root / "AGENTS.md").read_text()
    assert "`render_sheet`" in body and "`measure`" in body and "`check_contract`" in body
    # no spec.json (bare workspaces in tests/tools): unfiltered fallback, nothing crashes
    tmp_ws.spec_path.unlink()
    _mat(tmp_ws, kind="agy")
    assert "`joint_sweep`" in (tmp_ws.root / "AGENTS.md").read_text()


def test_no_mcp_server_is_written_into_the_workspace(tmp_ws: Workspace):
    """Only the ``context`` block goes into the agent-writable ws/.gemini/settings.json;
    3dcode reaches each CLI through a harness-owned per-session file (audit 2026-08-27)."""
    gs = tmp_ws.root / ".gemini" / "settings.json"
    gs.parent.mkdir(parents=True)
    gs.write_text(json.dumps({"mcpServers": {"3dcode": {"command": "/tmp/evil"}}, "ui": {"theme": "dark"}}))
    _mat(tmp_ws)
    data = json.loads(gs.read_text())
    assert data["ui"]["theme"] == "dark"
    assert "3dcode" not in data["mcpServers"], "an agent-planted 3dcode impostor must be dropped"
    assert not (tmp_ws.root / ".mcp.json").exists(), "claude-code writes its own per-session mcp.json"
    assert data["context"]["fileFiltering"]["respectGitIgnore"] is False  # .gitignore hides artifacts/ + trajectories/


def test_ignore_files_keep_agent_facing_paths_readable_and_a_missing_cookbook_is_said(tmp_ws: Workspace):
    """gemini-cli refuses read_file on ignored paths: census and tool renders stay readable."""
    import fnmatch

    from codeverse3d.agents.materialize import IGNORE_LINES

    _mat(tmp_ws)

    def ignored(rel: str, lines: tuple[str, ...]) -> bool:
        for pat in lines:
            if pat.endswith("/"):
                if rel.startswith(pat) or f"/{pat}" in f"/{rel}":
                    return True
            elif fnmatch.fnmatch(rel, pat):
                return True
        return False

    for f in (".geminiignore", ".aiexclude"):
        lines = tuple(ln for ln in (tmp_ws.root / f).read_text().splitlines() if ln.strip())
        assert lines == IGNORE_LINES
        assert "artifacts/" not in lines and "trajectories/" in lines
        for readable in ("artifacts/census.json", "artifacts/build.json", "artifacts/measurement.json",
                         "artifacts/gates/r00/contract_tool.json", "artifacts/tool_renders/r00_ab/sheet.png",
                         "src/model.py", ".3dcode/cookbook.md"):
            assert not ignored(readable, lines), readable
        for hidden in ("artifacts/renders/r00/sheet.png", "trajectories/baseline_r00/stdout.json",
                       "trajectories/baseline_r00/prompt.md", "trajectories/baseline_r00/gemini_settings.json",
                       "trajectories/baseline_r00/stderr.log", "node_modules/three/x.js", ".git/HEAD"):
            assert ignored(hidden, lines), hidden
    # no cookbook resolved (the default here): the body says so rather than naming a missing file
    assert "No cookbook is available" in (tmp_ws.root / "AGENTS.md").read_text()


def test_kind_specific_tool_hint_and_spatial_disabled_documents_absence(tmp_ws: Workspace):
    _mat(tmp_ws, kind="claude-code")
    assert "mcp__3dcode__" in (tmp_ws.root / "CLAUDE.md").read_text()
    _mat(tmp_ws, kind="agy")
    assert "codeverse3d.cli.main tools" in (tmp_ws.root / "AGENTS.md").read_text()
    _mat(tmp_ws, spatial=False)
    assert "No spatial tools are available" in (tmp_ws.root / "AGENTS.md").read_text()
