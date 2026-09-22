from pathlib import Path

from PIL import Image

from codeverse3d.contracts.artifacts import (
    GateFinding,
    GateReport,
    ImprovementItem,
    Judgment,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse3d.contracts.chat import ImagePart, TextPart
from codeverse3d.judges.prompt_builder import (
    TEXT_BUDGET_CHARS,
    build_judge_messages,
    build_system_prompt,
    montage_image_parts,
    prepare_image,
    view_az_el,
)
from codeverse3d.judges.rubrics import load_rubric
from tests.judges.conftest import draw_chair, make_renders

R = load_rubric("static_object_v1")


def _parts(msgs):
    return msgs[0].parts


def _images(msgs):
    return [p for p in _parts(msgs) if isinstance(p, ImagePart)]


def test_the_scene_rig_rule_names_every_harness_camera_and_no_authored_prefix():
    """Authored cameras carry the plan's names (``cam_i`` is only scene_host's fallback for an
    unnamed one) and the eye-level rig is the harness's too: the judge was told authored views
    are ``cam_*`` and nothing about ``eye_*``."""
    from codeverse3d.conventions import SCENE_VIEWS
    from codeverse3d.judges.prompt_builder import RIG_RULES

    rule = RIG_RULES["scene_cams"]
    assert "cam_*" not in rule
    assert all(f"{v.name.split('_')[0]}_*" in rule for v in SCENE_VIEWS)


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
    assert imgs[0].label.startswith("MONTAGE 1/1 — SHADED views: top-left = front_right_high · az 45° el 30°")
    assert "bottom-left = top · az 0° el 90°, bottom-right = front · az 0° el 0°" in imgs[0].label
    assert imgs[1].label.startswith("DETAIL CROP — centre of front_right_high")
    assert imgs[2].label.startswith("DETAIL CROP — ground-contact band of front")  # lowest elevation (0°)
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
    """The clay montage rides the pipeline's OWN cameras (OBJECT_CLAY_VIEWS) and labels
    them — the clay 'top' is el 88 while the rig's shaded 'top' is el 90 (D47)."""
    from codeverse3d.conventions import OBJECT_CLAY_VIEWS

    clay = []
    for v in OBJECT_CLAY_VIEWS:
        p = draw_chair(tmp_path / f"clay_{v.name}.png", color=(180, 180, 180))
        clay.append(RenderView(name=v.name, path=str(p), mode="clay"))
    _, msgs = build_judge_messages(judge_input, R, cache_dir=cache_dir, geometry_views=RenderSet(views=clay))
    labels = [p.label for p in _images(msgs)]
    assert labels[0].startswith("MONTAGE 1/2 — SHADED views") and labels[1].startswith("MONTAGE 2/2 — GEOMETRY-ONLY views (clay")
    assert "front_right_34 · az 35° el 22° · clay" in labels[1]
    assert "top · az 0° el 88° · clay" in labels[1]
    assert "top · az 0° el 90°" in labels[0]
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
    from codeverse3d.contracts.common import Language, Track
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
    assert view_az_el(RenderView(name="top", path="x")) == (0.0, 90.0)
    # a geometry-mode tile answers from the clay rig's own cameras (el 88, not 90)
    assert view_az_el(RenderView(name="top", path="x", mode="clay")) == (0.0, 88.0)
    assert view_az_el(RenderView(name="front_right_34", path="x", mode="normals")) == (35.0, 22.0)
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
    from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
    from codeverse3d.judges.prompt_builder import gates_section

    ok = GateReport(gate="connectivity", passed=True, findings=[
        GateFinding(gate="connectivity", severity=Severity.INFO, target="", message="all 9 parts are connected")])
    text = gates_section([ok])
    assert "CONNECTIVITY PASSED" in text and "not daylight" in text and "Do NOT report any part as floating" in text

    failed = GateReport(gate="connectivity", passed=False, findings=[
        GateFinding(gate="connectivity", severity=Severity.ERROR, target="Seat", message="part 'Seat' floats 12 mm above 'Leg'")])
    assert "CONNECTIVITY PASSED" not in gates_section([failed]), "a real floating part is still reported as one"
    assert "CONNECTIVITY PASSED" not in gates_section([GateReport(gate="contract", passed=True)]), "only the contact gate earns it"


# ===================================================================== the contact ledger (2026-08-30)
#: the connectivity gate's ledger for a real p90 round — eval/bench/out/codex_tiers_v3/cells/
#: cmp_hard_violin/harness_codex_gpt-5.6-sol r00 (16 parts, 26 contacts), re-run through
#: ``check_connectivity(planned_edges=planned_joins(plan, measurement))`` on 2026-08-30.
#: 23 overlaps, one open planned join (the tailpiece never reaches the endpin button).
LEDGER_VIOLIN = {"parts": ["Chinrest", "Strings", "Tailpiece", "Bridge", "TuningPegs_3", "TuningPegs_2", "TuningPegs_1", "TuningPegs_0", "PegboxAndScroll", "Nut", "Fingerboard", "NeckRoot", "EndpinButton", "TopPlate", "RibStructure", "BackPlate"], "contact_gap_mm": 2.0, "contacts": [["Chinrest", "TopPlate", 0.0], ["Chinrest", "RibStructure", 0.0], ["Strings", "Tailpiece", 0.0], ["Strings", "Bridge", 0.8], ["Strings", "TuningPegs_2", 0.0], ["Strings", "TuningPegs_1", 0.6], ["Strings", "PegboxAndScroll", 0.0], ["Strings", "Nut", 0.0], ["Strings", "NeckRoot", 0.0], ["Tailpiece", "TopPlate", 0.0], ["Bridge", "TopPlate", 0.0], ["TuningPegs_3", "PegboxAndScroll", 0.0], ["TuningPegs_2", "PegboxAndScroll", 0.0], ["TuningPegs_1", "PegboxAndScroll", 0.0], ["TuningPegs_0", "PegboxAndScroll", 0.0], ["PegboxAndScroll", "Nut", 0.0], ["PegboxAndScroll", "Fingerboard", 0.0], ["PegboxAndScroll", "NeckRoot", 0.0], ["Nut", "Fingerboard", 0.0], ["Nut", "NeckRoot", 0.0], ["Fingerboard", "NeckRoot", 0.0], ["NeckRoot", "TopPlate", 0.0], ["NeckRoot", "RibStructure", 0.0], ["EndpinButton", "RibStructure", 0.0], ["TopPlate", "RibStructure", 0.0], ["RibStructure", "BackPlate", 0.0]], "overlaps": [["Chinrest", "TopPlate", 1.8, 0.08], ["Strings", "Tailpiece", 3.1, 0.02], ["Strings", "TuningPegs_2", 2.4, 0.02], ["Strings", "PegboxAndScroll", 5.1, 0.0], ["Strings", "Nut", 0.4, 0.02], ["Strings", "NeckRoot", 1.5, 0.02], ["Tailpiece", "TopPlate", 1.7, 0.23], ["Bridge", "TopPlate", 1.5, 0.36], ["TuningPegs_3", "PegboxAndScroll", 6.6, 0.17], ["TuningPegs_2", "PegboxAndScroll", 9.0, 0.33], ["TuningPegs_1", "PegboxAndScroll", 8.3, 0.27], ["TuningPegs_0", "PegboxAndScroll", 3.9, 0.19], ["PegboxAndScroll", "Nut", 2.5, 0.11], ["PegboxAndScroll", "Fingerboard", 2.7, 0.1], ["PegboxAndScroll", "NeckRoot", 4.1, 0.2], ["Nut", "Fingerboard", 1.0, 0.23], ["Nut", "NeckRoot", 1.0, 0.07], ["Fingerboard", "NeckRoot", 8.4, 0.55], ["NeckRoot", "TopPlate", 1.0, 0.0], ["NeckRoot", "RibStructure", 0.1, 0.0], ["EndpinButton", "RibStructure", 3.0, 0.0], ["TopPlate", "RibStructure", 1.6, 0.05], ["RibStructure", "BackPlate", 2.5, 0.07]], "ground_gap_mm": {"Chinrest": 33.0, "Strings": 43.0, "Tailpiece": 37.0, "Bridge": 37.5, "TuningPegs_3": 55.0, "TuningPegs_2": 45.0, "TuningPegs_1": 45.0, "TuningPegs_0": 55.0, "PegboxAndScroll": 33.0, "Nut": 51.0, "Fingerboard": 44.2, "NeckRoot": 21.3, "EndpinButton": 14.0, "TopPlate": 31.0, "RibStructure": 3.0, "BackPlate": 0.0}, "planned": [["RibStructure", "BackPlate", 0.0, "contact"], ["TopPlate", "RibStructure", 0.0, "contact"], ["EndpinButton", "RibStructure", 0.0, "contact"], ["NeckRoot", "RibStructure", 0.0, "contact"], ["Fingerboard", "NeckRoot", 0.0, "contact"], ["Nut", "Fingerboard", 0.0, "contact"], ["PegboxAndScroll", "NeckRoot", 0.0, "contact"], ["TuningPegs_3", "PegboxAndScroll", 0.0, "contact"], ["TuningPegs_2", "PegboxAndScroll", 0.0, "contact"], ["TuningPegs_1", "PegboxAndScroll", 0.0, "contact"], ["TuningPegs_0", "PegboxAndScroll", 0.0, "contact"], ["Bridge", "TopPlate", 0.0, "contact"], ["Tailpiece", "EndpinButton", 24.9, "open"], ["Strings", "Bridge", 0.8, "contact"], ["Chinrest", "TopPlate", 0.0, "contact"]], "planned_unresolved": []}


def _pen(a: str, b: str, depth_mm: float, through: float, severity: Severity = Severity.WARN) -> GateFinding:
    return GateFinding(gate="connectivity", severity=severity, target=a,
                       message=f"'{a}' and '{b}' interpenetrate by ≈{depth_mm:.1f} mm (4% of surface samples inside, 30% where they meet)",
                       data={"kind": "penetration", "other": b, "depth_m": depth_mm / 1000, "through_ratio": through})


def _ledger_report(ledger: dict | None = None, *, passed: bool = True, extra: list[GateFinding] = ()) -> GateReport:
    d = ledger or LEDGER_VIOLIN
    findings = [_pen(a, b, depth, through) for a, b, depth, through in d["overlaps"] if depth > 2.0] + list(extra)
    findings.append(GateFinding(gate="connectivity", severity=Severity.INFO,
                                message=f"all {len(d['parts'])} parts are connected ({len(d['contacts'])} contacts, gap ≤ 2 mm)"))
    findings.append(GateFinding(gate="connectivity", severity=Severity.INFO, target="",
                                message=f"contact ledger: {len(d['parts'])} parts, {len(d['contacts'])} contacts, {len(d['overlaps'])} overlaps", data={"kind": "ledger", **d}))
    return GateReport(gate="connectivity", passed=passed, findings=findings)


def _ledger_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines()
                     if line.startswith(("MEASURED", "- ", "connectivity measured", "CONNECTIVITY PASSED")))


CONTRACT_FAIL = GateReport(gate="contract", passed=False, findings=[
    GateFinding(gate="contract", severity=Severity.ERROR, target="Bridge", message="'Bridge' bbox extents off by 0.03 m"),
    GateFinding(gate="contract", severity=Severity.WARN, target="Nut", message="'Nut' centre off by 4 mm")])


def test_ledger_turns_penetration_warn_prose_into_one_measured_line():
    """The audit (eval/docs/EVAL.md §6): 110 of 237 interpenetration flags cited only WARNs the rubric
    says to ignore; same images with the gate text removed flipped 13 of 24.  The eleven
    'interpenetrate by' sentences of this round become one measured line, ERRORs stay."""
    from codeverse3d.judges.prompt_builder import gates_section

    rep = _ledger_report()
    assert sum("interpenetrate" in f.message for f in rep.findings) == 13  # one WARN sentence per overlap over the weld line
    text = gates_section([rep, CONTRACT_FAIL, GateReport(gate="budget", passed=True)])
    assert "interpenetrate" not in text
    assert ("connectivity measured 23 mating overlaps (deepest weld 9.0 mm TuningPegs_2/PegboxAndScroll, through-ratio 0.33); "
            "weld allowance 2 mm, ERROR line 10 mm; 23 weld(s) under it are NOT the interpenetration defect.") in text
    # ≥ 0.5 of the way through: named as a measurement, left to the geometry montage
    assert "Measured, not a defect claim: Fingerboard/NeckRoot reaches 55% of the way to its partner's mid-plane (8.4 mm)" in text
    assert "errors (1):" in text and "- contract [Bridge]: 'Bridge' bbox extents off by 0.03 m" in text
    assert "warnings (1), grouped by kind:" in text and "- contract ×1: [Nut] 'Nut' centre off by 4 mm" in text
    assert "MEASURED STRUCTURE (connectivity gate, exact mesh-to-mesh distances): 16 parts, 26 contacts (gap ≤ 2 mm), 0 floating, 23 overlapping pairs." in text
    assert "PLANNED JOINS (the plan's attach_to pairs, measured; contact = gap ≤ 2 mm): 14/15 in contact. OPEN: Tailpiece→EndpinButton 24.9 mm." in text
    assert "Strings→Bridge 0.8," in text and "TuningPegs_3→PegboxAndScroll," in text
    # a planned contact is not repeated in the adjacency; the unplanned ones are
    assert "- other contacts, not in the plan (gap in mm where not 0): Chinrest: RibStructure · Strings: Tailpiece, TuningPegs_2, TuningPegs_1 0.6," in text
    assert "TuningPegs_3: PegboxAndScroll" not in text
    assert "- ground: lowest point 0.0 mm (BackPlate) — floor contact; parts within 10 mm of the floor: RibStructure 3.0 mm" in text
    assert ("CONNECTIVITY PASSED: all 16 parts are in measured contact (26 contacts, gap <= 2 mm)." in text
            and "Do NOT report any part as floating" in text)
    # three overlaps reach 8 mm (the veto's line): no injunction against the interpenetration tick,
    # the measurement is named and the picture decides — the prompt and rubrics._rule_gates_clean agree
    assert "All 23 overlaps measured; 3 reach 8 mm or more (" in text
    assert "mark interpenetration only if a part VISIBLY passes through another in a render" in text
    assert "do NOT mark the interpenetration defect" not in text
    assert text.index("errors (1)") < text.index("MEASURED STRUCTURE") < text.index("connectivity measured") < text.index("warnings (1)")


def test_ledger_block_stays_near_500_tokens_on_the_p90_round():
    """chars/4 is this module's own token convention (``TEXT_BUDGET_CHARS = 24_000 ≈ 6k``)."""
    from codeverse3d.judges.prompt_builder import (
        LEDGER_MAX_CONTACTS,
        LEDGER_MAX_JOINS,
        gates_section,
    )

    text = gates_section([_ledger_report(), GateReport(gate="contract", passed=True)])
    assert len(_ledger_lines(text)) / 4 <= 500
    # the clips: 40 unplanned contacts and 30 planned joins are cut with a count, not listed to the end
    big = dict(LEDGER_VIOLIN)
    big["parts"] = [f"Part{i}" for i in range(41)]
    big["contacts"] = [[f"Part{i}", f"Part{i + 1}", 0.0] for i in range(40)]
    big["planned"] = [[f"Part{i}", f"Part{i + 1}", 0.0, "contact"] for i in range(30)]
    big["ground_gap_mm"] = {f"Part{i}": 0.0 for i in range(41)}
    text = gates_section([_ledger_report(big)])
    assert f"… {30 - LEDGER_MAX_JOINS} more." in text
    assert "Part30: Part31" in text and "Part39: Part40" in text and " more\n" not in text  # the 10 unplanned contacts fit
    assert len(_ledger_lines(text)) / 4 <= 500
    big["planned"] = []
    text = gates_section([_ledger_report(big)])
    assert "- contacts (gap in mm where not 0): Part0: Part1 · " in text and f"· … {40 - LEDGER_MAX_CONTACTS} more" in text
    assert len(_ledger_lines(text)) / 4 <= 500


def test_ledger_error_pairs_are_errors_not_welds_and_a_failed_gate_earns_no_passed_paragraph():
    from codeverse3d.judges.prompt_builder import gates_section

    d = {"parts": ["Arm", "Post", "Seat", "Leg"], "contact_gap_mm": 2.0, "contacts": [["Arm", "Post", 0.0], ["Post", "Leg", 0.0]],
         "overlaps": [["Arm", "Post", 14.0, 1.2], ["Post", "Leg", 1.1, 0.05], ["Seat", "Leg", 0.3, 0.01]],
         "ground_gap_mm": {"Arm": 400.0, "Post": 0.0, "Seat": 412.0, "Leg": 0.0}, "planned": [["Seat", "Leg", 12.0, "open"]], "planned_unresolved": ["Cushion"]}
    floating = GateFinding(gate="connectivity", severity=Severity.ERROR, target="Seat",
                           message="part 'Seat' is floating: nearest supported part is 'Leg' at 12.0 mm", data={"kind": "floating", "nearest": "Leg", "gap_m": 0.012})
    rep = _ledger_report(d, passed=False, extra=[_pen("Arm", "Post", 14.0, 1.2, Severity.ERROR), floating])
    text = gates_section([rep])
    assert "errors (2):" in text and "- connectivity [Arm]: 'Arm' and 'Post' interpenetrate by ≈14.0 mm" in text and "[Seat]: part 'Seat' is floating" in text
    assert "4 parts, 2 contacts (gap ≤ 2 mm), 1 floating, 3 overlapping pairs." in text
    assert "1 pair(s) above it are listed under errors; 2 weld(s) under it are NOT the interpenetration defect." in text
    assert "(deepest weld 1.1 mm Post/Leg, through-ratio 0.05)" in text  # the ERROR pair is not the deepest weld
    assert "OPEN: Seat→Leg 12.0 mm." in text and "- plan parts not found in the mesh: Cushion" in text
    assert "CONNECTIVITY PASSED" not in text and "no errors or warnings" not in text


def test_ledger_ground_line_and_single_part_and_grouped_warnings():
    from codeverse3d.judges.prompt_builder import gates_section

    hover = dict(LEDGER_VIOLIN, parts=["Top", "Leg_0", "Leg_1", "Leg_2"], contacts=[], overlaps=[], planned=[],
                 ground_gap_mm={"Top": 700.0, "Leg_0": 3.1, "Leg_1": 3.1, "Leg_2": 0.3})
    text = gates_section([_ledger_report(hover)])
    # the corpus verdict wrote "legs end above the ground plane" on 0.3 mm: the number is printed, the far parts are not
    assert "- ground: lowest point 0.3 mm (Leg_2) — floor contact; parts within 10 mm of the floor: Leg_0 3.1 mm, Leg_1 3.1 mm" in text
    assert "Top 700" not in text
    hover["ground_gap_mm"] = {"Top": 700.0, "Leg_0": 3.1, "Leg_1": 3.1, "Leg_2": 3.1}
    assert "lowest point 3.1 mm above the floor (Leg_0) — nothing touches it; parts within 10 mm of the floor: Leg_0 3.1 mm, Leg_1 3.1 mm, Leg_2 3.1 mm" in gates_section([_ledger_report(hover)])
    hover["ground_gap_mm"] = {"Top": 700.0, "Leg_0": -2.0, "Leg_1": 0.2, "Leg_2": 0.2}
    assert "lowest point 2.0 mm below the floor (Leg_0); every other near-floor part is within 0.5 mm of the floor" in gates_section([_ledger_report(hover)])
    # one part has nothing to be connected to: no CONNECTIVITY PASSED paragraph
    single = dict(LEDGER_VIOLIN, parts=["Body"], contacts=[], overlaps=[], planned=[], ground_gap_mm={"Body": 0.0})
    text = gates_section([_ledger_report(single)])
    assert "1 part, 0 contacts" in text and "CONNECTIVITY PASSED" not in text
    # the remaining WARNs group by (gate, kind) instead of cutting the list at 8
    islands = [GateFinding(gate="connectivity", severity=Severity.WARN, target=f"P{i}", message=f"part 'P{i}' contains 1 tiny disconnected island(s)",
                           data={"islands": 2, "tiny_islands": 1}) for i in range(2)]
    contract = GateReport(gate="contract", passed=True, findings=[
        GateFinding(gate="contract", severity=Severity.WARN, target=f"C{i}", message=f"'C{i}' centre off by {i} mm", data={"kind": "center"}) for i in range(10)])
    text = gates_section([_ledger_report(extra=islands), contract])
    assert "warnings (12), grouped by kind:" in text
    assert "- connectivity ×2: [P0] part 'P0' contains 1 tiny disconnected island(s); [P1] part 'P1'" in text
    assert "- contract/center ×10: [C0] 'C0' centre off by 0 mm;" in text and "[C7] 'C7' centre off by 7 mm; … 2 more" in text
    assert "[C8]" not in text


def test_gates_section_without_a_ledger_is_byte_identical_to_the_pre_ledger_text():
    """A recorded round has no ledger finding: ``3dcode judge <old-slug>`` must build the exact text
    the stored verdict saw.  Expected strings were captured from ``gates_section`` at 4ddde32,
    before the ledger path existed (scratch capture_baseline.py, 2026-08-30)."""
    from codeverse3d.judges.prompt_builder import gates_section

    C = "connectivity"
    def F(sev, msg, target=None, gate=C, data=None):
        return GateFinding(gate=gate, severity=sev, target=target, message=msg, data=data or {})
    assert gates_section([]) == 'GATE FINDINGS: (no gates run)'
    passed = [GateReport(gate=C, passed=True, findings=[F(Severity.INFO, "all 9 parts are connected (12 contacts, gap ≤ 2 mm)")])]
    assert gates_section(passed) == 'GATE FINDINGS (deterministic; treat as facts): connectivity=pass\nno errors or warnings — parts are connected and contracts hold.\nCONNECTIVITY PASSED: every part is in measured contact with its neighbour (gap <= 2 mm). A dark seam or shadow line where two parts meet is contact, not daylight. Do NOT report any part as floating, hovering or disconnected, and do not mark the floating-part defect present; if a joint looks visually ugly, say so under craftsmanship.'
    mixed = [
        GateReport(gate=C, passed=True, findings=[
            F(Severity.WARN, "'Strings' and 'Tailpiece' interpenetrate by ≈2.5 mm (2% of surface samples inside, 30% where they meet)", "Strings",
              data={"kind": "penetration", "other": "Tailpiece", "depth_m": 0.0025, "through_ratio": 0.1}),
            F(Severity.WARN, "part 'Seat' contains 2 tiny disconnected island(s) (< 5% of the part size) — stray geometry", "Seat", data={"islands": 3, "tiny_islands": 2}),
            F(Severity.INFO, "all 16 parts are connected (26 contacts, gap ≤ 2 mm)")]),
        GateReport(gate="contract", passed=False, findings=[F(Severity.ERROR, "'Bridge' bbox extents off by 0.03 m", "Bridge", gate="contract"),
                                                            F(Severity.WARN, "'Nut' centre off by 4 mm", "Nut", gate="contract")]),
        GateReport(gate="budget", passed=True)]
    assert gates_section(mixed) == "GATE FINDINGS (deterministic; treat as facts): connectivity=pass, contract=FAIL, budget=pass\nerrors (1):\n- contract [Bridge]: 'Bridge' bbox extents off by 0.03 m\nwarnings (3):\n- connectivity [Strings]: 'Strings' and 'Tailpiece' interpenetrate by ≈2.5 mm (2% of surface samples inside, 30% where they meet)\n- connectivity [Seat]: part 'Seat' contains 2 tiny disconnected island(s) (< 5% of the part size) — stray geometry\n- contract [Nut]: 'Nut' centre off by 4 mm\nCONNECTIVITY PASSED: every part is in measured contact with its neighbour (gap <= 2 mm). A dark seam or shadow line where two parts meet is contact, not daylight. Do NOT report any part as floating, hovering or disconnected, and do not mark the floating-part defect present; if a joint looks visually ugly, say so under craftsmanship."
    failed = [GateReport(gate=C, passed=False, findings=[
        F(Severity.ERROR, "part 'Seat' is floating: nearest supported part is 'Leg' at 12.0 mm", "Seat", data={"kind": "floating", "nearest": "Leg", "gap_m": 0.012}),
        F(Severity.ERROR, "'Arm' and 'Post' interpenetrate by ≈14.0 mm (30% of surface samples inside, 80% where they meet) — 'Arm' reaches 120% of the way through 'Post' (10 mm)", "Arm",
          data={"kind": "penetration", "other": "Post", "depth_m": 0.014, "through_ratio": 1.2})])]
    assert gates_section(failed) == "GATE FINDINGS (deterministic; treat as facts): connectivity=FAIL\nerrors (2):\n- connectivity [Seat]: part 'Seat' is floating: nearest supported part is 'Leg' at 12.0 mm\n- connectivity [Arm]: 'Arm' and 'Post' interpenetrate by ≈14.0 mm (30% of surface samples inside, 80% where they meet) — 'Arm' reaches 120% of the way through 'Post' (10 mm)"
    many = [GateReport(gate=C, passed=False, findings=[F(Severity.ERROR, f"error {i}", f"P{i}") for i in range(14)] + [F(Severity.WARN, f"warn {i}", f"W{i}") for i in range(11)])]
    assert gates_section(many) == 'GATE FINDINGS (deterministic; treat as facts): connectivity=FAIL\nerrors (14):\n- connectivity [P0]: error 0\n- connectivity [P1]: error 1\n- connectivity [P2]: error 2\n- connectivity [P3]: error 3\n- connectivity [P4]: error 4\n- connectivity [P5]: error 5\n- connectivity [P6]: error 6\n- connectivity [P7]: error 7\n- connectivity [P8]: error 8\n- connectivity [P9]: error 9\n- connectivity [P10]: error 10\n- connectivity [P11]: error 11\n- … 2 more errors\nwarnings (11):\n- connectivity [W0]: warn 0\n- connectivity [W1]: warn 1\n- connectivity [W2]: warn 2\n- connectivity [W3]: warn 3\n- connectivity [W4]: warn 4\n- connectivity [W5]: warn 5\n- connectivity [W6]: warn 6\n- connectivity [W7]: warn 7\n- … 3 more warnings'
    assert gates_section(many, max_errors=3, max_warns=2) == 'GATE FINDINGS (deterministic; treat as facts): connectivity=FAIL\nerrors (14):\n- connectivity [P0]: error 0\n- connectivity [P1]: error 1\n- connectivity [P2]: error 2\n- … 11 more errors\nwarnings (11):\n- connectivity [W0]: warn 0\n- connectivity [W1]: warn 1\n- … 9 more warnings'


def test_judge_prompt_hash_does_not_cover_the_per_run_gate_text():
    """The ledger block rides on v1's hash: ``judge_prompt_hash`` covers the system prompt, the
    rig rules and the wire schema only, so a paired re-judge, not the hash, is what measures it."""
    from codeverse3d.judges.prompt_builder import judge_prompt_hash

    h = judge_prompt_hash(R)
    import codeverse3d.judges.prompt_builder as pb
    original = pb.gates_section
    try:
        pb.gates_section = lambda gates, **kw: "NOT THE SAME TEXT"
        assert judge_prompt_hash(R) == h
    finally:
        pb.gates_section = original
