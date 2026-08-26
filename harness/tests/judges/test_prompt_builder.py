from pathlib import Path

from PIL import Image

from codeverse.contracts.artifacts import GateFinding, GateReport, RenderSet, RenderView, Severity
from codeverse.contracts.chat import ImagePart, TextPart
from codeverse.contracts.judgment import ImprovementItem, Judgment
from codeverse.judges.images import prepare_image, view_az_el
from codeverse.judges.prompt_builder import (
    TEXT_BUDGET_CHARS,
    build_judge_messages,
    build_system_prompt,
    montage_image_parts,
)
from codeverse.judges.rubrics import load_rubric
from tests.judges.conftest import draw_chair, make_renders

R = load_rubric("static_object_v1")


def _parts(msgs):
    return msgs[0].parts


def _images(msgs):
    return [p for p in _parts(msgs) if isinstance(p, ImagePart)]


def test_system_has_role_rubric_anchors_and_defects():
    system = build_system_prompt(R)
    assert "BLIND JUDGE" in system
    for c in R.criteria:
        assert c.id in system and c.anchors["0.4"][:30] in system
    assert "Do NOT compute an overall" in system
    assert "DEFECT CHECKLIST" in system
    for d in R.defects:
        assert f"- {d.id}:" in system
    assert "[-0.10, cap 0.60]" in system  # floating_part penalty + cap shown


def test_user_message_layout(judge_input, cache_dir):
    system, msgs = build_judge_messages(judge_input, R, cache_dir=cache_dir)
    parts = _parts(msgs)
    assert isinstance(parts[0], TextPart)
    text = parts[0].text
    for section in ("BRIEF", "PLAN DIGEST", "ACCEPTANCE CHECKLIST", "MEASUREMENTS", "GATE FINDINGS", "VIEW RIG"):
        assert section in text, section
    assert "A1 [must, via measure]" in text
    assert "Seat" in text and "Backrest" in text  # measurement table (spatial or local fallback)
    imgs = _images(msgs)
    # 4 shaded views → ONE 2×2 montage + centre crop + ground-contact crop
    assert len(imgs) == 3
    assert imgs[0].label.startswith("MONTAGE 1/1 — SHADED views: top-left = front_right_34 · az 35° el 22°")
    assert "bottom-left = top · az 0° el 88°, bottom-right = front · az 0° el 8°" in imgs[0].label
    assert imgs[1].label.startswith("DETAIL CROP — centre of front_right_34")
    assert imgs[2].label.startswith("DETAIL CROP — ground-contact band of front")  # lowest elevation (8°)
    # labelled text precedes each image; the rig paragraph lists images in send order
    idx = parts.index(imgs[0])
    assert isinstance(parts[idx - 1], TextPart) and parts[idx - 1].text == imgs[0].label
    assert "- image 1: SHADED views" in text and "- detail crop 2: ground-contact band" in text
    assert "MONTAGE k <position> (<view name>)" in text
    for ip in imgs:
        assert Path(ip.path).is_file() and Path(ip.path).parent == cache_dir
    with Image.open(imgs[0].path) as im:
        assert 900 <= im.size[0] <= 1024 and im.size[1] > im.size[0]  # 2×2 grid + strip


def test_shuffle_is_deterministic_and_changes_order(tmp_path, judge_input, cache_dir):
    rs = make_renders(tmp_path / "eight", n=4)
    extra = []
    for name in ("right", "back", "left", "low_front_left"):
        p = draw_chair(tmp_path / f"v_{name}.png", az_hint=1)
        extra.append(RenderView(name=name, path=str(p)))
    judge_input.renders = RenderSet(views=rs.views + extra)
    _, a = build_judge_messages(judge_input, R, shuffle_seed=1, cache_dir=cache_dir)
    _, b = build_judge_messages(judge_input, R, shuffle_seed=1, cache_dir=cache_dir)
    _, c = build_judge_messages(judge_input, R, shuffle_seed=None, cache_dir=cache_dir)
    la, lb, lc = ([p.label for p in _images(m)] for m in (a, b, c))
    assert la == lb
    assert len(lc) == 4  # 2 montages + 2 crops
    assert lc[0].startswith("MONTAGE 1/2 — SHADED views:") and lc[1].startswith("MONTAGE 2/2 — SHADED views (remaining)")
    # canonical order puts the 3/4 views first; the seeded order differs somewhere (montage or tile order)
    assert la != lc
    assert la[-2:] == lc[-2:]  # detail crops are never shuffled


def test_geometry_views_add_a_montage(tmp_path, judge_input, cache_dir):
    clay = []
    for v in judge_input.renders.views:
        p = draw_chair(tmp_path / f"clay_{v.name}.png", color=(180, 180, 180))
        clay.append(RenderView(name=v.name, path=str(p), mode="clay"))
    _, msgs = build_judge_messages(judge_input, R, cache_dir=cache_dir, geometry_views=RenderSet(views=clay))
    labels = [p.label for p in _images(msgs)]
    assert labels[0].startswith("MONTAGE 1/2 — SHADED views") and labels[1].startswith("MONTAGE 2/2 — GEOMETRY-ONLY views (clay")
    assert "front_right_34 · az 35° el 22° · clay" in labels[1]
    text = _parts(msgs)[0].text
    assert "GEOMETRY-ONLY montage shows the same object without materials" in text
    # embedded clay views (by RenderView.mode) are routed the same way
    judge_input.renders = RenderSet(views=judge_input.renders.views + clay)
    _, msgs2 = build_judge_messages(judge_input, R, cache_dir=cache_dir)
    assert any(p.label.startswith("MONTAGE 2/2 — GEOMETRY-ONLY") for p in _images(msgs2))


def test_montage_cap_and_crop_knobs(tmp_path, judge_input, cache_dir):
    views = []
    for i in range(14):
        p = draw_chair(tmp_path / f"v{i}.png", az_hint=i)
        views.append(RenderView(name=f"v{i}", path=str(p), camera_position=(1.0, 0.5, 1.0), look_at=(0, 0.3, 0)))
    judge_input.renders = RenderSet(views=views)
    pairs, montages = montage_image_parts(judge_input.renders, cache_dir=cache_dir, max_montages=3, detail_crops=0)
    assert len(pairs) == 3 and all(m.kind == "shaded" for m in montages)
    assert "top-left = v0 · az 45° el" in pairs[0][0]  # az/el computed from camera geometry
    pairs2, _ = montage_image_parts(judge_input.renders, cache_dir=cache_dir, max_montages=1, detail_crops=1)
    assert len(pairs2) == 2 and pairs2[1][0].startswith("DETAIL CROP — centre of v0")


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
    assert len(_images(msgs)) == 2  # montage + centre crop only (no ground band for scenes)


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


def test_a_passed_connectivity_gate_tells_the_judge_a_seam_is_not_daylight():
    """Measured 2026-08-26 (fancy_v1 gas_street_lamp, plan-pinned pair): the connectivity gate
    said 'all 9 parts connected, gap <= 2 mm'; the judge read the dark seam under the pedestal
    as 'floating in mid-air, a clear daylight gap' (CRITICAL) and scored structure_plausibility
    0.4 against 1.0 for the near-identical sibling.  A measured contact outranks a shadow."""
    from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
    from codeverse.judges.prompt_builder import gates_section

    ok = GateReport(gate="connectivity", passed=True, findings=[
        GateFinding(gate="connectivity", severity=Severity.INFO, target="", message="all 9 parts are connected")])
    text = gates_section([ok])
    assert "CONNECTIVITY PASSED" in text and "not daylight" in text and "Do NOT report any part as floating" in text

    failed = GateReport(gate="connectivity", passed=False, findings=[
        GateFinding(gate="connectivity", severity=Severity.ERROR, target="Seat", message="part 'Seat' floats 12 mm above 'Leg'")])
    assert "CONNECTIVITY PASSED" not in gates_section([failed]), "a real floating part is still reported as one"
    assert "CONNECTIVITY PASSED" not in gates_section([GateReport(gate="contract", passed=True)]), "only the contact gate earns it"
