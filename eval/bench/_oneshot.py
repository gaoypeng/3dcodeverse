"""One-shot generation backends for ``bench/compare_backends.py``.

A one-shot arm gets ONE raw generation with no harness help: the model sees the
prompt plus the *minimal* contract of its language (file, frame, units, naming, no
export/camera/lights) and must answer with the complete entry file — ``src/model.py``
(blender), ``src/model.py`` + ``src/robot.urdf`` (articulated), ``src/shader.frag``
(graphics, 2026-09-07), ``src/scene.js`` (scene, 2026-09-07).  No tools, no cookbook,
no plan, no repair loop, no starter library — the harness only builds, renders and
judges whatever comes back.

Backends (``get_oneshot_backend``):

* ``claude-code`` / ``claude-code:<model>`` — ``claude -p`` with ``--tools ""``
  and ``--max-turns 1`` (subscription; model default when none given).
* ``codex`` / ``codex:<model>[@<effort>]`` — ``codex exec`` read-only sandbox, JSONL
  events, ``-c model_reasoning_effort=`` (default ``high``, see
  ``Settings.agents.codex_reasoning_effort``) (subscription; model default when none given).
* ``gemini:<m>`` / ``anthropic:<m>`` / ``openai:<m>`` — ``codeverse3d.models``
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

from bench._infra import is_infra_failure
from codeverse3d.agents.backends import (
    effort_overrides,
    parse_claude_json,
    parse_codex_jsonl,
    split_model_effort,
    usage_from_envelope,
)
from codeverse3d.agents.cli_common import is_secret_env, run_with_watchdog, tail
from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import BuildResult, GateReport
from codeverse3d.contracts.chat import ChatMessage, ChatRequest
from codeverse3d.contracts.common import ENTRY_FILE, Language, Usage
from codeverse3d.contracts.spec import Spec
from codeverse3d.conventions import LANGUAGE_FRAME, frame_doc
from codeverse3d.tracks.generation import MultiFileParseError, parse_multifile
from codeverse3d.tracks.repair import format_error_report
from codeverse3d.workspace import Workspace

MODEL_FILE = "src/model.py"
URDF_FILE = "src/robot.urdf"
SHADER_FILE = ENTRY_FILE[Language.GLSL_SHADER]   # src/shader.frag
SCENE_FILE = ENTRY_FILE[Language.SCENE_THREEJS]  # src/scene.js
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

#: The URDF (track articulated_object, language urdf_blender) minimal contract: the SAME
#: bpy rules for the link meshes plus the frame recipe of codeverse3d/prompts/urdf/contract.md
#: (D18) condensed to its rules — no worked example, no cookbook, no skeleton: the one-shot
#: arm gets what a careful reader of the contract would know, nothing the harness builds.
_URDF_CONTRACT_BODY = """You write TWO files.
`{model_file}` in raw Blender Python (bpy, Blender 5.x API): ONE mesh object per LINK, `obj.name` == the
link name, built in WORLD coordinates at the REST pose (URDF q = 0: doors closed, drawers in, lids down,
arms at home).  The harness runs it headless in an emptied scene and exports `meshes/<link>.glb` for every
link with the world coordinates baked in — you never export.
`{urdf_file}` hand-written URDF (links + joints) for the SAME object.

Frame, units and placement: {frame}  Build at real-world size, on z = 0.

URDF recipe (the harness verifies every line numerically and FAILS the build otherwise):
- Every joint `rpy="0 0 0"`: all link frames stay axis-aligned with the world.
- Root link frame = world origin (0,0,0).  Every other link frame = its joint's PIVOT: a world point on the
  joint axis (hinge line / slide axis / axle); for a fixed joint, the child's attachment point.
- `<joint><origin xyz>` = pivot_child − frame_parent (plain subtraction; for a child of the root that is the
  pivot itself).
- `<visual>` AND `<collision>` `<origin xyz>` = −pivot_link (root: 0 0 0), because the mesh holds world
  coordinates and the link frame sits at the pivot.  One `<visual>` and one identical `<collision>` per link,
  geometry `<mesh filename="meshes/<link>.glb"/>`, no scale.
- `<axis xyz>` is a unit vector in WORLD coordinates; positive q must move the child the way a user expects
  (door swings open, drawer pulls out towards −Y, lid lifts up) — negate the axis if not, never swap limits.
- `revolute` / `prismatic` joints need `<limit lower upper effort velocity/>` with lower ≤ 0 ≤ upper;
  `continuous` only `<limit effort velocity/>`; `fixed` none.
- One tree: exactly one root link, every other link the child of exactly one joint, no cycles, ≤ 60 links.
  Hardware on a moving part (handle on a door) attaches to the moving link with a `fixed` joint.
- Clearance 1–3 mm between a moving part and its housing over the whole range; fixed children touch their
  parent (gap ≤ 2 mm).
- Names: link name == Blender object name == `meshes/<link>.glb` stem, case-sensitive, identical in both files
  (snake_case, e.g. `door`, `handle_left`; never `Door.001`); joint names unique; `world` is reserved.
- Forbidden in the URDF: `<gazebo>`, `<transmission>`, `<sensor>`, xacro, `package://`, mesh scale, inline
  primitives, `mimic`.  Inertial blocks optional.

`{model_file}` rules: one mesh object per link linked to `bpy.context.scene.collection`, every mesh gets a
material (Principled BSDF); do NOT create cameras or lights, touch render/world settings, call
`bpy.ops.render.*`, `bpy.ops.export_*`, `bpy.ops.import_*`, `bpy.ops.wm.*`, read or write files, use the
network, or import anything other than `bpy`, `bmesh`, `mathutils`, `math`, `random`.  Under 500k
triangles, under 120 s.  The script must build every link when executed top-to-bottom (call your `main()`
at module level) and run without errors in headless Blender — there is no second chance."""

_OUTPUT_RULE_URDF = (f"You have NO tools in this session: you cannot write files, run Blender or inspect anything — "
                     f"both files must appear in your reply.  Reply with the COMPLETE contents of the two files in "
                     f"this exact envelope and nothing else (no prose before, between or after):\n"
                     f"=== FILE: {MODEL_FILE} ===\n<python>\n=== END FILE ===\n"
                     f"=== FILE: {URDF_FILE} ===\n<xml>\n=== END FILE ===")


#: The GLSL (track graphics, language glsl_shader) minimal contract: what the harness prepends
#: and expects, condensed from codeverse3d/prompts/glsl_shader/contract.md — no cookbook, no
#: recipes file, no frame-metric self-check.
_GLSL_CONTRACT_BODY = """You write ONE file, `{shader_file}`: the BODY of a Shadertoy-style fragment shader (GLSL 330 core).
The harness prepends these lines itself — do NOT write them (redeclaring any is an error):
  #version 330 core · uniform float u_time · uniform vec2 u_resolution · uniform vec2 u_mouse (always 0,0)
  uniform int u_frame · uniform sampler2D u_prev (previous frame, black at frame 0) · uniform sampler2D u_noise
  (256x256 RGBA white noise) · #define iTime u_time (also iResolution iFrame iMouse iChannel0=u_prev
  iChannel1=u_noise) · out vec4 fragColor (the ONLY output).
Entry point: `void mainImage(out vec4 fragColor, in vec2 fragCoord)` (preferred) or `void main()`.
NOT available: iChannel2/3, iDate, iSampleRate, textures, images, sound, includes, files.
The harness compiles it with moderngl headless, renders 1280x720 frames at t = 0, 1, 2.5, 4 and 6 s and
judges them: the shader must compile on GLSL 330 core, animate over t, and stay readable (not black,
not blown out).  Keep it under ~400 lines and cheap enough for 30 fps at 1280x720."""

_OUTPUT_RULE_GLSL = (f"You have NO tools in this session: you cannot write files, compile or render anything — "
                     f"the code must appear in your reply.  Reply with the COMPLETE contents of `{SHADER_FILE}` as ONE "
                     f"```glsl fenced code block and nothing else (no prose before or after, no partial snippets).")

#: The scene (track scene, language scene_threejs) minimal contract: the createScene shape and the
#: import rule of codeverse3d/prompts/scene_threejs/contract.md — no starter library, no zones,
#: no assets, no cookbook, no gates table.
_SCENE_CONTRACT_BODY = """You write ONE file, `{scene_file}`, an ES module for three.js r182:
  export async function createScene({{ THREE, renderer, loaders }}) → {{ scene, cameras, update(t, dt) }}
Imports: only `three` and `three/addons/*` (GLTFLoader, BufferGeometryUtils, Sky, Water, EffectComposer …).
No other files, packages, CDNs, textures, images, models or network — everything is procedural geometry
and materials (MeshStandardMaterial / ShaderMaterial).  Y-up, +Z front, metres, real-world scale.
The return value: `scene` is a THREE.Scene with `scene.fog` set and a background colour or sky dome, lit
by your own lights; `cameras` is 3–5 PLAIN objects `{{ name, position: [x, y, z], lookAt: [x, y, z], fov }}`
(PascalCase names; fov 35–60; the first is the establishing shot; never inside or within 0.5 m of
geometry; eye height about 1.6 m for human views); `update(t, dt)` advances the animation
deterministically and cheaply (no allocations).
The harness imports the module in headless Chrome with a WebGLRenderer (shadows on, ACES tone mapping),
awaits createScene, renders every camera at t = 0 and 1.5 s and judges the frames: readable exposure
(mean luminance ≥ 0.15, no black or blown frames), things resting on the ground, cameras framing the
content, visible motion between the two times.  Under 2 M triangles and 200 draw calls (InstancedMesh
for anything repeated); createScene must resolve in under 15 s."""

_OUTPUT_RULE_SCENE = (f"You have NO tools in this session: you cannot write files, run node or render anything — "
                      f"the code must appear in your reply.  Reply with the COMPLETE contents of `{SCENE_FILE}` as ONE "
                      f"```js fenced code block and nothing else (no prose before or after, no partial snippets).")


#: the one file a single-file language answers with (URDF answers with two, see files_for)
_ENTRY = {Language.BLENDER: MODEL_FILE, Language.GLSL_SHADER: SHADER_FILE, Language.SCENE_THREEJS: SCENE_FILE}


def files_for(language: Language) -> list[str]:
    """The files a one-shot answer must contain for ``language``."""
    if language is Language.URDF_BLENDER:
        return [MODEL_FILE, URDF_FILE]
    if language not in _ENTRY:
        raise ValueError(f"no one-shot contract for {language.value}: " + ", ".join(sorted(x.value for x in _ENTRY)))
    return [_ENTRY[language]]


def minimal_contract(language: Language = Language.BLENDER) -> str:
    """Frame/units come from ``codeverse3d.conventions`` (the single source of truth)."""
    if language is Language.URDF_BLENDER:
        return _URDF_CONTRACT_BODY.format(model_file=MODEL_FILE, urdf_file=URDF_FILE,
                                          frame=frame_doc(LANGUAGE_FRAME["urdf_blender"]))
    if language is Language.GLSL_SHADER:
        return _GLSL_CONTRACT_BODY.format(shader_file=SHADER_FILE)
    if language is Language.SCENE_THREEJS:
        return _SCENE_CONTRACT_BODY.format(scene_file=SCENE_FILE)
    return _CONTRACT_BODY.format(model_file=MODEL_FILE, frame=frame_doc(LANGUAGE_FRAME["blender"]))


def output_rule(language: Language = Language.BLENDER) -> str:
    return {Language.URDF_BLENDER: _OUTPUT_RULE_URDF, Language.GLSL_SHADER: _OUTPUT_RULE_GLSL,
            Language.SCENE_THREEJS: _OUTPUT_RULE_SCENE}.get(language, _OUTPUT_RULE)


def oneshot_prompt(spec: Spec) -> str:
    """The entire input of a one-shot arm: brief + constraints + minimal contract + output rule."""
    c = spec.constraints
    lead = {Language.URDF_BLENDER: "Model this ARTICULATED object as raw bpy link meshes plus a hand-written URDF",
            Language.GLSL_SHADER: "Write this as a Shadertoy-style fragment shader",
            Language.SCENE_THREEJS: "Build this scene in raw three.js"}.get(spec.language, "Model this object in raw bpy")
    lines = [f"{lead}: {spec.prompt.strip()}"]
    if c.dimensions_m:
        lines.append("Dimensions (m): " + ", ".join(f"{k}={v:g}" for k, v in c.dimensions_m.items()))
    if c.style:
        lines.append(f"Style: {c.style}")
    lines.extend(f"MUST HAVE: {m}" for m in c.must_have)
    lines.extend(f"MUST NOT: {m}" for m in c.must_not)
    return "\n".join(lines) + "\n\n" + minimal_contract(spec.language) + "\n\n" + output_rule(spec.language)


def repair_prompt(spec: Spec, previous_code: str | dict[str, str], build: BuildResult, lint: GateReport,
                  attempt: int) -> str:
    """Error-feedback retry (``oneshot+repair`` arm only): previous file(s) + the build error report."""
    report = format_error_report(build, lint)
    files = {MODEL_FILE: previous_code} if isinstance(previous_code, str) else previous_code
    fence = {".urdf": "xml", ".frag": "glsl", ".glsl": "glsl", ".js": "js"}
    prev = "\n\n".join(f"PREVIOUS `{path}`:\n```{fence.get(Path(path).suffix, 'python')}\n{body[:40_000]}\n```"
                       for path, body in files.items())
    plural = "files" if len(files) > 1 else "file"
    return (oneshot_prompt(spec)
            + f"\n\nYour previous attempt ({attempt}) did NOT build.  Fix the error below (smallest correct change, "
              f"keep every part) and return the COMPLETE corrected {plural}.\n\nERROR REPORT:\n{report}\n\n{prev}")


# ----------------------------------------------------------------------------- results
class OneShotResult(BaseModel):
    ok: bool = Field(description="a non-empty answer came back (parsing happens later)")
    text: str = ""
    usage: Usage = Field(default_factory=Usage)
    tool_calls: int = Field(default=0, description="tool/command calls the CLI reported (should be 0)")
    notes: str = ""
    infra_failed: bool = Field(default=False, description=(
        "the provider, not the model, failed.  Decided at the raise site because only "
        "there does the exception still carry .status and .__cause__: stringifying it "
        "into `notes` first threw both away, so the one-shot arm scored a hard 0.0 for "
        "the same outage that dropped the harness arm (see bench/_infra.py)."))
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
    return body if any(k in body for k in ("import bpy", "createScene", "mainImage", "void main")) else text


#: what a bare (unfenced) answer must contain to be read as the file at all
_BARE_MARKERS = {SCENE_FILE: ("createScene",), SHADER_FILE: ("mainImage", "void main(")}


def extract_model_file(text: str, rel: str = MODEL_FILE) -> str:
    """The one file of a single-file answer (tolerant: fenced block / bare code / FILE envelope /
    hallucinated Write-tool XML) — ``src/model.py`` by default, the shader or the scene module
    when ``rel`` says so."""
    stripped = _strip_hallucinated_tool_xml(text)
    try:
        files = parse_multifile(stripped, expected_files=[rel])
    except MultiFileParseError:
        # a bare module body with no fence and no envelope (the tool-less CLI answered with
        # nothing but the code): accepted only when it carries the language's entry point —
        # prose never does, and the python path keeps parse_multifile's own bare-code rule
        if "```" in stripped or not any(k in stripped for k in _BARE_MARKERS.get(rel, ())):
            raise
        files = {rel: stripped.strip("\n") + "\n"}
    code = files.get(rel) or next(iter(files.values()), "")
    if not code.strip():
        raise MultiFileParseError(f"empty {rel} in the answer")
    return code if code.endswith("\n") else code + "\n"


def extract_files(text: str, language: Language = Language.BLENDER) -> dict[str, str]:
    """Every file a one-shot answer must contain for ``language`` → ``{path: body}``.

    Single-file languages keep the tolerant static path (:func:`extract_model_file`).  A
    URDF answer must carry BOTH files in the ``=== FILE: … ===`` envelope (or fenced blocks
    each preceded by its path); an answer with only one of them is a format failure —
    ``MultiFileParseError`` → the cell is ``no_code``, an earned zero.
    """
    expected = files_for(language)
    if len(expected) == 1:
        return {expected[0]: extract_model_file(text, expected[0])}
    files = parse_multifile(text, expected_files=expected)
    missing = [f for f in expected if not (files.get(f) or "").strip()]
    if missing:
        raise MultiFileParseError(f"answer lacks {missing} (got {sorted(files)})")
    return {f: (files[f] if files[f].endswith("\n") else files[f] + "\n") for f in expected}


def write_answer_files(ws: Workspace, text: str, language: Language = Language.BLENDER) -> list[Path]:
    """Parse the answer and write every expected file into the workspace (raises MultiFileParseError)."""
    written = []
    for rel, body in extract_files(text, language).items():
        dest = ws.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body)
        written.append(dest)
    return written


# ----------------------------------------------------------------------------- common
def _scratch_cwd(label: str) -> Path:
    """A fresh directory outside any repo: no CLAUDE.md/AGENTS.md/.git context can leak in."""
    return Path(tempfile.mkdtemp(prefix=f"c3d_oneshot_{label}_"))


def _clean_env(keep: set[str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in keep or not is_secret_env(k)}
    env["C3D_AGENT_CONTEXT"] = "1"
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
        import tomllib

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
    """One ``ChatRequest`` through ``codeverse3d.models`` (``gemini:*`` / ``anthropic:*`` / ``openai:*``)."""

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
            from codeverse3d.models import get_chat_model

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
            return OneShotResult(ok=False, notes=f"{type(e).__name__}: {e}", infra_failed=is_infra_failure(e),
                                 duration_s=round(time.time() - t0, 2), transcript_dir=str(out_dir))
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
           "extract_model_file", "get_oneshot_backend", "minimal_contract", "oneshot_prompt", "repair_prompt"]
