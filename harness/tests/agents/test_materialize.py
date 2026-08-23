"""materialize_workspace: bodies, MCP configs, ignore files, cookbook, codex overrides."""

from __future__ import annotations

import json

import pytest

from codeverse.agents.cli_common import default_mcp_command
from codeverse.agents.materialize import CV3D_DIR, codex_mcp_overrides, materialize_workspace
from codeverse.workspace import Workspace

CONTRACT = "## blender contract\nWrite pure bpy into src/model.py."


def _mat(ws: Workspace, kind: str = "gemini-cli", spatial: bool = True, cookbook: str = "missing/cookbook.md"):
    return materialize_workspace(ws, agent_kind=kind, contract_md=CONTRACT, cookbook_rel=cookbook,
                                 spatial_tools=spatial, mcp_command=default_mcp_command(ws))


def test_writes_three_bodies_same_content(tmp_ws: Workspace):
    res = _mat(tmp_ws)
    bodies = [(tmp_ws.root / n).read_text() for n in ("AGENTS.md", "GEMINI.md", "CLAUDE.md")]
    assert bodies[0] == bodies[1] == bodies[2]
    body = bodies[0]
    assert "ONLY under `src/` and `public/`" in body
    assert "never import from `codeverse`" in body.lower() or "Never import from `codeverse`" in body
    assert CONTRACT.splitlines()[0] in body
    assert "Spatial tools (3dcv)" in body
    assert len(res.body_files) == 3


def test_mcp_configs_written_and_merged(tmp_ws: Workspace):
    gs = tmp_ws.root / ".gemini" / "settings.json"
    gs.parent.mkdir(parents=True)
    gs.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "ui": {"theme": "dark"}}))
    res = _mat(tmp_ws)
    data = json.loads(gs.read_text())
    assert data["ui"]["theme"] == "dark" and "other" in data["mcpServers"]
    server = data["mcpServers"]["3dcv"]
    assert server["command"] == default_mcp_command(tmp_ws)[0]
    assert server["args"][:2] == ["-m", "codeverse.spatial.mcp_server"] and server["timeout"] == 600000
    mcp = json.loads((tmp_ws.root / ".mcp.json").read_text())
    assert mcp["mcpServers"]["3dcv"]["type"] == "stdio"
    assert res.codex_overrides[1].startswith("mcp_servers.3dcv.command=")
    assert "mcp_servers.3dcv.args=[" in res.codex_overrides[3]
    assert "global" in res.agy_mcp.lower()
    assert data["context"]["fileFiltering"]["respectGitIgnore"] is False  # .gitignore hides artifacts/ + trajectories/


def test_ignore_files_keep_agent_facing_paths_readable(tmp_ws: Workspace):
    """gemini-cli refuses read_file on ignored paths: the build census, tool renders and the
    long-prompt file (trajectories/<label>_rNN/task_prompt.md) must NOT be ignored."""
    import fnmatch

    from codeverse.agents.materialize import IGNORE_LINES

    res = _mat(tmp_ws)

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
        assert lines == IGNORE_LINES and str(tmp_ws.root / f) in res.ignore_files
        assert "artifacts/" not in lines and "trajectories/" not in lines
        for readable in ("artifacts/census.json", "artifacts/build_last.json", "artifacts/measurement.json",
                         "artifacts/gates/r00/contract_tool.json", "artifacts/tool_renders/r00_ab/sheet.png",
                         "trajectories/baseline_r00/task_prompt.md", "src/model.py", ".3dcv/cookbook.md"):
            assert not ignored(readable, lines), readable
        for hidden in ("artifacts/renders/r00/sheet.png", "trajectories/baseline_r00/stdout.json",
                       "trajectories/baseline_r00/stderr.log", "node_modules/three/x.js", ".git/HEAD"):
            assert ignored(hidden, lines), hidden


def test_spatial_disabled_drops_server_and_documents_absence(tmp_ws: Workspace):
    _mat(tmp_ws)
    res = _mat(tmp_ws, spatial=False)
    assert res.mcp_files == []
    assert "3dcv" not in json.loads((tmp_ws.root / ".gemini" / "settings.json").read_text())["mcpServers"]
    assert "No spatial tools are available" in (tmp_ws.root / "AGENTS.md").read_text()


def test_cookbook_copied_when_found(tmp_ws: Workspace, tmp_path):
    cb = tmp_path / "cookbook.md"
    cb.write_text("# cookbook\nsnippet")
    res = _mat(tmp_ws, cookbook=str(cb))
    assert res.cookbook_path.endswith(f"{CV3D_DIR}/cookbook.md")
    assert (tmp_ws.root / CV3D_DIR / "cookbook.md").read_text().startswith("# cookbook")
    assert f"{CV3D_DIR}/cookbook.md" in (tmp_ws.root / "AGENTS.md").read_text()
    assert not res.warnings


def test_missing_cookbook_is_a_warning(tmp_ws: Workspace):
    res = _mat(tmp_ws, cookbook="nope/cookbook.md")
    assert any("cookbook not found" in w for w in res.warnings)


def test_kind_specific_tool_hint(tmp_ws: Workspace):
    _mat(tmp_ws, kind="claude-code")
    assert "mcp__c3v__" in (tmp_ws.root / "CLAUDE.md").read_text()
    _mat(tmp_ws, kind="agy")
    assert "codeverse.cli.main tools" in (tmp_ws.root / "AGENTS.md").read_text()


def test_codex_overrides_are_valid_toml_fragments():
    ov = codex_mcp_overrides(["python", "-m", "x", "--workspace", "/a b/c"])
    assert ov[0] == "-c" and ov[1] == 'mcp_servers.3dcv.command="python"'
    assert ov[3] == 'mcp_servers.3dcv.args=["-m", "x", "--workspace", "/a b/c"]'
    assert ov[::2] == ["-c"] * (len(ov) // 2)
    keys = {kv.split("=", 1)[0]: kv.split("=", 1)[1] for kv in ov[1::2]}
    # codex exec cannot answer the per-tool approval elicitation: without this every 3dcv call is cancelled
    assert keys["mcp_servers.3dcv.default_tools_approval_mode"] == '"approve"'


def test_spatial_requires_mcp_command(tmp_ws: Workspace):
    with pytest.raises(ValueError):
        materialize_workspace(tmp_ws, agent_kind="codex", contract_md="c", cookbook_rel="", spatial_tools=True, mcp_command=[])
