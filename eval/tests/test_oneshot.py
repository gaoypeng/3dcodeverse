"""One-shot helpers: prompt, answer parsing, CLI argv, registry (offline)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench._oneshot import (  # noqa: E402
    MODEL_FILE,
    SCENE_FILE,
    SHADER_FILE,
    ApiOneShot,
    ClaudeOneShot,
    CodexOneShot,
    extract_files,
    extract_model_file,
    files_for,
    minimal_contract,
    oneshot_prompt,
)
from codeverse3d.contracts.common import Language, Track  # noqa: E402
from codeverse3d.contracts.spec import Constraints, Spec  # noqa: E402
from codeverse3d.tracks.generation import MultiFileParseError  # noqa: E402


def _spec() -> Spec:
    return Spec(id="t/stool", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="a three-legged stool",
                constraints=Constraints(must_have=["round seat", "three legs"], dimensions_m={"height": 0.45}))


def test_prompt_is_brief_plus_minimal_contract_only():
    p = oneshot_prompt(_spec())
    assert "a three-legged stool" in p and "MUST HAVE: round seat" in p and "height=0.45" in p
    assert MODEL_FILE in p and "Z is UP" in p and "meters" in p and "bpy.ops.export_*" in p
    # no harness help: no cookbook snippets, no plan table, no tool cards
    assert "cookbook" not in p.lower() and "bmesh.ops.create_cone" not in p and "| part |" not in p
    assert minimal_contract() in p and p.rstrip().endswith("no partial snippets).")


@pytest.mark.parametrize("spec, opening, needed, harness_only", [
    (Spec(id="t/harbour", track=Track.SCENE, language=Language.SCENE_THREEJS, prompt="a fishing harbour at dusk"),
     "Build this scene in raw three.js: a fishing harbour at dusk",
     (SCENE_FILE, "createScene", "three/addons/*", "scene.fog", "```js"), ("src/lib", "zones/", "public/assets")),
    (Spec(id="t/aurora", track=Track.GRAPHICS, language=Language.GLSL_SHADER, prompt="an aurora over snow"),
     "Write this as a Shadertoy-style fragment shader: an aurora over snow",
     (SHADER_FILE, "mainImage", "u_time", "#version 330 core", "```glsl"), ("recipes.glsl", "buffer_a")),
])
def test_scene_and_shader_prompts_are_their_contract_and_nothing_of_the_harness(spec, opening, needed, harness_only):
    p = oneshot_prompt(spec)
    assert p.startswith(opening) and minimal_contract(spec.language) in p
    assert all(s in p for s in needed) and "cookbook" not in p.lower()
    assert not any(s in p for s in harness_only)


def test_extract_model_file_is_tolerant_but_rejects_prose():
    for text in ("Here you go:\n```python\nimport bpy\nprint(1)\n```\nDone.",
                 "=== FILE: src/model.py ===\nimport bpy\nprint(1)\n=== END FILE ===",
                 "import bpy\nprint(1)\n"):
        assert extract_model_file(text) == "import bpy\nprint(1)\n", text
    with pytest.raises(MultiFileParseError):
        extract_model_file("Sorry, I cannot do that.")


def test_claude_argv_disables_tools_and_is_single_turn():
    argv = ClaudeOneShot("", binary="claude").argv("PROMPT")
    assert argv[:3] == ["claude", "-p", "PROMPT"]
    assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--max-turns") + 1] == "1"
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv and "--model" not in argv
    assert "--mcp-config" not in argv and "--dangerously-skip-permissions" not in argv
    argv_m = ClaudeOneShot("opus", binary="claude").argv("x")
    assert argv_m[argv_m.index("--model") + 1] == "opus"


def test_codex_argv_is_read_only_and_forces_a_reasoning_effort(tmp_path: Path):
    """Read-only sandbox, no MCP, prompt on stdin.  Effort: default high
    (Settings.agents.codex_reasoning_effort); `@effort` on the id wins; '' opts out."""
    argv = CodexOneShot("", binary="codex").argv(tmp_path, tmp_path / "last.md")
    assert argv[:3] == ["codex", "exec", "--json"]
    assert argv[argv.index("--sandbox") + 1] == "read-only" and argv[-1] == "-"
    assert "--ephemeral" in argv and "-o" in argv and "mcp_servers" not in " ".join(argv)
    argv = CodexOneShot("gpt-5.6-terra", binary="codex").argv(tmp_path, tmp_path / "last.md")
    assert argv[argv.index("--model") + 1] == "gpt-5.6-terra"
    assert "model_reasoning_effort=high" in argv and argv[argv.index("-c") + 1] == "model_reasoning_effort=high"
    b = CodexOneShot("gpt-5.6-luna@low", binary="codex")
    assert b.model == "gpt-5.6-luna" and b.id == "oneshot:codex:gpt-5.6-luna"
    assert "model_reasoning_effort=low" in b.argv(tmp_path, tmp_path / "last.md")
    plain = CodexOneShot("gpt-5.6-sol", binary="codex", reasoning_effort="").argv(tmp_path, tmp_path / "l.md")
    assert "model_reasoning_effort" not in " ".join(plain)


def test_api_oneshot_uses_injected_chat_model(tmp_path: Path):
    class M:
        def generate(self, req):
            from codeverse3d.contracts.chat import ChatResponse
            from codeverse3d.contracts.common import Usage

            assert req.max_output_tokens == 32000 and "stool" in req.messages[0].text
            return ChatResponse(text="```python\nimport bpy\n```", usage=Usage(cost_usd=0.004))

    r = ApiOneShot("gemini:x", chat_model=M()).generate(oneshot_prompt(_spec()), out_dir=tmp_path / "g")
    assert r.ok and r.usage.cost_usd == 0.004 and (tmp_path / "g" / "response.md").is_file() and r.tool_calls == 0

    class Boom:
        def generate(self, req):
            raise RuntimeError("quota")

    r = ApiOneShot("gemini:x", chat_model=Boom()).generate("p", out_dir=tmp_path / "boom")
    assert not r.ok and "quota" in r.notes


def test_single_file_answers_are_extracted_under_their_own_entry():
    js = "import * as THREE from 'three';\nexport async function createScene({ THREE }) { return {}; }"
    assert extract_files(f"Here you go:\n```js\n{js}\n```\n", Language.SCENE_THREEJS) == {SCENE_FILE: js + "\n"}
    glsl = "void mainImage(out vec4 o, in vec2 p) { o = vec4(p / iResolution.xy, 0.5, 1.0); }"
    assert extract_files(f"```glsl\n{glsl}\n```", Language.GLSL_SHADER) == {SHADER_FILE: glsl + "\n"}
    assert files_for(Language.SCENE_THREEJS) == [SCENE_FILE] and files_for(Language.GLSL_SHADER) == [SHADER_FILE]
    with pytest.raises(ValueError, match="no one-shot contract"):
        files_for(Language.THREEJS)


def test_a_hallucinated_write_tool_of_a_scene_module_is_still_read():
    xml = ('<invoke name="Write"><parameter name="path">src/scene.js</parameter><parameter name="content">\n'
           "export async function createScene({ THREE }) { return { scene: new THREE.Scene(), cameras: [], update() {} }; }\n"
           "</parameter></invoke>")
    got = extract_files(xml, Language.SCENE_THREEJS)[SCENE_FILE]
    assert got.startswith("export async function createScene")


def test_claude_one_shot_pins_its_effort_and_never_reads_the_users_settings():
    from bench._oneshot import ClaudeOneShot

    a = ClaudeOneShot("sonnet@high").argv("brief")
    assert a[a.index("--model") + 1] == "sonnet" and a[a.index("--effort") + 1] == "high"
    assert a[a.index("--setting-sources") + 1] == "project"
    b = ClaudeOneShot("opus").argv("brief")
    assert "--effort" in b and b[b.index("--effort") + 1]   # the harness's claude_effort, never inherited


def test_a_continued_claude_reply_is_read_whole_not_just_its_last_continuation():
    import json as _json

    from bench._oneshot import assistant_text

    first = "```js\nexport async function createScene({ THREE }) {\n  const a = 1;\n"
    second = "  return { scene: null, cameras: [], update() {} };\n}\n```"
    stream = "\n".join(_json.dumps(e) for e in (
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": first}]}},
        {"type": "user", "message": {"content": [{"type": "text", "text": "Output token limit hit. Resume directly"}]}, "isSynthetic": True},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": second}]}},
        {"type": "result", "subtype": "success", "result": second, "is_error": False},
    ))
    assert assistant_text(stream) == first + second


_CLAUDE_KILLED = [  # a reply's usage block, then no result envelope
    {"type": "system", "subtype": "init"},
    {"type": "assistant", "message": {"id": "m1", "model": "claude-sonnet-5", "content": [{"type": "text", "text": "```js"}],
                                      "usage": {"input_tokens": 9000, "output_tokens": 4000}}}]
_CODEX_KILLED = [  # an item completed, no turn did
    {"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "import bpy\n" + "# pad\n" * 500}}]


@pytest.mark.parametrize("backend, events", [
    (lambda b: ClaudeOneShot("claude-sonnet-5", binary=b), _CLAUDE_KILLED),
    (lambda b: CodexOneShot("gpt-5.5", binary=b), _CODEX_KILLED),
], ids=["claude", "codex"])
def test_a_session_that_never_finished_books_what_it_spent_not_zero(tmp_path: Path, backend, events):
    """N82a: the harness books these sessions (stream usage / codex estimate); the one-shot booked $0."""
    import json as _json

    out = tmp_path / "stdout.jsonl"
    out.write_text("\n".join(_json.dumps(e) for e in events) + "\n")
    fake = tmp_path / "cli"
    fake.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\nprint(open({str(out)!r}).read())\nsys.exit(1)\n")
    fake.chmod(0o755)
    r = backend(str(fake)).generate("brief", out_dir=tmp_path / "o", timeout_s=30)
    assert r.usage.input_tokens > 0 and r.usage.cost_usd > 0, r.usage
