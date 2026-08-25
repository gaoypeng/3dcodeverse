"""compare_backends on an articulated battery: two-file one-shot answers, articulated_v1 fixed judge."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench._compare_report import RUBRIC_BY_TRACK  # noqa: E402
from bench._fixed_eval import FixedEvaluator  # noqa: E402
from bench._oneshot import (  # noqa: E402
    MODEL_FILE,
    URDF_FILE,
    extract_files,
    files_for,
    minimal_contract,
    oneshot_prompt,
    repair_prompt,
    write_answer_files,
)
from bench.run_bench import Battery  # noqa: E402
from codeverse.contracts.artifacts import BuildResult, GateReport  # noqa: E402
from codeverse.contracts.common import Language, Track  # noqa: E402
from codeverse.contracts.spec import Constraints, Spec  # noqa: E402
from codeverse.tracks.generation import MultiFileParseError  # noqa: E402
from codeverse.workspace import Workspace  # noqa: E402

PROMPTS = REPO / "bench" / "prompts"
PY = "import bpy\nprint(1)\n"
XML = '<?xml version="1.0"?>\n<robot name="x"><link name="base"/></robot>\n'
ENVELOPE = f"=== FILE: {MODEL_FILE} ===\n{PY}=== END FILE ===\n=== FILE: {URDF_FILE} ===\n{XML}=== END FILE ===\n"


def _urdf_spec() -> Spec:
    return Spec(id="t/lamp", track=Track.ARTICULATED_OBJECT, language=Language.URDF_BLENDER,
                prompt="an architect lamp with two hinged arms",
                constraints=Constraints(must_have=["two revolute joints"], dimensions_m={"height": 0.6}))


def test_urdf_oneshot_prompt_carries_the_frame_recipe_and_the_two_file_envelope():
    p = oneshot_prompt(_urdf_spec())
    assert "hand-written URDF" in p and "architect lamp" in p and "MUST HAVE: two revolute joints" in p
    for rule in ("rpy=\"0 0 0\"", "pivot_child − frame_parent", "−pivot_link", "meshes/<link>.glb", "lower ≤ 0 ≤ upper"):
        assert rule in p, rule
    assert f"=== FILE: {MODEL_FILE} ===" in p and f"=== FILE: {URDF_FILE} ===" in p
    # no harness help: no worked example, no cookbook, no skeleton
    assert "pedal" not in p.lower() and "cookbook" not in p.lower() and "PIVOT[" not in p
    assert minimal_contract(Language.URDF_BLENDER) in p
    # the static contract is untouched by the language switch
    assert minimal_contract() == minimal_contract(Language.BLENDER) and URDF_FILE not in minimal_contract()


def test_files_for_and_extract_files_two_file_answer():
    assert files_for(Language.BLENDER) == [MODEL_FILE] and files_for(Language.URDF_BLENDER) == [MODEL_FILE, URDF_FILE]
    got = extract_files(ENVELOPE, Language.URDF_BLENDER)
    assert got == {MODEL_FILE: PY, URDF_FILE: XML}
    # fenced blocks each preceded by their path also parse
    fenced = f"{MODEL_FILE}\n```python\n{PY}```\n{URDF_FILE}\n```xml\n{XML}```\n"
    assert extract_files(fenced, Language.URDF_BLENDER) == {MODEL_FILE: PY, URDF_FILE: XML}
    # the static path is unchanged
    assert extract_files("```python\nimport bpy\n```", Language.BLENDER) == {MODEL_FILE: "import bpy\n"}


@pytest.mark.parametrize("text", [
    f"```python\n{PY}```",                                        # one block, two expected
    f"=== FILE: {MODEL_FILE} ===\n{PY}=== END FILE ===\n",        # envelope with the URDF missing
    f"=== FILE: {MODEL_FILE} ===\n{PY}=== END FILE ===\n=== FILE: {URDF_FILE} ===\n\n=== END FILE ===\n",  # empty urdf
])
def test_a_urdf_answer_without_both_files_is_a_format_failure(text: str):
    with pytest.raises(MultiFileParseError):
        extract_files(text, Language.URDF_BLENDER)


def test_write_answer_files_writes_both(tmp_path):
    ws = Workspace(tmp_path / "ws")
    ws.create()
    paths = write_answer_files(ws, ENVELOPE, Language.URDF_BLENDER)
    assert [p.relative_to(ws.root).as_posix() for p in paths] == [MODEL_FILE, URDF_FILE]
    assert (ws.root / URDF_FILE).read_text() == XML and (ws.root / MODEL_FILE).read_text() == PY


def test_urdf_repair_prompt_carries_both_previous_files():
    build = BuildResult(ok=False, language="urdf_blender", error_type="FKMismatch",
                        error_message="link 'arm' visual origin should be -0.1 0 0.3", error_file=URDF_FILE, error_line=9)
    p = repair_prompt(_urdf_spec(), {MODEL_FILE: PY, URDF_FILE: XML}, build, GateReport(gate="lint:urdf", passed=True), attempt=1)
    assert "FKMismatch" in p and "corrected files" in p
    assert f"PREVIOUS `{MODEL_FILE}`" in p and f"PREVIOUS `{URDF_FILE}`" in p and "```xml" in p


def test_fixed_evaluator_follows_the_battery_track():
    ev = FixedEvaluator("gemini:x", n_samples=3, track=Track.ARTICULATED_OBJECT, language=Language.URDF_BLENDER)
    assert ev.rubric == "articulated_v1" == RUBRIC_BY_TRACK["articulated_object"]
    assert ev.language is Language.URDF_BLENDER and ev.runtime.language is Language.URDF_BLENDER
    static = FixedEvaluator("gemini:x")
    assert static.rubric == "static_object_v1" and static.language is Language.BLENDER
    pinned = FixedEvaluator("gemini:x", rubric="asset_v1", track=Track.ARTICULATED_OBJECT)
    assert pinned.rubric == "asset_v1"


def test_main_builds_the_evaluator_from_the_battery(monkeypatch, tmp_path):
    from bench import compare_backends as cb

    seen = {}

    def fake_run_matrix(battery_path, out_dir, arms, opts, deps, on_result=None):
        seen["track"], seen["language"], seen["rubric"] = deps.evaluator.track, deps.evaluator.language, deps.evaluator.rubric
        (Path(out_dir)).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "results.jsonl").write_text("")

    monkeypatch.setattr(cb, "run_matrix", fake_run_matrix)
    monkeypatch.setattr(cb, "_preflight", lambda *a, **k: True)
    rc = cb.main(["--prompts", str(PROMPTS / "articulated_v2.yaml"), "--arms", "oneshot:gemini:x",
                  "--out", str(tmp_path / "out"), "--no-preflight", "--judge-samples", "3"])
    assert rc == 0
    assert seen == {"track": Track.ARTICULATED_OBJECT, "language": Language.URDF_BLENDER, "rubric": "articulated_v1"}
    b = Battery.load(PROMPTS / "articulated_v2.yaml")
    assert b.track is Track.ARTICULATED_OBJECT and all(p.must_have for p in b.prompts)
