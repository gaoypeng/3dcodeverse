"""D48: conditional cross-section slices — a round without a connectivity ERROR builds the exact pre-D48 payload."""

from __future__ import annotations

from pathlib import Path

import trimesh

from codeverse3d.contracts.artifacts import (
    GateFinding,
    GateReport,
    RenderSet,
    Severity,
)
from codeverse3d.contracts.chat import ImagePart, TextPart
from codeverse3d.judges.base import JudgeInput
from codeverse3d.judges.prompt_builder import (
    PROVENANCE_ELICITATION,
    build_judge_messages,
)
from codeverse3d.judges.rubrics import load_rubric
from codeverse3d.judges.vlm_judge import SLICE_LABELS, VlmJudge
from tests.judges.conftest import ACCEPTANCE, FakeChatModel, good_reply, make_renders, make_spec

R = load_rubric("static_object")
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
    VlmJudge("static_object", model_id="fake:fake-1", chat_model=model, cache_dir=cache_dir).judge(inp)
    return model.requests[0]


def labels(req) -> list[str]:
    return [p.label for m in req.messages for p in m.parts if isinstance(p, ImagePart)]


def text_of(req) -> str:
    return "\n".join(p.text for m in req.messages for p in m.parts if isinstance(p, TextPart))


# --------------------------------------------------------------------- clean = byte-identical, dirty = slices appended
def test_slices_ride_only_on_a_connectivity_error(tmp_path, cache_dir):
    """The invariant: glb present + knob on, but no gate ERROR → the exact pre-D48 payload.  Any
    connectivity ERROR (a penetration pair, or a floating part alone) appends the slices after the crops."""
    renders, glb = make_renders(tmp_path / "r"), two_box_glb(tmp_path / "o.glb")
    inp = make_input(renders, clean_gates(), glb)
    req = judge_request(inp, cache_dir)
    system, messages = build_judge_messages(inp, R, cache_dir=cache_dir)  # the baseline builder
    assert req.system == system
    assert [m.model_dump() for m in req.messages] == [m.model_dump() for m in messages]
    assert PROVENANCE_ELICITATION not in req.system
    assert "cross-section" not in text_of(req)

    req = judge_request(make_input(renders, dirty_gates(), glb), cache_dir)
    # placement: montage, 2 detail crops, THEN the two slices — never prepended
    lbls = labels(req)
    assert lbls[-2:] == [SLICE_LABELS["front_back"], SLICE_LABELS["left_right"]]
    assert "DETAIL CROP" in lbls[-3] and len(lbls) == 5  # 1 montage + 2 crops + 2 slices

    assert "cross-section slice(s) show the interior" in text_of(req)
    assert req.system.count(PROVENANCE_ELICITATION) == 1

    # the slice pngs and their manifest landed in the judge cache, hatching the gate pair
    from codeverse3d.spatial.sections import SliceManifest
    manifests = list(cache_dir.glob("slices_*/manifest.json"))
    assert len(manifests) == 1
    man = SliceManifest.model_validate_json(manifests[0].read_text())
    assert man.error_pairs == [("A", "B")]
    assert all({p.a, p.b} == {"A", "B"} for s in man.rendered() for p in s.hatched_pairs)
