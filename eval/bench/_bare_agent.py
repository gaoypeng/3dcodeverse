"""The bare-agent arm of ``bench/compare_backends.py``: a vendor coding agent with a shell and nothing else.

The strongest objection to the harness is not "a model asked once does worse" — it is "give the SAME
agent the SAME minutes and a Blender, and it does as well on its own".  This arm answers that: the same
vendor CLI and model the harness drives (``agent:gemini-cli:gemini-3.7-flash``), the one-shot arm's
brief and minimal contract, a shell with the machine's own tools (Blender, node + three + puppeteer,
python + moderngl, glslangValidator) and the harness arm's wall clock.  It gets no plan, no 3dcode MCP
tools, no cookbook, no gates, no judge, no starter library and no deterministic repairs — the agent
decides for itself whether and how to build, render and look.

The delivered file(s) are then scored by the SAME fixed evaluator as every other arm.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from bench._oneshot import files_for
from codeverse3d.agents.registry import get_coding_agent
from codeverse3d.config import get_settings
from codeverse3d.contracts.agent import AgentJob, AgentResult
from codeverse3d.contracts.common import Language
from codeverse3d.contracts.spec import Spec
from codeverse3d.proc import write_text_atomic
from codeverse3d.spatial.node import node_modules_dir
from codeverse3d.workspace import Workspace

GLSLANG = Path.home() / "3dcodeverse_data" / "tools" / "glslang" / "bin" / "glslangValidator"


def _tools_note(language: Language) -> str:
    blender = get_settings().resolve_blender()
    if language in (Language.BLENDER, Language.URDF_BLENDER):
        return (f"Blender is installed: `{blender} --background --factory-startup --python src/model.py` runs your "
                "script headless.  You may write your own throwaway scripts under tmp/ (e.g. to export a GLB, check "
                "bounding boxes, or render PNGs with Eevee/Cycles and then open the PNGs to look at them).")
    if language is Language.GLSL_SHADER:
        return (f"`{GLSLANG}` validates GLSL; python3 has moderngl, numpy and Pillow, so you can write your own "
                "offscreen renderer under tmp/ (prepend the uniforms above, draw a fullscreen quad, save PNGs at a "
                "few times) and open the PNGs to look at them.")
    if language is Language.SCENE_THREEJS:
        return (f"node is installed; `{node_modules_dir()}` contains three (r182) and puppeteer with a cached headless "
                "Chrome (set NODE_PATH to it, or import by absolute path).  You may write your own throwaway "
                "harness under tmp/ that loads src/scene.js in headless Chrome, renders the cameras to PNGs, and "
                "open the PNGs to look at them.")
    return ""


def bare_agent_prompt(spec: Spec, minutes: float) -> str:
    """The one-shot arm's brief + minimal contract, with the output rule replaced by a working session."""
    from bench._oneshot import oneshot_prompt, output_rule

    brief = oneshot_prompt(spec)
    brief = brief[: brief.rfind(output_rule(spec.language))].rstrip()
    brief = brief.replace(" — there is no second chance", "")
    files = ", ".join(f"`{f}`" for f in files_for(spec.language))
    return (brief + "\n\n"
            f"You are working in the current directory.  Write the deliverable to {files} (relative to the current "
            "directory) — only those files are collected and judged; anything else you create is discarded.  The "
            "contract's rules (no render/export/file calls, allowed imports) apply to the deliverable only; your own "
            "throwaway scripts under tmp/ may do anything.  "
            "You have a shell: build, test, render and inspect your work as much as you find useful, and iterate "
            f"until you are satisfied.  " + _tools_note(spec.language) + "  "
            f"Your session is cut off after about {minutes:.0f} minutes; make sure a complete, working version of "
            "the file is on disk early and improve it from there.")


def run_bare_agent(spec: Spec, target: str, cell: Path, eval_ws: Workspace, *, minutes: float) -> AgentResult:
    """Run the session in ``cell/agent`` and copy the deliverable into ``eval_ws``.  Resumable: a finished
    session (``cell/agent/.bare_done.json``) is not re-run."""
    ws = Workspace(cell / "agent")
    done = ws.root / ".bare_done.json"
    if done.is_file():
        result = AgentResult.model_validate_json(done.read_text())
    else:
        if ws.exists():
            shutil.rmtree(ws.root)
        ws.create()
        (ws.root / "tmp").mkdir(exist_ok=True)
        job = AgentJob(workspace=str(ws.root), prompt=bare_agent_prompt(spec, minutes), label="bare",
                       timeout_s=int(minutes * 60), spatial_tools=False, write_roots=["src", "tmp"],
                       hard_deadline_s=time.monotonic() + minutes * 60,
                       kind="baseline", language=spec.language.value, track=spec.track.value,
                       env={"NODE_PATH": str(node_modules_dir())})
        result = get_coding_agent(target).run(job)
        if not result.transient:  # a session a 503 storm killed is not finished: a --redo must run it again
            write_text_atomic(done, result.model_dump_json(indent=1))
    for rel in files_for(spec.language):
        src = ws.root / rel
        if src.is_file():
            dst = eval_ws.root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return result
