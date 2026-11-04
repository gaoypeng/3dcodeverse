"""Per-workspace agent instructions + ignore file.

One shared body is written to ``AGENTS.md`` (codex / agy) and ``CLAUDE.md`` (claude-code):
rules, the ``3dcode`` tool cards and the language contract.  MCP wiring is never a file in
the workspace (it is agent-writable).  Skills are written per round by ``codeverse3d/skills``.
"""

from __future__ import annotations

import json
import logging

from codeverse3d.agents.cli_common import default_mcp_command
from codeverse3d.proc import read_json_or_none
from codeverse3d.skills.materialize import BODY_FILES
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

MCP_SERVER_NAME = "3dcode"
MCP_TOOL_TIMEOUT_MS = 600_000
#: agy's ignore file: hides harness-private noise only; the agent-facing artefacts stay readable.
IGNORE_FILE = ".antigravityignore"
IGNORE_LINES = (
    "artifacts/renders/", "artifacts/judge/", "trajectories/",
    "stages/", "rounds/", "_cand/", "_assets/",
    ".git/", "node_modules/", "__pycache__/", ".gemini/tmp/",
)


# --------------------------------------------------------------------------- body
def _tool_section(agent_kind: str, mcp_command: list[str], ws: Workspace | None = None) -> str:
    from codeverse3d.spatial.registry import list_tools

    # filter to the MCP server's track/language tool set (this file is agy's only tool documentation)
    spec = (read_json_or_none(ws.spec_path) if ws is not None else None) or {}
    tools = list_tools(track=str(spec.get("track", "")), language=str(spec.get("language", "")))
    names = {t.name for t in tools}
    cards = "\n".join(t.card() for t in tools) or "(the tool registry is empty in this environment)"
    look = [n for n in ("render_views", "render_sheet", "scene_views", "gl_frames") if n in names]
    prove = [n for n in ("measure", "check_contract", "scene_probe", "gl_probe") if n in names]
    if agent_kind == "claude-code":
        how = (f"Tools are exposed by the MCP server `{MCP_SERVER_NAME}`; their names appear as "
               f"`mcp__{MCP_SERVER_NAME}__<name>` (e.g. `mcp__{MCP_SERVER_NAME}__build`).")
    elif agent_kind == "agy":
        how = (f"Tools are exposed by the MCP server `{MCP_SERVER_NAME}`; call them by name and prefer them "
               "over shell commands.")
    else:
        how = f"Tools are exposed by the MCP server `{MCP_SERVER_NAME}` (command: `{' '.join(mcp_command)}`); call them by name."
    workflow = (
        "Workflow: edit → `build` → read the errors/numbers → fix → `build` again."
        + (f" Use {' / '.join(f'`{n}`' for n in look)} to LOOK at what you made before declaring it finished;" if look else "")
        + (f" use {' / '.join(f'`{n}`' for n in prove)} to prove it." if prove else "")
        + " Never finish on a failing build."
    )
    return (
        "## Spatial tools (3dcode)\n\n"
        f"{how}\n\n"
        f"{workflow}\n\n"
        f"{cards}\n"
    )


def _body(agent_kind: str, contract_md: str, mcp_command: list[str],
          ws: Workspace | None = None) -> str:
    return (
        "# 3dcode workspace — rules for the coding agent\n\n"
        "You are working inside a harness-managed workspace. Read this whole file before acting.\n\n"
        "## Hard rules\n\n"
        "1. You may create or edit files ONLY under `src/` and `public/`. Never modify `artifacts/`, "
        "`trajectories/`, `.git/`, `.gemini/`, `.3dcode/`, or this file — they are harness-owned.\n"
        "2. Write RAW code in the language named by the contract below. Never import from `codeverse3d` or "
        "any helper SDK (harness-owned modules the contract ships INSIDE your src/ are the one "
        "exception — call them, never rewrite them); do not install packages; do not use the network; do not write "
        "camera / render / export / file-output code unless the contract explicitly asks for it — "
        "the harness owns export and rendering.\n"
        "3. After a file's first write, change it only with your edit tool, even for large changes; "
        "never write the whole file again.\n"
        "4. Do not run `git`; the harness snapshots your work before and after the session.\n"
        "5. Keep dimensions explicit and in meters; follow the coordinate frame stated in the contract.\n"
        "6. When you are done, reply with a SHORT summary of what you changed and what you verified. "
        "Do not ask questions — there is no human in the loop; make a reasonable decision and proceed.\n\n"
        f"{_tool_section(agent_kind, mcp_command, ws)}\n"
        f"## Language contract\n\n{contract_md.strip()}\n"
    )


# --------------------------------------------------------------------------- configs
def _toml_str_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(x) for x in items) + "]"


#: codex asks for per-tool approval before an MCP tool runs; ``codex exec`` has nobody to answer.
CODEX_MCP_APPROVAL_MODE = "approve"


def codex_mcp_overrides(mcp_command: list[str]) -> list[str]:
    """``-c`` pairs that register the 3dcode MCP server for one ``codex exec`` call."""
    return [
        "-c", f"mcp_servers.{MCP_SERVER_NAME}.command={json.dumps(mcp_command[0])}",
        "-c", f"mcp_servers.{MCP_SERVER_NAME}.args={_toml_str_list(mcp_command[1:])}",
        # codex filters the environment of stdio MCP subprocesses; pass the engine/runtime selection through.
        "-c", f"mcp_servers.{MCP_SERVER_NAME}.env_vars={_toml_str_list(['PYTHONPATH', 'C3D_RUNTIME_JS'])}",
        "-c", f"mcp_servers.{MCP_SERVER_NAME}.tool_timeout_sec={MCP_TOOL_TIMEOUT_MS // 1000}",
        "-c", f"mcp_servers.{MCP_SERVER_NAME}.startup_timeout_sec=60",
        "-c", f"mcp_servers.{MCP_SERVER_NAME}.default_tools_approval_mode={json.dumps(CODEX_MCP_APPROVAL_MODE)}",
    ]


# --------------------------------------------------------------------------- entry
def materialize_workspace(
    ws: Workspace,
    *,
    agent_kind: str,
    contract_md: str,
    mcp_command: list[str] | None = None,
) -> None:
    """Write AGENTS.md / CLAUDE.md + the ignore file into ``ws``."""
    mcp_command = list(mcp_command) if mcp_command else default_mcp_command(ws)
    body = _body(agent_kind, contract_md, mcp_command, ws)
    for name in BODY_FILES:
        (ws.root / name).write_text(body)
    # no MCP server is written into the workspace: every CLI gets 3dcode from a harness-owned per-session file
    (ws.root / IGNORE_FILE).write_text("\n".join(IGNORE_LINES) + "\n")
