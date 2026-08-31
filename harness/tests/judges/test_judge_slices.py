"""D48: conditional cross-section slices in the judge payload.

The load-bearing invariant is byte-identity: a round WITHOUT a connectivity
gate ERROR must build exactly the pre-D48 messages (same system prompt, same
parts, same ``judge_prompt_hash``) whatever the knob or ``glb_path`` say.  A
gate-ERROR round on an object track appends the slice images AFTER the
montages and detail crops, describes them in the view-rig text, and adds the
provenance-elicitation sentence to the defect-checklist bullet exactly once.
"""

from __future__ import annotations

from pathlib import Path

import trimesh

from codeverse.config import Settings, get_settings
from codeverse.contracts.artifacts import BuildResult, GateFinding, GateReport, RenderSet, Severity
from codeverse.contracts.chat import ImagePart, TextPart
from codeverse.contracts.run import RoundRecord, RunRecord
from codeverse.judges.base import JudgeInput
from codeverse.judges.prompt_builder import (
    PROVENANCE_ELICITATION,
    build_judge_messages,
    connectivity_error_pairs,
    judge_prompt_hash,
)
from codeverse.judges.rubrics import load_rubric
from codeverse.judges.vlm_judge import SLICE_LABELS, VlmJudge
from codeverse.workspace import Workspace
from tests.judges.conftest import ACCEPTANCE, FakeChatModel, good_reply, make_renders, make_spec

R = load_rubric("static_object_v1")
IDS = ["A1", "A2"]


def two_box_glb(path: Path) -> Path:
    """Two 0.3 m boxes overlapping by 50 mm along x — both centre planes show the overlap."""
    sc = trimesh.Scene()
    a = trimesh.creation.box(extents=(0.3, 0.3, 0.3))
    a.apply_translation((0, 0.15, 0))
    b = trimesh.creation.box(extents=(0.3, 0.3, 0.3))
    b.apply_translation((0.25, 0.15, 0))
    sc.add_geometry(a, node_name="A", geom_name="A")
    sc.add_geometry(b, node_name="B", geom_name="B")
    sc.export(str(path))
    return path


def dirty_gates() -> list[GateReport]:
    return [GateReport(gate="connectivity", passed=False, findings=[
        GateFinding(gate="connectivity", severity=Severity.ERROR, target="A",
                    message="'A' and 'B' interpenetrate by ≈50.0 mm",
                    data={"kind": "penetration", "other": "B", "entering": "B", "container": "A",
                          "depth_m": 0.05}),
    ])]


def clean_gates() -> list[GateReport]:
    return [GateReport(gate="connectivity", passed=True, findings=[
        GateFinding(gate="connectivity", severity=Severity.WARN, target="A",
                    message="'A' and 'B' overlap by 1.2 mm (weld)",
                    data={"kind": "penetration", "other": "B", "depth_m": 0.0012}),
    ])]


def make_input(renders: RenderSet, gates: list[GateReport], glb: Path | None, **kw) -> JudgeInput:
    return JudgeInput(spec=make_spec(**kw.pop("spec_kw", {})), renders=renders, gates=gates,
                      acceptance=list(ACCEPTANCE), glb_path=str(glb) if glb else None, **kw)


def judge_request(inp: JudgeInput, cache_dir: Path):
    model = FakeChatModel([good_reply(R, IDS, 0.8)])
    VlmJudge("static_object_v1", model_id="fake:fake-1", chat_model=model, cache_dir=cache_dir).judge(inp)
    return model.requests[0]


def labels(req) -> list[str]:
    return [p.label for m in req.messages for p in m.parts if isinstance(p, ImagePart)]


def text_of(req) -> str:
    return "\n".join(p.text for m in req.messages for p in m.parts if isinstance(p, TextPart))


# --------------------------------------------------------------------- (a) clean = byte-identical
def test_clean_gates_build_byte_identical_messages(tmp_path, cache_dir):
    """The invariant: glb present + knob on, but no gate ERROR → the exact pre-D48 payload."""
    inp = make_input(make_renders(tmp_path / "r"), clean_gates(), two_box_glb(tmp_path / "o.glb"))
    req = judge_request(inp, cache_dir)
    system, messages = build_judge_messages(inp, R, cache_dir=cache_dir)  # the baseline builder
    assert req.system == system
    assert [m.model_dump() for m in req.messages] == [m.model_dump() for m in messages]
    assert PROVENANCE_ELICITATION not in req.system
    assert "cross-section" not in text_of(req)
    # and the protocol hash is untouched: the hash builds the default (elicitation-free) prompt
    assert judge_prompt_hash(R) == VlmJudge("static_object_v1", model_id="fake:fake-1").prompt_hash


def test_knob_off_wrong_track_and_missing_glb_stay_baseline(tmp_path, cache_dir, monkeypatch):
    renders = make_renders(tmp_path / "r")
    glb = two_box_glb(tmp_path / "o.glb")
    baseline = build_judge_messages(make_input(renders, dirty_gates(), None), R, cache_dir=cache_dir)

    # dirty gates but no glb_path (e.g. a pre-D48 record): baseline
    req = judge_request(make_input(renders, dirty_gates(), None), cache_dir)
    assert req.system == baseline[0] and PROVENANCE_ELICITATION not in req.system
    assert [m.model_dump() for m in req.messages] == [m.model_dump() for m in baseline[1]]

    # dirty gates + glb on a NON-OBJECT track (stray glb_path on a scene input): baseline —
    # the SLICE_TRACKS guard, tested directly at the VlmJudge layer
    req = judge_request(make_input(renders, dirty_gates(), glb,
                                   spec_kw={"track": "scene", "language": "scene_threejs"}), cache_dir)
    assert req.system == baseline[0] and PROVENANCE_ELICITATION not in req.system
    assert "cross-section" not in text_of(req)

    # dirty gates + glb, but the knob is off: baseline
    monkeypatch.setattr("codeverse.judges.vlm_judge.get_settings",
                        lambda: Settings(judge={"slices": "off"}))
    req = judge_request(make_input(renders, dirty_gates(), glb), cache_dir)
    assert PROVENANCE_ELICITATION not in req.system and "cross-section" not in text_of(req)
    get_settings.cache_clear()


# --------------------------------------------------------------------- (b) dirty = slices appended
def test_dirty_gates_append_slices_after_crops_with_rig_text_and_one_elicitation(tmp_path, cache_dir):
    inp = make_input(make_renders(tmp_path / "r"), dirty_gates(), two_box_glb(tmp_path / "o.glb"))
    req = judge_request(inp, cache_dir)

    # placement: montage, 2 detail crops, THEN the two slices — never prepended
    lbls = labels(req)
    assert lbls[-2:] == [SLICE_LABELS["front_back"], SLICE_LABELS["left_right"]]
    assert "DETAIL CROP" in lbls[-3] and len(lbls) == 5  # 1 montage + 2 crops + 2 slices

    text = text_of(req)
    assert "After the crops, 2 cross-section slice(s) show the interior" in text
    assert "A gap between parts IN THE CUT PLANE is not evidence of disconnection" in text
    # the 2026-08-31 watch-item battery: the generic caveat did not stop a 2.4 mm open join
    # and section-cut islands being marked floating_part, so the text names the views that
    # CAN decide instead of merely denying the slice (a bare prohibition would suppress the
    # true marks the channel is there to win)
    assert "Judge floating_part and holes_or_inverted_faces from the shaded" in text
    assert f"- slice 1: {SLICE_LABELS['front_back']}" in text
    assert req.system.count(PROVENANCE_ELICITATION) == 1
    assert 'do not hide one to be kind. ' + PROVENANCE_ELICITATION in req.system

    # the slice pngs and their manifest landed in the judge cache, hatching the gate pair
    from codeverse.spatial.sections import SliceManifest
    manifests = list(cache_dir.glob("slices_*/manifest.json"))
    assert len(manifests) == 1
    man = SliceManifest.model_validate_json(manifests[0].read_text())
    assert man.error_pairs == [("A", "B")]
    assert all({p.a, p.b} == {"A", "B"} for s in man.rendered() for p in s.hatched_pairs)


def test_error_pair_extraction_reads_target_and_other(tmp_path):
    # target/other win over entering/container (the probe's direction is not the finding's)
    assert connectivity_error_pairs(dirty_gates()) == [("A", "B")]
    g = GateReport(gate="connectivity", passed=False, findings=[
        GateFinding(gate="connectivity", severity=Severity.ERROR, target="",
                    message="x", data={"kind": "penetration", "entering": "Leg", "container": "Seat"}),
        GateFinding(gate="connectivity", severity=Severity.ERROR, target="Mast",
                    message="floating", data={"kind": "floating"}),  # no pair: not a penetration
        GateFinding(gate="connectivity", severity=Severity.WARN, target="C",
                    message="weld", data={"kind": "penetration", "other": "D"}),  # WARN: never
    ])
    assert connectivity_error_pairs([g]) == [("Leg", "Seat")]
    assert connectivity_error_pairs([GateReport(gate="contract", passed=False, findings=[])]) == []


def test_a_dirty_round_with_a_floating_only_error_still_elicits(tmp_path, cache_dir):
    """The trigger is ANY connectivity ERROR; with no penetration pair the slices carry
    no hatch but the elicitation sentence still applies (the tested v3 semantics)."""
    gates = [GateReport(gate="connectivity", passed=False, findings=[
        GateFinding(gate="connectivity", severity=Severity.ERROR, target="B",
                    message="part 'B' is floating", data={"kind": "floating"})])]
    inp = make_input(make_renders(tmp_path / "r"), gates, two_box_glb(tmp_path / "o.glb"))
    req = judge_request(inp, cache_dir)
    assert req.system.count(PROVENANCE_ELICITATION) == 1
    assert labels(req)[-2:] == [SLICE_LABELS["front_back"], SLICE_LABELS["left_right"]]


# --------------------------------------------------------------------- (d) 3dcv judge replay
def test_replay_reproduces_the_conditional_payload_from_stored_gates_and_glb(tmp_ws: Workspace, tmp_path, cache_dir):
    from codeverse.cli._judge import build_judge_input

    glb = two_box_glb(tmp_path / "object.glb")
    renders = make_renders(tmp_path / "renders")
    rec = RunRecord(spec=make_spec(), workspace=str(tmp_ws.root))
    rnd = RoundRecord(index=0, kind="baseline", gates=dirty_gates(), renders=renders,
                      build=BuildResult(ok=True, language="blender", glb_path=str(glb)))

    inp = build_judge_input(tmp_ws, rec, rnd)
    assert inp.glb_path == str(glb)
    req = judge_request(inp, cache_dir)
    assert labels(req)[-2:] == [SLICE_LABELS["front_back"], SLICE_LABELS["left_right"]]
    assert req.system.count(PROVENANCE_ELICITATION) == 1

    # a scene round's stored build never feeds the channel, whatever it exported
    scene_rec = RunRecord(spec=make_spec(track="scene", language="scene_threejs"), workspace=str(tmp_ws.root))
    assert build_judge_input(tmp_ws, scene_rec, rnd).glb_path is None

    # a moved/deleted glb disables the channel instead of crashing the replay
    rnd_gone = rnd.model_copy(update={"build": BuildResult(ok=True, language="blender",
                                                           glb_path=str(tmp_path / "gone.glb"))})
    assert build_judge_input(tmp_ws, rec, rnd_gone).glb_path is None
