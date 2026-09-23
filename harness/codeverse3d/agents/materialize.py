"""Materialise per-workspace agent instructions + MCP configs.

One shared body is written to ``AGENTS.md`` (codex / generic), ``GEMINI.md``
(gemini-cli) and ``CLAUDE.md`` (claude-code): task-agnostic rules, how to call
the ``3dcode`` spatial tools, where the cookbook is, and the language contract.
MCP wiring:

* gemini-cli → the per-session system settings (``agents/backends.write_system_settings``);
  ``ws/.gemini/settings.json`` is agent-writable, so it only carries the ``context`` block
* claude-code → per-session ``trajectories/<label>_rNN/mcp.json`` (``agents/backends.py``)
* codex → ``-c`` overrides (:func:`codex_mcp_overrides`, built per session by its backend)
* agy (Antigravity) → no per-workspace MCP; the body documents the CLI fallback.

Skills: the routed ``SKILL.md`` bundles are written per ROUND, not here — the set depends
on the round's kind and the previous round's gate findings (``codeverse3d/skills``,
``tracks/skills_hook.py``).  ``skills.materialize.write_index`` then edits a marked section
into the three body files this module writes, so there is exactly one shared head and no
new one (docs/COST.md §13 measured and reverted a second head at +2,925 tokens/call).
Nothing here needs to change for the CLIs to see them: codex's per-tool approval override
below is scoped to ``mcp_servers.3dcode.*`` and cannot reach its skills loader, and
gemini-cli's ``activate_skill`` consent is already covered by ``--approval-mode yolo``.
The per-backend wiring lives in ``agents/backends.py``: claude-code needs ``Skill`` in its
``--allowedTools`` (``ALLOWED_TOOLS``), gemini-cli needs ``skills.enabled`` and folder trust
off in its per-session system settings (``SYSTEM_SETTINGS``: 0.53 skips both workspace skill
roots in an untrusted folder), and each backend records what its CLI activated
(``cli_common.record_tool_calls``) — re-read against the installed CLIs 2026-09-22.

Ignore files: ``.geminiignore`` / ``.aiexclude`` hide only noise (:data:`IGNORE_LINES`);
``.gemini/settings.json`` gets ``context.fileFiltering.respectGitIgnore=false`` because the
workspace ``.gitignore`` hides ``artifacts/`` from git and gemini-cli would otherwise refuse
to read the build census there.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from codeverse3d.agents.cli_common import default_mcp_command
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

C3D_DIR = ".3dcode"  # harness-owned read-only docs inside the workspace
MCP_SERVER_NAME = "3dcode"
MCP_TOOL_TIMEOUT_MS = 600_000
#: ``.geminiignore`` / ``.aiexclude`` — gemini-cli's read_file/glob REFUSE ignored paths, so the
#: agent-facing artefacts must stay readable: ``artifacts/*.json`` (the build tool advertises
#: ``census.json`` etc.) and ``artifacts/tool_renders/``.  Only noise / harness-private output is
#: hidden — ``trajectories/`` whole since the prompt reaches every CLI on stdin (it was the
#: long-prompt file's home until 2026-09-22).
IGNORE_LINES = (
    "artifacts/renders/", "artifacts/judge/", "trajectories/",
    "stages/", "rounds/", "_cand/", "_assets/",
    ".git/", "node_modules/", "__pycache__/", ".gemini/tmp/",
)
#: gemini-cli also honours the workspace ``.gitignore`` (``artifacts/``, ``trajectories/``, ``.3dcode/``
#: are git-ignored run state) unless told otherwise — the per-workspace settings turn that off so
#: ``IGNORE_LINES`` is the single source of truth for what the agent may read.
GEMINI_CONTEXT_SETTINGS = {"fileFiltering": {"respectGitIgnore": False, "respectGeminiIgnore": True}}


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
    track = language = ""
    if ws is not None and ws.spec_path.is_file():
        try:
            spec = json.loads(ws.spec_path.read_text())
            track, language = str(spec.get("track", "")), str(spec.get("language", ""))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("could not read %s for tool filtering: %s", ws.spec_path, e)
    tools = list_tools(track=track, language=language)
    names = {t.name for t in tools}
    cards = "\n".join(t.card() for t in tools) or "(the tool registry is empty in this environment)"
    look = [n for n in ("render_views", "render_sheet", "scene_views", "gl_frames") if n in names]
    prove = [n for n in ("measure", "check_contract", "scene_probe", "gl_probe") if n in names]
    if agent_kind == "claude-code":
        how = (f"Tools are exposed by the MCP server `{MCP_SERVER_NAME}`; their names appear as "
               f"`mcp__{MCP_SERVER_NAME}__<name>` (e.g. `mcp__{MCP_SERVER_NAME}__build`).")
    elif agent_kind == "agy":
        how = (
            "This session has no MCP server. Call a tool from the shell instead:\n"
            "`python -m codeverse3d.cli.main tools <name> --json '{\"arg\": \"value\"}'` "
            "(run from the workspace root; prints the observation as text + JSON)."
        )
    elif agent_kind == "gemini-cli":
        how = (
            f"Tools are exposed by the MCP server `{MCP_SERVER_NAME}`; they appear in your tool list as "
            f"`mcp_{MCP_SERVER_NAME}_<name>` (or plain `<name>`). Prefer them over shell commands."
        )
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


def _body(agent_kind: str, contract_md: str, cookbook_note: str, spatial_tools: bool, mcp_command: list[str],
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
    if isinstance(servers, dict) and servers.keys() & {MCP_SERVER_NAME, "3dcv"}:   # "3dcv": the name before 2026-09-21
        servers.pop(MCP_SERVER_NAME, None)
        servers.pop("3dcv", None)
        path.write_text(json.dumps(data, indent=2) + "\n")


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
    cookbook_text: str,
    spatial_tools: bool,
    mcp_command: list[str] | None = None,
) -> None:
    """Write AGENTS.md / GEMINI.md / CLAUDE.md + MCP configs + ignore files into ``ws``.

    ``mcp_command`` defaults to ``cli_common.default_mcp_command(ws)`` — the same
    ``sys.executable`` the backends launch; three callers used to spell a bare ``python``
    here, which the body text and codex's ``-c`` overrides then quoted verbatim.

    Returns nothing: the ``Materialized`` DTO this used to build (body/ignore paths, the
    cookbook path, the argv, a warnings list) was discarded by every production caller —
    ``tracks/common.Services.materialize`` is typed ``-> None`` — so its one real signal,
    a cookbook that did not resolve, was written and read by nobody.  That is the exact
    failure ``tracks/common.cookbook_rel_for`` was fixed for on 2026-08-29 (an articulated
    run told the agent "No cookbook is available" while its 24 kB cookbook sat on disk);
    it is a log line now, where someone reading the run can see it (2026-08-30).

    ``cookbook_text`` is what the catalog loaded (``RunContext.cookbook_text``): until
    2026-09-22 this took a path and resolved it itself, workspace first — so a
    ``<ws>/blender/cookbook.md`` the agent wrote shadowed the harness cookbook."""
    mcp_command = list(mcp_command) if mcp_command else default_mcp_command(ws)

    # cookbook: copy into the harness-owned .3dcode/ dir so every CLI can read it in-workspace
    if cookbook_text:
        dest = ws.root / C3D_DIR / "cookbook.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(cookbook_text, encoding="utf-8")
        cookbook_note = (
            f"The cookbook for this language — copyable, verified snippets and skeletons — is at "
            f"`{C3D_DIR}/cookbook.md` (relative to the workspace root). Read the relevant sections "
            "before writing code and copy its patterns exactly."
        )
    else:
        log.warning("cookbook not found — the %s session gets the contract only", agent_kind)
        cookbook_note = "No cookbook is available in this session; rely on the contract below."

    body = _body(agent_kind, contract_md, cookbook_note, spatial_tools, mcp_command, ws)
    for name in ("AGENTS.md", "GEMINI.md", "CLAUDE.md"):
        (ws.root / name).write_text(body)

    # No MCP server is written into the workspace: every CLI gets 3dcode from a harness-owned
    # per-session file, so an agent-planted server cannot reach the next round (audit 2026-08-27).
    gemini_settings = ws.root / ".gemini" / "settings.json"
    _merge_json(gemini_settings, {"context": dict(GEMINI_CONTEXT_SETTINGS)})
    _drop_server(gemini_settings)

    ignore_text = "\n".join(IGNORE_LINES) + "\n"
    for name in (".geminiignore", ".aiexclude"):
        (ws.root / name).write_text(ignore_text)
