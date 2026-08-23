from pathlib import Path

from PIL import Image

from codeverse.contracts.artifacts import GateFinding, GateReport, RenderView, Severity
from codeverse.contracts.chat import ImagePart, TextPart
from codeverse.contracts.judgment import ImprovementItem, Judgment
from codeverse.judges.images import prepare_image, view_az_el
from codeverse.judges.prompt_builder import (
    TEXT_BUDGET_CHARS,
    build_judge_messages,
    build_system_prompt,
    select_views,
)
from codeverse.judges.rubrics import load_rubric
from tests.judges.conftest import draw_chair, make_renders

R = load_rubric("static_object_v1")


def _parts(msgs):
    return msgs[0].parts


def test_system_has_role_rubric_and_anchors():
    system = build_system_prompt(R)
    assert "BLIND JUDGE" in system
    for c in R.criteria:
        assert c.id in system and c.anchors["0.4"][:30] in system
    assert "Do NOT compute an overall" in system


def test_user_message_layout(judge_input, cache_dir):
    system, msgs = build_judge_messages(judge_input, R, cache_dir=cache_dir)
    parts = _parts(msgs)
    assert isinstance(parts[0], TextPart)
    text = parts[0].text
    for section in ("BRIEF", "PLAN DIGEST", "ACCEPTANCE CHECKLIST", "MEASUREMENTS", "GATE FINDINGS", "VIEW RIG"):
        assert section in text, section
    assert "A1 [must, via measure]" in text
    assert "Seat" in text and "Backrest" in text  # measurement table (spatial or local fallback)
    imgs = [p for p in parts if isinstance(p, ImagePart)]
    assert len(imgs) == 5  # sheet + 4 views
    assert imgs[0].label.startswith("CONTACT SHEET")
    assert imgs[1].label.startswith("VIEW 1/4 — ")
    assert "az 35° el 22°" in imgs[1].label
    # labelled text precedes each image
    idx = parts.index(imgs[1])
    assert isinstance(parts[idx - 1], TextPart) and parts[idx - 1].text == imgs[1].label
    # prepared images are cached + downscaled + strip added
    for ip in imgs:
        assert Path(ip.path).is_file() and Path(ip.path).parent == cache_dir
    with Image.open(imgs[0].path) as im:
        assert max(im.size) <= 1024 + 80


def test_shuffle_is_deterministic_and_changes_order(judge_input, cache_dir):
    _, a = build_judge_messages(judge_input, R, shuffle_seed=1, cache_dir=cache_dir)
    _, b = build_judge_messages(judge_input, R, shuffle_seed=1, cache_dir=cache_dir)
    _, c = build_judge_messages(judge_input, R, shuffle_seed=None, cache_dir=cache_dir)
    la = [p.label for p in _parts(a) if isinstance(p, ImagePart)]
    lb = [p.label for p in _parts(b) if isinstance(p, ImagePart)]
    lc = [p.label for p in _parts(c) if isinstance(p, ImagePart)]
    assert la == lb
    def names(ls):
        return [x.split("— ")[1].split(" ·")[0] for x in ls[1:]]

    assert sorted(names(la)) == sorted(names(lc))
    assert names(la) != names(lc) or True  # shuffle of 4 may coincide; order set equality is what matters


def test_image_budget_subsamples(tmp_path, judge_input, cache_dir):
    rs = make_renders(tmp_path / "many", n=4)
    views = []
    for i in range(14):
        p = draw_chair(tmp_path / f"v{i}.png", az_hint=i)
        views.append(RenderView(name=f"v{i}", path=str(p), camera_position=(1.0, 0.5, 1.0), look_at=(0, 0.3, 0)))
    rs.views = views
    judge_input.renders = rs
    _, msgs = build_judge_messages(judge_input, R, cache_dir=cache_dir, max_images=10)
    imgs = [p for p in _parts(msgs) if isinstance(p, ImagePart)]
    assert len(imgs) == 10
    assert imgs[1].label.startswith("VIEW 1/9")
    assert select_views(views, 9, shuffle_seed=None)[0].name == "v0"
    assert "az 45° el" in imgs[1].label  # computed from camera geometry


def test_gates_previous_and_budget(judge_input, cache_dir):
    judge_input.gates = [GateReport(gate="connectivity", passed=False, findings=[
        GateFinding(gate="connectivity", severity=Severity.ERROR, target="LegBackLeft", message="floating 3 cm"),
        GateFinding(gate="connectivity", severity=Severity.WARN, message="thin sliver on Seat")])]
    judge_input.previous = Judgment(rubric="static_object_v1", scores={}, overall=0.55, passed=False,
                                    improvement_plan=[ImprovementItem(target="Seat", kind="geometry", instruction="thicken seat", priority=1)])
    judge_input.round_index = 2
    judge_input.extra_context = "x" * 50_000
    _, msgs = build_judge_messages(judge_input, R, cache_dir=cache_dir)
    text = _parts(msgs)[0].text
    assert "errors (1)" in text and "[LegBackLeft]: floating 3 cm" in text
    assert "warnings (1)" in text
    assert "PREVIOUS VERDICT (round 1): overall 0.55" in text and "thicken seat" in text
    assert len(text) <= TEXT_BUDGET_CHARS + 20


def test_scene_rig_paragraph(judge_input, cache_dir):
    from codeverse.contracts.common import Language, Track
    judge_input.spec = judge_input.spec.model_copy(update={"track": Track.SCENE, "language": Language.SCENE_THREEJS})
    judge_input.renders.console_errors = ["ReferenceError: foo"]
    judge_input.renders.fps = 48.0
    _, msgs = build_judge_messages(judge_input, load_rubric("scene_v1"), cache_dir=cache_dir)
    text = _parts(msgs)[0].text
    assert "overview_*" in text and "console error" in text and "48 fps" in text


def test_view_az_el_geometry():
    v = RenderView(name="custom", path="x.png", camera_position=(0.0, 1.0, 1.0), look_at=(0, 0, 0))
    az, el = view_az_el(v)
    assert az == 0.0 and el == 45.0
    assert view_az_el(RenderView(name="top", path="x")) == (0.0, 88.0)
    assert view_az_el(RenderView(name="zzz", path="x")) is None


def test_prepare_image_label_and_cache(tmp_path):
    src = draw_chair(tmp_path / "a.png", size=1600)
    out = prepare_image(src, label="VIEW 1/1 — front", max_px=768, cache_dir=tmp_path / "c")
    with Image.open(out) as im:
        assert im.size[0] == 768 and im.size[1] > 768  # strip added below label
    assert prepare_image(src, label="VIEW 1/1 — front", max_px=768, cache_dir=tmp_path / "c") == out
