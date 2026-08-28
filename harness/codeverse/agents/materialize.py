"""Materialise per-workspace agent instructions + MCP configs.

One shared body is written to ``AGENTS.md`` (codex / generic), ``GEMINI.md``
(gemini-cli) and ``CLAUDE.md`` (claude-code): task-agnostic rules, how to call
the ``3dcv`` spatial tools, where the cookbook is, and the language contract.
MCP wiring:

* gemini-cli → the per-session system settings (``agents/gemini_cli.write_system_settings``);
  ``ws/.gemini/settings.json`` is agent-writable, so it only carries the ``context`` block
* claude-code → per-session ``trajectories/<label>_rNN/mcp.json`` (``agents/claude_code.py``)
* codex → ``-c`` overrides returned in :class:`Materialized.codex_overrides`
* agy (Antigravity) → no per-workspace MCP; the body documents the CLI fallback.

Skills: the routed ``SKILL.md`` bundles are written per ROUND, not here — the set depends
on the round's kind and the previous round's gate findings (``codeverse/skills``,
``tracks/skills_hook.py``).  ``skills.materialize.write_index`` then edits a marked section
into the three body files this module writes, so there is exactly one shared head and no
new one (docs/COST.md §13 measured and reverted a second head at +2,925 tokens/call).
Nothing here needs to change for the CLIs to see them: codex's per-tool approval override
below is scoped to ``mcp_servers.3dcv.*`` and cannot reach its skills loader, and
gemini-cli's ``activate_skill`` consent is already covered by ``--approval-mode yolo``
(``agents/gemini_cli.py``).  claude-code needed one change — ``Skill`` in its
``--allowedTools``, see ``agents/claude_code.py``.

Ignore files: ``.geminiignore`` / ``.aiexclude`` hide only noise (:data:`IGNORE_LINES`);
``.gemini/settings.json`` gets ``context.fileFiltering.respectGitIgnore=false`` because the
workspace ``.gitignore`` hides ``artifacts/`` + ``trajectories/`` from git and gemini-cli
would otherwise refuse to read the build census / the long-prompt file there.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from codeverse.prompts import PROMPTS_DIR
from codeverse.workspace import Workspace

CV3D_DIR = ".3dcv"  # harness-owned read-only docs inside the workspace
MCP_SERVER_NAME = "3dcv"
MCP_TOOL_TIMEOUT_MS = 600_000
#: ``.geminiignore`` / ``.aiexclude`` — gemini-cli's read_file/glob REFUSE ignored paths, so the
#: agent-facing artefacts must stay readable: ``artifacts/*.json`` (the build tool advertises
#: ``census.json`` etc.), ``artifacts/tool_renders/`` and ``trajectories/<label>_rNN/task_prompt.md``
#: (the long-prompt fallback).  Only noise / harness-private output is hidden.
IGNORE_LINES = (
    "artifacts/renders/", "artifacts/judge/",
    "trajectories/*/stdout*", "trajectories/*/stderr*", "trajectories/*/transcript.jsonl",
    "stages/", "rounds/", "_cand/", "_assets/",
    ".git/", "node_modules/", "__pycache__/", ".gemini/tmp/",
)
#: gemini-cli also honours the workspace ``.gitignore`` (``artifacts/``, ``trajectories/``, ``.3dcv/``
#: are git-ignored run state) unless told otherwise — the per-workspace settings turn that off so
#: ``IGNORE_LINES`` is the single source of truth for what the agent may read.
GEMINI_CONTEXT_SETTINGS = {"fileFiltering": {"respectGitIgnore": False, "respectGeminiIgnore": True}}


class Materialized(BaseModel):
    """What :func:`materialize_workspace` wrote and how each CLI reaches the tools."""

    body_files: list[str] = Field(default_factory=list)
    ignore_files: list[str] = Field(default_factory=list)
    cookbook_path: str = ""
    mcp_command: list[str] = Field(default_factory=list)
    codex_overrides: list[str] = Field(default_factory=list, description="extra argv for `codex exec` (-c k=v pairs)")
    agy_mcp: str = Field(default="", description="how Antigravity reaches the tools")
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- body
def _tool_section(agent_kind: str, spatial_tools: bool, mcp_command: list[str]) -> str:
    if not spatial_tools:
        return (
            "## Spatial tools\n\n"
            "No spatial tools are available in this session. Verify your work by reading the "
            "code carefully and keeping every dimension explicit; the harness builds, measures "
            "and renders after you finish.\n"
        )
    from codeverse.spatial.registry import tool_cards

    cards = tool_cards() or "(the tool registry is empty in this environment)"
    if agent_kind == "claude-code":
        how = "Tools are exposed by the MCP server `3dcv`; their names appear as `mcp__c3v__<name>` (e.g. `mcp__c3v__build`)."
    elif agent_kind == "agy":
        how = (
            "This session has no MCP server. Call a tool from the shell instead:\n"
            "`python -m codeverse.cli.main tools <name> --json '{\"arg\": \"value\"}'` "
            "(run from the workspace root; prints the observation as text + JSON)."
        )
    elif agent_kind == "gemini-cli":
        how = (
            f"Tools are exposed by the MCP server `{MCP_SERVER_NAME}`; they appear in your tool list as "
            f"`mcp_{MCP_SERVER_NAME}_<name>` (or plain `<name>`). Prefer them over shell commands."
        )
    else:
        how = f"Tools are exposed by the MCP server `{MCP_SERVER_NAME}` (command: `{' '.join(mcp_command)}`); call them by name."
    return (
        "## Spatial tools (3dcv)\n\n"
        f"{how}\n\n"
        "Workflow: edit → `build` → read the errors/numbers → fix → `build` again. Use `render_views` / "
        "`render_sheet` to LOOK at what you made before declaring it finished; use `measure` / "
        "`check_contract` to prove dimensions. Never finish on a failing build.\n\n"
        f"{cards}\n"
    )


def _body(agent_kind: str, contract_md: str, cookbook_note: str, spatial_tools: bool, mcp_command: list[str]) -> str:
    return (
        "# 3dcv workspace — rules for the coding agent\n\n"
        "You are working inside a harness-managed workspace. Read this whole file before acting.\n\n"
        "## Hard rules\n\n"
        "1. You may create or edit files ONLY under `src/` and `public/`. Never modify `artifacts/`, "
        "`trajectories/`, `.git/`, `.gemini/`, `.3dcv/`, or this file — they are harness-owned.\n"
        "2. Write RAW code in the language named by the contract below. Never import from `codeverse` or "
        "any helper SDK; do not install packages; do not use the network; do not write "
        "camera / render / export / file-output code unless the contract explicitly asks for it — "
        "the harness owns export and rendering.\n"
        "3. Do not run `git`; the harness snapshots your work before and after the session.\n"
        "4. Keep dimensions explicit and in meters; follow the coordinate frame stated in the contract.\n"
        "5. When you are done, reply with a SHORT summary of what you changed and what you verified. "
        "Do not ask questions — there is no human in the loop; make a reasonable decision and proceed.\n\n"
        f"{_tool_section(agent_kind, spatial_tools, mcp_command)}\n"
        f"## Cookbook\n\n{cookbook_note}\n\n"
        f"## Language contract\n\n{contract_md.strip()}\n"
    )


# --------------------------------------------------------------------------- configs
def _merge_json(path: Path, patch: dict) -> None:
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text() or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"{path} is not valid JSON; refusing to merge: {e}") from e
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(data.get(k), dict):
            data[k].update(v)
        else:
            data[k] = v
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _drop_server(path: Path) -> None:
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text() or "{}")
    except json.JSONDecodeError:
        return
    servers = data.get("mcpServers")
    if isinstance(servers, dict) and MCP_SERVER_NAME in servers:
        servers.pop(MCP_SERVER_NAME)
        path.write_text(json.dumps(data, indent=2) + "\n")


def _toml_str_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(x) for x in items) + "]"


#: codex ≥ 0.14x asks for per-tool approval before running an MCP tool; ``codex exec`` has
#: nobody to answer, so without this every 3dcv call is auto-cancelled ("requires approval").
CODEX_MCP_APPROVAL_MODE = "approve"


def codex_mcp_overrides(mcp_command: list[str]) -> list[str]:
    """``-c`` pairs that register the 3dcv MCP server for one ``codex exec`` call."""
    return [
        "-c", f"mcp_servers.{MCP_SERVER_NAME}.command={json.dumps(mcp_command[0])}",
        "-c", f"mcp_servers.{MCP_SERVER_NAME}.args={_toml_str_list(mcp_command[1:])}",
        "-c", f"mcp_servers.{MCP_SERVER_NAME}.tool_timeout_sec={MCP_TOOL_TIMEOUT_MS // 1000}",
        "-c", f"mcp_servers.{MCP_SERVER_NAME}.startup_timeout_sec=60",
        "-c", f"mcp_servers.{MCP_SERVER_NAME}.default_tools_approval_mode={json.dumps(CODEX_MCP_APPROVAL_MODE)}",
    ]


def _resolve_cookbook(ws: Workspace, cookbook_rel: str) -> Path | None:
    if not cookbook_rel:
        return None
    for cand in (ws.root / cookbook_rel, PROMPTS_DIR / cookbook_rel, Path(cookbook_rel)):
        if cand.is_file():
            return cand
    return None


# --------------------------------------------------------------------------- entry
def materialize_workspace(
    ws: Workspace,
    *,
    agent_kind: str,
    contract_md: str,
    cookbook_rel: str,
    spatial_tools: bool,
    mcp_command: list[str],
) -> Materialized:
    """Write AGENTS.md / GEMINI.md / CLAUDE.md + MCP configs + ignore files into ``ws``."""
    out = Materialized(mcp_command=list(mcp_command))
    if spatial_tools and not mcp_command:
        raise ValueError("spatial_tools=True requires a non-empty mcp_command")

    # cookbook: copy into the harness-owned .3dcv/ dir so every CLI can read it in-workspace
    src = _resolve_cookbook(ws, cookbook_rel)
    if src is not None:
        dest = ws.root / CV3D_DIR / "cookbook.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        out.cookbook_path = str(dest)
        cookbook_note = (
            f"The cookbook for this language — copyable, verified snippets and skeletons — is at "
            f"`{CV3D_DIR}/cookbook.md` (relative to the workspace root). Read the relevant sections "
            "before writing code and copy its patterns exactly."
            + (" You may also call the `read_cookbook` tool." if spatial_tools else "")
        )
    else:
        out.warnings.append(f"cookbook not found: {cookbook_rel!r}")
        cookbook_note = "No cookbook is available in this session; rely on the contract below."

    body = _body(agent_kind, contract_md, cookbook_note, spatial_tools, list(mcp_command))
    for name in ("AGENTS.md", "GEMINI.md", "CLAUDE.md"):
        (ws.root / name).write_text(body)
        out.body_files.append(str(ws.root / name))

    # No MCP server is written into the workspace: every CLI gets 3dcv from a harness-owned
    # per-session file, so an agent-planted server cannot reach the next round (audit 2026-08-27).
    gemini_settings = ws.root / ".gemini" / "settings.json"
    _merge_json(gemini_settings, {"context": dict(GEMINI_CONTEXT_SETTINGS)})
    _drop_server(gemini_settings)
    if spatial_tools:
        out.codex_overrides = codex_mcp_overrides(list(mcp_command))
        out.agy_mcp = (
            "Antigravity CLI only supports GLOBAL MCP registration (`agy mcp add ...`), which would "
            "leak across parallel runs; no per-workspace MCP is written. The body documents the "
            "`python -m codeverse.cli.main tools <name> --json ...` shell fallback instead."
        )
    else:
        out.agy_mcp = "spatial tools disabled"

    ignore_text = "\n".join(IGNORE_LINES) + "\n"
    for name in (".geminiignore", ".aiexclude"):
        (ws.root / name).write_text(ignore_text)
        out.ignore_files.append(str(ws.root / name))
    return out
