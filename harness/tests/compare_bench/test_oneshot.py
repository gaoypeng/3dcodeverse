"""One-shot helpers: prompt, answer parsing, CLI argv, registry (offline)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench._oneshot import (  # noqa: E402
    MODEL_FILE,
    ApiOneShot,
    ClaudeOneShot,
    CodexOneShot,
    extract_model_file,
    get_oneshot_backend,
    minimal_contract,
    oneshot_prompt,
    repair_prompt,
)
from codeverse.contracts.artifacts import BuildResult, GateReport  # noqa: E402
from codeverse.contracts.common import Language, Track  # noqa: E402
from codeverse.contracts.spec import Constraints, Spec  # noqa: E402
from codeverse.tracks.generation import MultiFileParseError  # noqa: E402


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


def test_repair_prompt_carries_error_and_previous_code():
    build = BuildResult(ok=False, language="blender", error_type="NameError", error_message="name 'foo' is not defined",
                        error_file=MODEL_FILE, error_line=7)
    p = repair_prompt(_spec(), "import bpy\nfoo()\n", build, GateReport(gate="lint:blender", passed=True), attempt=1)
    assert "BUILD FAILED" in p and "NameError" in p and "foo()" in p and "previous attempt (1)" in p


@pytest.mark.parametrize("text", [
    "```python\nimport bpy\nprint(1)\n```",
    "Here you go:\n```python\nimport bpy\nprint(1)\n```\nDone.",
    "=== FILE: src/model.py ===\nimport bpy\nprint(1)\n=== END FILE ===",
    "import bpy\nprint(1)\n",
])
def test_extract_model_file_tolerant(text: str):
    assert extract_model_file(text) == "import bpy\nprint(1)\n"


def test_extract_model_file_rejects_prose():
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


def test_codex_argv_is_read_only_and_reads_prompt_from_stdin(tmp_path: Path):
    argv = CodexOneShot("", binary="codex").argv(tmp_path, tmp_path / "last.md")
    assert argv[:3] == ["codex", "exec", "--json"]
    assert argv[argv.index("--sandbox") + 1] == "read-only" and argv[-1] == "-"
    assert "--ephemeral" in argv and "-o" in argv and "mcp_servers" not in " ".join(argv)


def test_codex_oneshot_forces_a_reasoning_effort(tmp_path: Path):
    """Default high (Settings.agents.codex_reasoning_effort); `@effort` on the id wins; '' opts out."""
    argv = CodexOneShot("gpt-5.6-terra", binary="codex").argv(tmp_path, tmp_path / "last.md")
    assert argv[argv.index("--model") + 1] == "gpt-5.6-terra"
    assert "model_reasoning_effort=high" in argv and argv[argv.index("-c") + 1] == "model_reasoning_effort=high"
    b = CodexOneShot("gpt-5.6-luna@low", binary="codex")
    assert b.model == "gpt-5.6-luna" and b.id == "oneshot:codex:gpt-5.6-luna"
    assert "model_reasoning_effort=low" in b.argv(tmp_path, tmp_path / "last.md")
    plain = CodexOneShot("gpt-5.6-sol", binary="codex", reasoning_effort="").argv(tmp_path, tmp_path / "l.md")
    assert "model_reasoning_effort" not in " ".join(plain)


def test_registry():
    assert isinstance(get_oneshot_backend("claude-code"), ClaudeOneShot)
    assert get_oneshot_backend("claude-code:sonnet").model == "sonnet"
    assert isinstance(get_oneshot_backend("codex"), CodexOneShot)
    api = get_oneshot_backend("gemini:gemini-3.7-flash")
    assert isinstance(api, ApiOneShot) and api.id == "oneshot:gemini:gemini-3.7-flash"
    for bad in ("gemini", "gemini-cli:x", "", "anthropic:"):
        with pytest.raises(ValueError):
            get_oneshot_backend(bad)


def test_api_oneshot_uses_injected_chat_model(tmp_path: Path):
    class M:
        def generate(self, req):
            from codeverse.contracts.chat import ChatResponse
            from codeverse.contracts.common import Usage

            assert req.max_output_tokens == 32000 and "stool" in req.messages[0].text
            return ChatResponse(text="```python\nimport bpy\n```", usage=Usage(cost_usd=0.004))

    r = ApiOneShot("gemini:x", chat_model=M()).generate(oneshot_prompt(_spec()), out_dir=tmp_path / "g")
    assert r.ok and r.usage.cost_usd == 0.004 and (tmp_path / "g" / "response.md").is_file() and r.tool_calls == 0


def test_api_oneshot_model_error_is_recorded(tmp_path: Path):
    class Boom:
        def generate(self, req):
            raise RuntimeError("quota")

    r = ApiOneShot("gemini:x", chat_model=Boom()).generate("p", out_dir=tmp_path / "g")
    assert not r.ok and "quota" in r.notes


def test_extract_model_file_from_hallucinated_write_tool_xml():
    text = ('<invoke name="Write">\n<parameter name="file_path">/tmp/x/src/model.py</parameter>\n'
            '<parameter name="content">import bpy\nprint(2)\n</parameter>\n</invoke>\n\nsyntax ok\n')
    assert extract_model_file(text) == "import bpy\nprint(2)\n"
