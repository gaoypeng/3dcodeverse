"""materialize_workspace: bodies, MCP configs, ignore files, cookbook, codex overrides."""

from __future__ import annotations

import json

import pytest

from codeverse.agents.cli_common import default_mcp_command
from codeverse.agents.materialize import C3V_DIR, codex_mcp_overrides, materialize_workspace
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
    assert "Spatial tools (c3v)" in body
    assert len(res.body_files) == 3


def test_mcp_configs_written_and_merged(tmp_ws: Workspace):
    gs = tmp_ws.root / ".gemini" / "settings.json"
    gs.parent.mkdir(parents=True)
    gs.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "ui": {"theme": "dark"}}))
    res = _mat(tmp_ws)
    data = json.loads(gs.read_text())
    assert data["ui"]["theme"] == "dark" and "other" in data["mcpServers"]
    c3v = data["mcpServers"]["c3v"]
    assert c3v["command"] == default_mcp_command(tmp_ws)[0]
    assert c3v["args"][:2] == ["-m", "codeverse.spatial.mcp_server"] and c3v["timeout"] == 600000
    mcp = json.loads((tmp_ws.root / ".mcp.json").read_text())
    assert mcp["mcpServers"]["c3v"]["type"] == "stdio"
    assert res.codex_overrides[1].startswith("mcp_servers.c3v.command=")
    assert "mcp_servers.c3v.args=[" in res.codex_overrides[3]
    assert "global" in res.agy_mcp.lower()
    for f in (".geminiignore", ".aiexclude"):
        assert "artifacts/" in (tmp_ws.root / f).read_text()


def test_spatial_disabled_drops_server_and_documents_absence(tmp_ws: Workspace):
    _mat(tmp_ws)
    res = _mat(tmp_ws, spatial=False)
    assert res.mcp_files == []
    assert "c3v" not in json.loads((tmp_ws.root / ".gemini" / "settings.json").read_text())["mcpServers"]
    assert "No spatial tools are available" in (tmp_ws.root / "AGENTS.md").read_text()


def test_cookbook_copied_when_found(tmp_ws: Workspace, tmp_path):
    cb = tmp_path / "cookbook.md"
    cb.write_text("# cookbook\nsnippet")
    res = _mat(tmp_ws, cookbook=str(cb))
    assert res.cookbook_path.endswith(f"{C3V_DIR}/cookbook.md")
    assert (tmp_ws.root / C3V_DIR / "cookbook.md").read_text().startswith("# cookbook")
    assert f"{C3V_DIR}/cookbook.md" in (tmp_ws.root / "AGENTS.md").read_text()
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
    assert ov[0] == "-c" and ov[1] == 'mcp_servers.c3v.command="python"'
    assert ov[3] == 'mcp_servers.c3v.args=["-m", "x", "--workspace", "/a b/c"]'


def test_spatial_requires_mcp_command(tmp_ws: Workspace):
    with pytest.raises(ValueError):
        materialize_workspace(tmp_ws, agent_kind="codex", contract_md="c", cookbook_rel="", spatial_tools=True, mcp_command=[])
