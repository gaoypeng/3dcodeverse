from pathlib import Path

from PIL import Image

from codeverse3d.contracts.artifacts import (
    GateFinding,
    GateReport,
    Severity,
)
from codeverse3d.contracts.chat import ImagePart
from codeverse3d.judges.prompt_builder import (
    build_judge_messages,
)
from codeverse3d.judges.rubrics import load_rubric

R = load_rubric("static_object")


def _parts(msgs):
    return msgs[0].parts


def _images(msgs):
    return [p for p in _parts(msgs) if isinstance(p, ImagePart)]


def test_montage_images_are_cached_at_the_payload_size(judge_input, cache_dir):
    """The labels and text are pinned by tests/prompts/manifest.json; the image bytes are not."""
    _, msgs = build_judge_messages(judge_input, R, cache_dir=cache_dir)
    imgs = _images(msgs)
    assert len(imgs) == 3  # 4 shaded views → ONE 2×2 montage + centre crop + ground-contact crop
    for ip in imgs:
        assert Path(ip.path).is_file() and Path(ip.path).parent == cache_dir
    with Image.open(imgs[0].path) as im:
        assert 900 <= im.size[0] <= 1024 and im.size[1] > im.size[0]  # 2×2 grid + strip


# ===================================================================== the contact ledger (2026-08-30)
#: a real p90 round's connectivity ledger (16 parts, 26 contacts, 23 overlaps, one open planned join)
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
    """eval/docs/EVAL.md §6: the WARN 'interpenetrate by' sentences become one measured line; ERRORs stay."""
    from codeverse3d.judges.prompt_builder import gates_section

    rep = _ledger_report()
    assert sum("interpenetrate" in f.message for f in rep.findings) == 13  # one WARN sentence per overlap over the weld line
    text = gates_section([rep, CONTRACT_FAIL, GateReport(gate="budget", passed=True)])
    assert "interpenetrate" not in text
    assert "connectivity measured 23 mating overlaps (deepest weld 9.0 mm TuningPegs_2/PegboxAndScroll" in text
    assert "errors (1):" in text and "warnings (1), grouped by kind:" in text
    assert "MEASURED STRUCTURE (connectivity gate, exact mesh-to-mesh distances): 16 parts, 26 contacts (gap ≤ 2 mm), 0 floating, 23 overlapping pairs." in text
    assert "14/15 in contact. OPEN: Tailpiece→EndpinButton 24.9 mm." in text
    assert "TuningPegs_3: PegboxAndScroll" not in text  # a planned contact is not repeated in the adjacency
    assert "CONNECTIVITY PASSED: all 16 parts are in measured contact" in text
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
