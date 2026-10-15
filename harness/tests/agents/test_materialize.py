"""materialize_workspace: bodies, ignore file, codex overrides."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib

from codeverse3d.agents.cli_common import default_mcp_command
from codeverse3d.agents.materialize import (
    LEGACY_COOKBOOK,
    codex_mcp_overrides,
    materialize_workspace,
    needs_rematerialize,
)
from codeverse3d.workspace import Workspace

CONTRACT = "## blender contract\nWrite pure bpy into src/model.py."


def _mat(ws: Workspace, kind: str = "agy", spatial: bool = True):
    return materialize_workspace(ws, agent_kind=kind, contract_md=CONTRACT, spatial_tools=spatial,
                                 mcp_command=default_mcp_command(ws))


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


def test_no_mcp_config_and_no_gemini_body_is_written_into_the_workspace(tmp_ws: Workspace):
    """3dcode reaches each CLI through a harness-owned per-session file (audit 2026-08-27), and
    agy reads GEMINI.md as well as AGENTS.md — a third copy of the body would be loaded twice."""
    for kind in ("agy", "claude-code", "codex"):
        _mat(tmp_ws, kind=kind)
    assert not (tmp_ws.root / ".mcp.json").exists(), "claude-code writes its own per-session mcp.json"
    assert not (tmp_ws.root / ".gemini").exists() and not (tmp_ws.root / "GEMINI.md").exists()
    assert {p.name for p in tmp_ws.root.glob("*.md")} >= {"AGENTS.md", "CLAUDE.md"}


def test_the_body_is_rules_tools_and_contract_and_never_mentions_a_cookbook(tmp_ws: Workspace):
    """No language ships a cookbook (D107, owner 2026-10-15): the body is Hard rules → Spatial tools →
    Language contract, says nothing about a cookbook (a note that one is missing only sends the agent
    looking), and writes nothing under ``.3dcode/`` for the agent to read."""
    tmp_ws.spec_path.write_text(json.dumps({"id": "t", "track": "static_object", "language": "blender", "prompt": "p"}))
    _mat(tmp_ws)
    body = (tmp_ws.root / "AGENTS.md").read_text()
    assert "cookbook" not in body.lower()
    assert body.index("## Hard rules") < body.index("## Spatial tools") < body.index("## Language contract")
    assert not (tmp_ws.root / ".3dcode").exists() or not any((tmp_ws.root / ".3dcode").iterdir())


def test_a_workspace_materialised_before_d107_is_rematerialised_and_loses_its_cookbook(tmp_ws: Workspace):
    """A resumed pre-D107 run still holds ``.3dcode/cookbook.md`` and a "## Cookbook" body section:
    ``needs_rematerialize`` says so (``ensure_materialized`` then writes the body again), and
    materialising removes the file."""
    tmp_ws.spec_path.write_text(json.dumps({"id": "t", "track": "static_object", "language": "blender", "prompt": "p"}))
    legacy = tmp_ws.root / LEGACY_COOKBOOK
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("# Cookbook\n")
    (tmp_ws.root / "AGENTS.md").write_text("## Cookbook\ncopy its patterns exactly\n")
    assert needs_rematerialize(tmp_ws)
    _mat(tmp_ws)
    assert not legacy.exists() and not needs_rematerialize(tmp_ws)
    assert "cookbook" not in (tmp_ws.root / "AGENTS.md").read_text().lower()


def test_ignore_files_keep_agent_facing_paths_readable(tmp_ws: Workspace):
    """Ignored paths are hidden from the agent: census and tool renders stay readable."""
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

    for f in (".antigravityignore",):
        lines = tuple(ln for ln in (tmp_ws.root / f).read_text().splitlines() if ln.strip())
        assert lines == IGNORE_LINES
        assert "artifacts/" not in lines and "trajectories/" in lines
        for readable in ("artifacts/census.json", "artifacts/build.json", "artifacts/measurement.json",
                         "artifacts/gates/r00/contract_tool.json", "artifacts/tool_renders/r00_ab/sheet.png",
                         "src/model.py", ".3dcode/images/ref.png"):
            assert not ignored(readable, lines), readable
        for hidden in ("artifacts/renders/r00/sheet.png", "trajectories/baseline_r00/stdout.json",
                       "trajectories/baseline_r00/prompt.md", "trajectories/baseline_r00/agy.log",
                       "trajectories/baseline_r00/stderr.log", "node_modules/three/x.js", ".git/HEAD"):
            assert ignored(hidden, lines), hidden


def test_kind_specific_tool_hint_and_spatial_disabled_documents_absence(tmp_ws: Workspace):
    _mat(tmp_ws, kind="claude-code")
    assert "mcp__3dcode__" in (tmp_ws.root / "CLAUDE.md").read_text()
    _mat(tmp_ws, kind="agy")
    body = (tmp_ws.root / "AGENTS.md").read_text()
    assert "MCP server `3dcode`" in body and "codeverse3d.cli.main tools" not in body   # always MCP: no shell fallback
    _mat(tmp_ws, spatial=False)
    assert "No spatial tools are available" in (tmp_ws.root / "AGENTS.md").read_text()


def test_codex_mcp_preserves_explicit_engine_selection(tmp_path, monkeypatch):
    """A caller-selected module must remain importable after Codex filters MCP env."""
    selected = tmp_path / "frozen engine"
    selected.mkdir()
    (selected / "c3d_selected_probe.py").write_text("IDENTITY = 'frozen-engine'\n")
    runtime = str(selected / "runtime_js")
    monkeypatch.setenv("PYTHONPATH", str(selected))
    monkeypatch.setenv("C3D_RUNTIME_JS", runtime)
    monkeypatch.setenv("UNRELATED_SERVICE_API_KEY", "test-only-sentinel")
    overrides = codex_mcp_overrides([sys.executable, "-m", "unused"])
    config = tomllib.loads("\n".join(overrides[1::2]))["mcp_servers"]["3dcode"]
    # Model the documented MCP allowlist and exercise actual Python resolution.
    forwarded = {name: os.environ[name] for name in config["env_vars"] if name in os.environ}
    run = subprocess.run(
        [sys.executable, "-c", "import c3d_selected_probe, os; "
         "print(c3d_selected_probe.IDENTITY); print(os.environ.get('C3D_RUNTIME_JS')); "
         "print('UNRELATED_SERVICE_API_KEY' in os.environ)"],
        env=forwarded, cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert run.stdout.splitlines() == ["frozen-engine", runtime, "False"]
