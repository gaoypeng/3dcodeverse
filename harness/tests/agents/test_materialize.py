"""materialize_workspace: bodies, ignore file, codex overrides."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib

from codeverse3d.agents.cli_common import default_mcp_command
from codeverse3d.agents.materialize import (
    codex_mcp_overrides,
    materialize_workspace,
)
from codeverse3d.workspace import Workspace

CONTRACT = "## blender contract\nWrite pure bpy into src/model.py."


def _mat(ws: Workspace, kind: str = "agy"):
    return materialize_workspace(ws, agent_kind=kind, contract_md=CONTRACT,
                                 mcp_command=default_mcp_command(ws))














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
