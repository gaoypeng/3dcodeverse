"""Materialise per-workspace agent instructions + ignore file.

One shared body is written to ``AGENTS.md`` (codex / agy) and ``CLAUDE.md`` (claude-code):
task-agnostic rules, how to call the ``3dcode`` spatial tools, and the language contract.
(agy reads ``GEMINI.md`` as well as ``AGENTS.md``; a third copy would be
loaded twice.)  MCP wiring — never a file in the workspace, which is agent-writable:

* claude-code → per-session ``trajectories/<label>_rNN/mcp.json`` (``agents/backends.py``)
* codex → ``-c`` overrides (:func:`codex_mcp_overrides`, built per session by its backend)
* agy → the ``mcp_config.json`` of the session's own HOME (``AntigravityAgent.build_home``)

Skills: the routed ``SKILL.md`` bundles are written per ROUND, not here — the set depends
on the round's kind and the previous round's gate findings (``codeverse3d/skills``,
``tracks/skills_hook.py``).  ``skills.materialize.write_index`` then edits a marked section
into the body files this module writes, so there is exactly one shared head and no
new one (docs/COST.md §13 measured and reverted a second head at +2,925 tokens/call).
Nothing here needs to change for the CLIs to see them: codex's per-tool approval override
below is scoped to ``mcp_servers.3dcode.*`` and cannot reach its skills loader.
claude-code needs ``Skill`` in its ``--allowedTools`` (``backends.ALLOWED_TOOLS``), and each
backend records what its CLI activated (``cli_common.record_tool_calls``).

Ignore file: ``.antigravityignore`` hides only noise (:data:`IGNORE_LINES`).
"""

from __future__ import annotations

import json
import logging

from codeverse3d.agents.cli_common import default_mcp_command
from codeverse3d.proc import read_json_or_none
from codeverse3d.skills.materialize import BODY_FILES
from codeverse3d.workspace import C3D_DIR, Workspace

log = logging.getLogger(__name__)

#: a run made BEFORE D107 was handed its language's cookbook here (and a "## Cookbook" section in
#: the body files).  Materialising removes it, and its presence makes a resumed run re-materialise
#: (``needs_rematerialize``) so the agent of a later round never reads a cookbook.
LEGACY_COOKBOOK = f"{C3D_DIR}/cookbook.md"
MCP_SERVER_NAME = "3dcode"
MCP_TOOL_TIMEOUT_MS = 600_000
#: ``.antigravityignore`` — agy's own ignore file.  The agent-facing artefacts must stay readable:
#: ``artifacts/*.json`` (the build tool advertises ``census.json`` etc.) and
#: ``artifacts/tool_renders/``.  Only noise / harness-private output is hidden — ``trajectories/``
#: whole since the prompt reaches every CLI on stdin.
IGNORE_FILE = ".antigravityignore"
IGNORE_LINES = (
    "artifacts/renders/", "artifacts/judge/", "trajectories/",
    "stages/", "rounds/", "_cand/", "_assets/",
    ".git/", "node_modules/", "__pycache__/", ".gemini/tmp/",
)


# --------------------------------------------------------------------------- body
def _tool_section(agent_kind: str, spatial_tools: bool, mcp_command: list[str], ws: Workspace | None = None) -> str:
    if not spatial_tools:
        return (
            "## Spatial tools\n\n"
            "No spatial tools are available in this session. Verify your work by reading the "
            "code carefully and keeping every dimension explicit; the harness builds, measures "
            "and renders after you finish.\n"
        )
    from codeverse3d.spatial.registry import list_tools

    # the MCP server's track/language filter (mcp_server.build_context): this file is
    # agy's ONLY tool documentation, and the whole registry taught dead-end object tools
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


def _body(agent_kind: str, contract_md: str, spatial_tools: bool, mcp_command: list[str],
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
        "3. Do not run `git`; the harness snapshots your work before and after the session.\n"
        "4. Keep dimensions explicit and in meters; follow the coordinate frame stated in the contract.\n"
        "5. When you are done, reply with a SHORT summary of what you changed and what you verified. "
        "Do not ask questions — there is no human in the loop; make a reasonable decision and proceed.\n\n"
        f"{_tool_section(agent_kind, spatial_tools, mcp_command, ws)}\n"
        f"## Language contract\n\n{contract_md.strip()}\n"
    )


# --------------------------------------------------------------------------- configs
def _toml_str_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(x) for x in items) + "]"


#: codex ≥ 0.14x asks for per-tool approval before running an MCP tool; ``codex exec`` has
#: nobody to answer, so without this every 3dcode call is auto-cancelled ("requires approval").
CODEX_MCP_APPROVAL_MODE = "approve"


def codex_mcp_overrides(mcp_command: list[str]) -> list[str]:
    """``-c`` pairs that register the 3dcode MCP server for one ``codex exec`` call."""
    return [
        "-c", f"mcp_servers.{MCP_SERVER_NAME}.command={json.dumps(mcp_command[0])}",
        "-c", f"mcp_servers.{MCP_SERVER_NAME}.args={_toml_str_list(mcp_command[1:])}",
        # Codex filters the environment of stdio MCP subprocesses. Preserve the
        # caller's explicit engine/runtime selection (e.g. a frozen benchmark
        # checkout) instead of silently importing an unrelated editable install.
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
    spatial_tools: bool,
    mcp_command: list[str] | None = None,
) -> None:
    """Write AGENTS.md / CLAUDE.md + the ignore file into ``ws``.

    ``mcp_command`` defaults to ``cli_common.default_mcp_command(ws)`` — the same
    ``sys.executable`` the backends launch; three callers used to spell a bare ``python``
    here, which the body text and codex's ``-c`` overrides then quoted verbatim.

    Returns nothing: the ``Materialized`` DTO this used to build (body/ignore paths, the
    argv, a warnings list) was discarded by every production caller —
    ``tracks/common.Services.materialize`` is typed ``-> None``.

    The body is the hard rules, the tool cards and the language contract — nothing else is
    written for the agent to read (D107, owner 2026-10-15: no language ships a cookbook; the
    interface facts are in the contract, the craft in the routed skills)."""
    mcp_command = list(mcp_command) if mcp_command else default_mcp_command(ws)
    body = _body(agent_kind, contract_md, spatial_tools, mcp_command, ws)
    for name in BODY_FILES:
        (ws.root / name).write_text(body)
    (ws.root / LEGACY_COOKBOOK).unlink(missing_ok=True)
    # No MCP server is written into the workspace: every CLI gets 3dcode from a harness-owned
    # per-session file, so an agent-planted server cannot reach the next round (audit 2026-08-27).
    (ws.root / IGNORE_FILE).write_text("\n".join(IGNORE_LINES) + "\n")


def needs_rematerialize(ws: Workspace) -> bool:
    """A workspace materialised before D107 (it still holds :data:`LEGACY_COOKBOOK`) — its body
    files carry the old "## Cookbook" section, so a resumed run writes them again."""
    return (ws.root / LEGACY_COOKBOOK).is_file()
