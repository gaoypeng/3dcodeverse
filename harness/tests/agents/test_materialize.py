"""materialize_workspace: bodies, MCP configs, ignore files, cookbook, codex overrides."""

from __future__ import annotations

import json
import logging
import sys

from codeverse3d.agents.cli_common import default_mcp_command
from codeverse3d.agents.materialize import C3D_DIR, codex_mcp_overrides, materialize_workspace
from codeverse3d.workspace import Workspace

CONTRACT = "## blender contract\nWrite pure bpy into src/model.py."


def _mat(ws: Workspace, kind: str = "gemini-cli", spatial: bool = True, cookbook: str = ""):
    return materialize_workspace(ws, agent_kind=kind, contract_md=CONTRACT, cookbook_text=cookbook,
                                 spatial_tools=spatial, mcp_command=default_mcp_command(ws))


def test_writes_three_bodies_same_content(tmp_ws: Workspace):
    _mat(tmp_ws)
    bodies = [(tmp_ws.root / n).read_text() for n in ("AGENTS.md", "GEMINI.md", "CLAUDE.md")]
    assert bodies[0] == bodies[1] == bodies[2]
    body = bodies[0]
    assert "ONLY under `src/` and `public/`" in body
    assert "never import from `codeverse3d`" in body.lower() or "Never import from `codeverse3d`" in body
    assert CONTRACT.splitlines()[0] in body
    assert "Spatial tools (3dcode)" in body


def test_bodies_document_only_this_tracks_tools(tmp_ws: Workspace):
    """AGENTS.md used to list all 19 tools + the object workflow for every track — for
    agy (no MCP) this file is the ONLY tool documentation and its shell fallback is
    unscoped, so scene/graphics agents were taught dead-end object-GLB tools (V11a)."""
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


def test_ignore_files_keep_agent_facing_paths_readable(tmp_ws: Workspace):
    """gemini-cli refuses read_file on ignored paths: the build census and tool renders must NOT
    be ignored.  trajectories/ is, whole: the prompt reaches every CLI on stdin since 2026-09-22,
    so nothing there is the agent's to read."""
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


def test_spatial_disabled_documents_absence(tmp_ws: Workspace):
    _mat(tmp_ws, spatial=False)
    assert "No spatial tools are available" in (tmp_ws.root / "AGENTS.md").read_text()


def test_cookbook_copied_when_found(tmp_ws: Workspace, caplog):
    with caplog.at_level(logging.WARNING, logger="codeverse3d.agents.materialize"):
        _mat(tmp_ws, cookbook="# cookbook\nsnippet")
    assert (tmp_ws.root / C3D_DIR / "cookbook.md").read_text() == "# cookbook\nsnippet"
    assert f"{C3D_DIR}/cookbook.md" in (tmp_ws.root / "AGENTS.md").read_text()
    assert not [r for r in caplog.records if r.name == "codeverse3d.agents.materialize"], \
        "a resolved cookbook must not warn"


def test_missing_cookbook_warns_where_someone_can_see_it(tmp_ws: Workspace, caplog):
    """The unresolved cookbook is the failure the cookbook path lookup (`cookbook_rel_for`, gone)
    was fixed for (an articulated run told the agent "No cookbook is available" while its 24 kB cookbook sat
    on disk).  It used to land in a `Materialized.warnings` list every caller threw away; the
    log line is the whole signal now, so it has to fire."""
    with caplog.at_level(logging.WARNING, logger="codeverse3d.agents.materialize"):
        _mat(tmp_ws, cookbook="")
    assert any("cookbook not found" in r.getMessage() for r in caplog.records)
    assert "No cookbook is available" in (tmp_ws.root / "AGENTS.md").read_text()


def test_kind_specific_tool_hint(tmp_ws: Workspace):
    _mat(tmp_ws, kind="claude-code")
    assert "mcp__3dcode__" in (tmp_ws.root / "CLAUDE.md").read_text()
    _mat(tmp_ws, kind="agy")
    assert "codeverse3d.cli.main tools" in (tmp_ws.root / "AGENTS.md").read_text()


def test_codex_overrides_are_valid_toml_fragments():
    ov = codex_mcp_overrides(["python", "-m", "x", "--workspace", "/a b/c"])
    assert ov[0] == "-c" and ov[1] == 'mcp_servers.3dcode.command="python"'
    assert ov[3] == 'mcp_servers.3dcode.args=["-m", "x", "--workspace", "/a b/c"]'
    assert ov[::2] == ["-c"] * (len(ov) // 2)
    keys = {kv.split("=", 1)[0]: kv.split("=", 1)[1] for kv in ov[1::2]}
    # codex exec cannot answer the per-tool approval elicitation: without this every 3dcode call is cancelled
    assert keys["mcp_servers.3dcode.default_tools_approval_mode"] == '"approve"'


def test_default_mcp_command_is_the_backends_interpreter(tmp_ws: Workspace):
    # one place knows the command: the body + codex overrides quote sys.executable, never bare "python"
    materialize_workspace(tmp_ws, agent_kind="codex", contract_md="c", cookbook_text="", spatial_tools=True)
    body = (tmp_ws.root / "AGENTS.md").read_text()
    assert default_mcp_command(tmp_ws)[0] == sys.executable
    assert f"command: `{' '.join(default_mcp_command(tmp_ws))}`" in body
