"""One-shot generation backends for ``bench/compare_backends.py``.

A one-shot arm gets ONE raw generation with no harness help: the model sees the
prompt plus the *minimal* blender contract (file, frame, units, naming, no
export/camera/lights) and must answer with the complete ``src/model.py``.  No
tools, no cookbook, no plan, no repair loop — the harness only builds, renders
and judges whatever comes back.

Backends (``get_oneshot_backend``):

* ``claude-code`` / ``claude-code:<model>`` — ``claude -p`` with ``--tools ""``
  and ``--max-turns 1`` (subscription; model default when none given).
* ``codex`` / ``codex:<model>[@<effort>]`` — ``codex exec`` read-only sandbox, JSONL
  events, ``-c model_reasoning_effort=`` (default ``high``, see
  ``Settings.agents.codex_reasoning_effort``) (subscription; model default when none given).
* ``gemini:<m>`` / ``anthropic:<m>`` / ``openai:<m>`` — ``codeverse.models``
  chat model, one ``ChatRequest``.

Every call runs in a scratch directory *outside* the repository so the CLIs
cannot pick up CLAUDE.md / AGENTS.md context, and with secrets stripped from the
environment (``hardened_env``-style), keeping only the CLI's own auth variable.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from codeverse.agents.claude_code import parse_claude_json, usage_from_envelope
from codeverse.agents.cli_common import is_secret_env, tail
from codeverse.agents.codex import effort_overrides, parse_codex_jsonl, split_model_effort
from codeverse.agents.watchdog import run_with_watchdog
from codeverse.config import get_settings
from codeverse.contracts.artifacts import BuildResult, GateReport
from codeverse.contracts.chat import ChatMessage, ChatRequest
from codeverse.contracts.common import Usage
from codeverse.contracts.spec import Spec
from codeverse.conventions import LANGUAGE_FRAME, frame_doc
from codeverse.tracks.generation import MultiFileParseError, parse_multifile
from codeverse.tracks.repair import format_error_report
from codeverse.workspace import Workspace

MODEL_FILE = "src/model.py"
DEFAULT_TIMEOUT_S = 900.0
IDLE_GRACE_S = 600.0

# ----------------------------------------------------------------------------- contract
#: The minimal contract: frame/units from conventions (never restated), file, naming, no export.
_CONTRACT_BODY = """You write ONE file, `{model_file}`, in raw Blender Python (bpy, Blender 5.x API).
The harness runs it headless (`blender -b --factory-startup --python <wrapper> -- --script {model_file}`)
inside an EMPTIED scene (no default cube / camera / light) and then exports the result itself.

Frame, units and placement: {frame}  Build at real-world size.

Naming: one mesh object per part, `obj.name` set to a unique PascalCase part name (e.g. `Seat`,
`LegFrontLeft`); no auto-suffixed duplicates like `Leg.001`.  Link every object to
`bpy.context.scene.collection`.  Every visible mesh gets a material (Principled BSDF base colour /
roughness / metallic).

Do NOT: create cameras or lights, touch render/world settings, call `bpy.ops.render.*`,
`bpy.ops.export_*`, `bpy.ops.import_*`, `bpy.ops.wm.*`, read or write files, use the network, or
import anything other than `bpy`, `bmesh`, `mathutils`, `math`, `random`.  Keep the total under
500k triangles and finish in under 120 s.

The script must build the whole object when executed top-to-bottom (call your `main()` at module
level).  Make sure the code runs without errors in headless Blender — there is no second chance."""

_OUTPUT_RULE = (f"You have NO tools in this session: you cannot write files, run Blender or inspect anything — "
                f"the code must appear in your reply.  Reply with the COMPLETE contents of `{MODEL_FILE}` as ONE "
                f"```python fenced code block and nothing else (no prose before or after, no partial snippets).")


def minimal_contract() -> str:
    """Frame/units come from ``codeverse.conventions`` (the single source of truth)."""
    return _CONTRACT_BODY.format(model_file=MODEL_FILE, frame=frame_doc(LANGUAGE_FRAME["blender"]))


def oneshot_prompt(spec: Spec) -> str:
    """The entire input of a one-shot arm: brief + constraints + minimal contract + output rule."""
    c = spec.constraints
    lines = [f"Model this object in raw bpy: {spec.prompt.strip()}"]
    if c.dimensions_m:
        lines.append("Dimensions (m): " + ", ".join(f"{k}={v:g}" for k, v in c.dimensions_m.items()))
    if c.style:
        lines.append(f"Style: {c.style}")
    lines.extend(f"MUST HAVE: {m}" for m in c.must_have)
    lines.extend(f"MUST NOT: {m}" for m in c.must_not)
    return "\n".join(lines) + "\n\n" + minimal_contract() + "\n\n" + _OUTPUT_RULE


def repair_prompt(spec: Spec, previous_code: str, build: BuildResult, lint: GateReport, attempt: int) -> str:
    """Error-feedback retry (``oneshot+repair`` arm only): previous file + the build error report."""
    report = format_error_report(build, lint)
    return (oneshot_prompt(spec)
            + f"\n\nYour previous attempt ({attempt}) did NOT build.  Fix the error below (smallest correct change, "
              f"keep every part) and return the COMPLETE corrected file.\n\nERROR REPORT:\n{report}\n\n"
              f"PREVIOUS `{MODEL_FILE}`:\n```python\n{previous_code[:40_000]}\n```")


# ----------------------------------------------------------------------------- results
class OneShotResult(BaseModel):
    ok: bool = Field(description="a non-empty answer came back (parsing happens later)")
    text: str = ""
    usage: Usage = Field(default_factory=Usage)
    tool_calls: int = Field(default=0, description="tool/command calls the CLI reported (should be 0)")
    notes: str = ""
    duration_s: float = 0.0
    transcript_dir: str = ""


class OneShotBackend(Protocol):
    id: str

    def generate(self, prompt: str, *, out_dir: Path, timeout_s: float = DEFAULT_TIMEOUT_S,
                 label: str = "oneshot") -> OneShotResult: ...


_TOOL_XML_CONTENT = re.compile(r'<parameter name="content">\n?(?P<body>.*?)</parameter>', re.S)


def _strip_hallucinated_tool_xml(text: str) -> str:
    """A tool-less CLI model sometimes "writes the file" as XML tool-call text; keep the largest file body."""
    bodies = [m.group("body") for m in _TOOL_XML_CONTENT.finditer(text)]
    if not bodies:
        return text
    body = max(bodies, key=len)
    return body if "import bpy" in body else text


def extract_model_file(text: str) -> str:
    """The python file from a one-shot answer (tolerant: fenced block / bare code / FILE envelope /
    hallucinated Write-tool XML)."""
    files = parse_multifile(_strip_hallucinated_tool_xml(text), expected_files=[MODEL_FILE])
    code = files.get(MODEL_FILE) or next(iter(files.values()), "")
    if not code.strip():
        raise MultiFileParseError("empty model file in the answer")
    return code if code.endswith("\n") else code + "\n"


def write_model_file(ws: Workspace, text: str) -> Path:
    """Parse the answer and write ``src/model.py`` into the workspace (raises MultiFileParseError)."""
    code = extract_model_file(text)
    dest = ws.root / MODEL_FILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(code)
    return dest


# ----------------------------------------------------------------------------- common
def _scratch_cwd(label: str) -> Path:
    """A fresh directory outside any repo: no CLAUDE.md/AGENTS.md/.git context can leak in."""
    return Path(tempfile.mkdtemp(prefix=f"cv3d_oneshot_{label}_"))


def _clean_env(keep: set[str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in keep or not is_secret_env(k)}
    env["CV3D_AGENT_CONTEXT"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _dump(out_dir: Path, prompt: str, argv: list[str], stdout: str, stderr: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prompt.md").write_text(prompt)
    (out_dir / "argv.json").write_text(json.dumps([a if a != prompt else f"<prompt {len(prompt)} chars>" for a in argv]))
    (out_dir / "stdout.txt").write_text(stdout)
    (out_dir / "stderr.txt").write_text(stderr)


# ----------------------------------------------------------------------------- claude -p
class ClaudeOneShot:
    """``claude -p`` with every tool disabled and a single turn."""

    kind = "claude-code"

    def __init__(self, model: str = "", binary: str | None = None):
        self.model = model
        self.binary = binary or get_settings().binaries.claude_cli
        self.id = f"oneshot:{self.kind}" + (f":{model}" if model else "")

    def argv(self, prompt: str) -> list[str]:
        argv = [self.binary, "-p", prompt, "--output-format", "json", "--tools", "", "--max-turns", "1",
                "--no-session-persistence", "--strict-mcp-config"]
        if self.model:
            argv += ["--model", self.model]
        return argv

    def generate(self, prompt: str, *, out_dir: Path, timeout_s: float = DEFAULT_TIMEOUT_S, label: str = "oneshot") -> OneShotResult:
        cwd = _scratch_cwd("claude")
        argv = self.argv(prompt)
        proc = run_with_watchdog(argv, cwd=cwd, env=_clean_env({"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}),
                                 soft_timeout_s=timeout_s, idle_grace_s=IDLE_GRACE_S, activity_dirs=[cwd])
        _dump(out_dir, prompt, argv, proc.stdout, proc.stderr)
        env = parse_claude_json(proc.stdout)
        usage = usage_from_envelope(env, self.model or "default") if env else Usage(backend=self.kind, model=self.model)
        usage.latency_ms = usage.latency_ms or int(proc.duration_s * 1000)
        text = str((env or {}).get("result") or "")
        notes = ""
        ok = bool(text.strip()) and not proc.timed_out
        if proc.timed_out:
            notes = f"timeout after {proc.duration_s:.0f}s ({proc.killed_reason})"
        elif env is None or proc.rc != 0:
            notes = f"rc={proc.rc}; no result envelope; stderr: {tail(proc.stderr, 800)}"
            ok = False
        elif env.get("is_error"):
            notes = f"claude is_error subtype={env.get('subtype', '')}: {tail(text, 400)}"
            ok = False
        (out_dir / "response.md").write_text(text)
        return OneShotResult(ok=ok, text=text, usage=usage, tool_calls=usage.tool_calls, notes=notes,
                             duration_s=round(proc.duration_s, 2), transcript_dir=str(out_dir))


# ----------------------------------------------------------------------------- codex exec
def codex_default_model() -> str:
    """The model ``codex`` will use when none is passed (``~/.codex/config.toml``), for labelling/pricing."""
    try:
        from codeverse._compat import tomllib

        cfg = tomllib.loads((Path.home() / ".codex" / "config.toml").read_text())
        return str(cfg.get("model") or "")
    except Exception:  # noqa: BLE001 — label only
        return ""


class CodexOneShot:
    """``codex exec`` in a read-only sandbox on an empty scratch dir (no files to read, nothing to run)."""

    kind = "codex"

    def __init__(self, model: str = "", binary: str | None = None, reasoning_effort: str | None = None):
        self.model, self.reasoning_effort = split_model_effort(model, reasoning_effort)
        self.binary = binary or get_settings().binaries.codex_cli
        self.id = f"oneshot:{self.kind}" + (f":{self.model}" if self.model else "")

    def argv(self, cwd: Path, last_msg: Path) -> list[str]:
        argv = [self.binary, "exec", "--json", "-C", str(cwd), "--sandbox", "read-only", "--skip-git-repo-check",
                "--ephemeral", "--color", "never", "-o", str(last_msg)]
        if self.model:
            argv += ["--model", self.model]
        argv += effort_overrides(self.reasoning_effort)
        argv.append("-")  # prompt on stdin
        return argv

    def generate(self, prompt: str, *, out_dir: Path, timeout_s: float = DEFAULT_TIMEOUT_S, label: str = "oneshot") -> OneShotResult:
        cwd = _scratch_cwd("codex")
        out_dir.mkdir(parents=True, exist_ok=True)
        last_msg = out_dir / "last_message.md"
        argv = self.argv(cwd, last_msg)
        proc = run_with_watchdog(argv, cwd=cwd, env=_clean_env({"OPENAI_API_KEY", "CODEX_API_KEY"}), soft_timeout_s=timeout_s,
                                 idle_grace_s=IDLE_GRACE_S, stdin=prompt, activity_dirs=[cwd])
        _dump(out_dir, prompt, argv, proc.stdout, proc.stderr)
        events = parse_codex_jsonl(proc.stdout)
        text = last_msg.read_text() if last_msg.is_file() else "\n\n".join(m for m in events.messages if m.strip())
        usage = events.usage(self.model or codex_default_model() or "default")
        usage.latency_ms = int(proc.duration_s * 1000)
        notes = "; ".join(events.errors[:3])
        ok = bool(text.strip()) and not proc.timed_out
        if proc.timed_out:
            notes = f"timeout after {proc.duration_s:.0f}s ({proc.killed_reason})"
        elif proc.rc != 0 or events.n_events == 0:
            notes = f"rc={proc.rc}; events={events.n_events}; stderr: {tail(proc.stderr, 800)}"
            ok = ok and events.n_events > 0
        (out_dir / "response.md").write_text(text)
        return OneShotResult(ok=ok, text=text, usage=usage, tool_calls=events.tool_calls, notes=notes,
                             duration_s=round(proc.duration_s, 2), transcript_dir=str(out_dir))


# ----------------------------------------------------------------------------- API chat model
class ApiOneShot:
    """One ``ChatRequest`` through ``codeverse.models`` (``gemini:*`` / ``anthropic:*`` / ``openai:*``)."""

    kind = "api"

    def __init__(self, model_id: str, *, chat_model: Any | None = None, temperature: float = 0.5,
                 thinking: str = "medium", max_output_tokens: int = 32000):
        self.model_id = model_id
        self.id = f"oneshot:{model_id}"
        self._model = chat_model
        self.temperature = temperature
        self.thinking = thinking
        self.max_output_tokens = max_output_tokens

    @property
    def model(self) -> Any:
        if self._model is None:
            from codeverse.models import get_chat_model

            self._model = get_chat_model(self.model_id)
        return self._model

    def generate(self, prompt: str, *, out_dir: Path, timeout_s: float = DEFAULT_TIMEOUT_S, label: str = "oneshot") -> OneShotResult:
        t0 = time.time()
        req = ChatRequest(messages=[ChatMessage.user(prompt)], system="You are an expert Blender (bpy) modeller writing raw code.",
                          temperature=self.temperature, thinking=self.thinking,  # type: ignore[arg-type]
                          max_output_tokens=self.max_output_tokens, label=label)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "prompt.md").write_text(prompt)
        try:
            resp = self.model.generate(req)
        except Exception as e:  # noqa: BLE001 — a model outage is a recorded failure, not a crash
            return OneShotResult(ok=False, notes=f"{type(e).__name__}: {e}", duration_s=round(time.time() - t0, 2),
                                 transcript_dir=str(out_dir))
        text = resp.text or ""
        (out_dir / "response.md").write_text(text)
        return OneShotResult(ok=bool(text.strip()), text=text, usage=resp.usage, duration_s=round(time.time() - t0, 2),
                             transcript_dir=str(out_dir))


# ----------------------------------------------------------------------------- registry
def get_oneshot_backend(target: str) -> OneShotBackend:
    """``claude-code[:model]`` · ``codex[:model]`` · ``<provider>:<model>`` (API)."""
    kind, _, model = target.partition(":")
    if kind == "claude-code":
        return ClaudeOneShot(model)
    if kind == "codex":
        return CodexOneShot(model)
    if kind in ("gemini", "anthropic", "openai") and model:
        return ApiOneShot(target)
    raise ValueError(f"unknown one-shot target {target!r}; expected claude-code[:m], codex[:m] or <gemini|anthropic|openai>:<model>")


__all__ = ["ApiOneShot", "ClaudeOneShot", "CodexOneShot", "MODEL_FILE", "OneShotBackend", "OneShotResult",
           "extract_model_file", "get_oneshot_backend", "minimal_contract", "oneshot_prompt", "repair_prompt",
           "write_model_file"]
