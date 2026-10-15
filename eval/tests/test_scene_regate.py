"""``bench/scene_regate.py``: re-running a gate over recorded workspaces, pinned without a browser."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from bench.scene_regate import (
    _head,
    kind_diff,
    probe,
    recorded_errors,
    recorded_kinds,
    regate,
    report,
    report_kinds,
    run_language,
    scene_runs,
)
from codeverse3d.contracts.artifacts import GateFinding, GateReport
from codeverse3d.contracts.common import Language
from codeverse3d.spatial.scene_placement import placement_gate
from codeverse3d.workspace import Workspace


def _run(root: Path, name: str, *, track: str = "scene", with_src: bool = True, language: str | None = "scene_threejs",
         entry: str | None = None) -> Path:
    d = root / name / "run"
    (d / "src").mkdir(parents=True, exist_ok=True)
    if with_src:
        (d / "src" / (entry or ("scene.py" if language == "scene_blender" else "scene.js"))).write_text("# entry")
    spec = {"track": track, "id": name}
    if language:
        spec["language"] = language
    (d / "record.json").write_text(json.dumps({"spec": spec, "rounds": []}))
    return d


def test_regate_reads_the_stage_records_the_round_gate_reads(tmp_path: Path, monkeypatch) -> None:
    """The layouts envelope is opened (coastal_village's real density finding came back) and an
    asset the asset stage never built is not "missing": re-gating calls the round's own entry
    point, which reads both from ``stages/``."""
    import bench.scene_regate as sr

    run = _run(tmp_path, "a")
    plan = {"zones": [{"name": "VillageQuay", "contents": ["Bollard", "BronzeCenser"]}], "assets": []}
    (run / "record.json").write_text(json.dumps({"spec": {"track": "scene"}, "plan": plan, "rounds": []}))
    (run / "stages").mkdir()
    (run / "stages" / "layouts.json").write_text(json.dumps({"stage": "layouts", "inputs_hash": "abc", "result": {
        "VillageQuay": {"placements": [{"asset": "Bollard", "count": 43}], "ground_cover": 400}}}))
    (run / "stages" / "assets.json").write_text(json.dumps({"stage": "assets", "inputs_hash": "abc", "result": {
        "BronzeCenser": {"name": "BronzeCenser", "ok": False}}}))
    census = {"fog": {"type": "Fog"}, "background": "#aabbcc",
              "groups": [{"name": "VillageQuay", "instances": 179}],
              "placement": {"assets": [{"name": "Bollard", "zone": "VillageQuay", "ground_gap_m": 0.0,
                                        "support": "Ground"}], "pairs": []}}
    monkeypatch.setattr(sr, "probe", lambda ws, timeout_s, language: census)
    after = regate(run, timeout_s=1.0)["after"]
    assert not any("BronzeCenser" in m for m in after), after
    # the density finding is an INFO note since D106 (a count is not a requirement), so it is not
    # among the ERRORs regate lists; the round's own gate, which regate calls, still reads it
    report = placement_gate(Workspace(run), census, plan)
    notes = [f.message for f in report.findings if f.data.get("kind") == "density_note"]
    assert any("holds ~179 instances" in m and "443" in m for m in notes), [f.message for f in report.findings]
    assert not any("179" in m for m in after), after


def test_only_scene_runs_with_their_entry_file_are_re_gated(tmp_path: Path) -> None:
    """A run is probed through its language's entry (``ENTRY_FILE``): a scene_blender record wants
    ``src/scene.py``, a three.js one ``src/scene.js``; a record without a language is three.js."""
    _run(tmp_path, "scene_ok")
    _run(tmp_path, "an_object", track="static_object")
    _run(tmp_path, "scene_no_src", with_src=False)
    _run(tmp_path, "bpy_ok", language="scene_blender")
    _run(tmp_path, "bpy_wrong_entry", language="scene_blender", entry="scene.js")
    _run(tmp_path, "old_record", language=None)
    names = {p.parent.name for p in scene_runs(tmp_path)}
    assert names == {"scene_ok", "bpy_ok", "old_record"}, "a run whose entry is gone cannot be re-probed"
    assert run_language({"spec": {"language": "scene_blender"}}) is Language.SCENE_BLENDER
    assert run_language({"spec": {}}) is Language.SCENE_THREEJS


def test_the_errors_read_back_are_the_last_round_s(tmp_path: Path) -> None:
    run = _run(tmp_path, "c")
    record = {"rounds": [
        {"index": 0, "gates": [{"gate": "scene_placement", "passed": False, "findings": [
            {"severity": "error", "message": "round 0 defect", "data": {"kind": "sunken"}}]}]},
        {"index": 1, "gates": [{"gate": "scene_placement", "passed": False, "findings": [
            {"severity": "error", "message": "round 1 defect", "data": {"kind": "floating", "gap_m": 0.3}},
            {"severity": "error", "message": "round 1 defect two", "data": {"kind": "floating", "gap_m": 0.2}},
            {"severity": "error", "message": "an old finding without a kind"},
            {"severity": "warn", "message": "a warning is not an error", "data": {"kind": "unsupported"}}]}]},
    ]}
    (run / "record.json").write_text(json.dumps(record))
    assert recorded_errors(record) == ["round 1 defect", "round 1 defect two", "an old finding without a kind"]
    assert recorded_kinds(record) == Counter({"floating": 2, "untyped": 1})


def test_two_wordings_of_one_defect_match() -> None:
    """The diff is on the identifying head, so a reworded hint or a changed depth does
    not read as one finding gone and another arrived."""
    a = "BlackPine_5 is sunken 3.46 m into AtmosphereHaze — raise it by 3.46 m"
    b = "BlackPine_5 is sunken 3.46 m into AtmosphereHaze (8 columns)"
    assert _head(a) == _head(b)


def test_kinds_are_counted_not_matched_on_messages() -> None:
    """Per kind ``(recorded, now, gone, new)`` from typed ``data.kind`` on both sides."""
    rep = GateReport.of("scene_placement", [
        GateFinding(gate="scene_placement", severity="error", message="x", data={"kind": "floating"}),
        GateFinding(gate="scene_placement", severity="error", message="y", data={"kind": "scale"}),
        GateFinding(gate="scene_placement", severity="warn", message="z", data={"kind": "floating"}),
        GateFinding(gate="scene_placement", severity="info", message="s", data={"kind": "summary"})])
    assert report_kinds(rep) == Counter({"floating": 1, "scale": 1}) and report_kinds(None) == Counter()
    diff = kind_diff(Counter({"floating": 2, "sunken": 1}), report_kinds(rep))
    assert diff == {"floating": (2, 1, 1, 0), "sunken": (1, 0, 1, 0), "scale": (0, 1, 0, 1)}
    assert list(diff) == ["floating", "sunken", "scale"]     # the gate's order, not alphabetical


def test_a_blender_record_is_probed_on_its_kept_census_glb(tmp_path: Path, monkeypatch) -> None:
    """scene_blender: the same driver on ``artifacts/census.glb`` (no settle — the picture is Blender's)
    with the bpy facts merged as the build merges them; nothing written into the recorded run."""
    import bench.scene_regate as sr

    run = _run(tmp_path, "bpy", language="scene_blender")
    (run / "artifacts").mkdir()
    (run / "artifacts" / "census.glb").write_bytes(b"glTF")
    (run / "artifacts" / "bpy_census.json").write_text(json.dumps({"fog": {"type": "VolumeBox"}, "background": "sky:MULTIPLE_SCATTERING",
                                                                    "lights": 1, "unique_tris": 120}))
    seen: list[list[str]] = []

    def fake_driver(args, *, timeout_s):
        seen.append(list(args))
        return {"boot": {"ok": True, "camera_problems": []}, "console_errors": [],
                "census": {"totals": {"meshes": 3}, "fog": None, "background": None, "groups": [],
                           "placement": {"assets": [{"name": "Crate", "zone": "Yard", "ground_gap_m": 0.3, "support": "Ground"}]}}}

    monkeypatch.setattr(sr, "_driver_summary", fake_driver)
    before = sorted(p.name for p in (run / "artifacts").iterdir())
    census = probe(run, timeout_s=5.0, language=Language.SCENE_BLENDER)
    assert seen and "--glb" in seen[0] and "--no-settle" in seen[0] and str(run / "artifacts" / "census.glb") in seen[0]
    assert census["language"] == "scene_blender" and census["fog"] == {"type": "VolumeBox"} and census["totals"]["lights"] == 1
    assert census["placement"]["assets"][0]["name"] == "Crate"
    assert sorted(p.name for p in (run / "artifacts").iterdir()) == before
    # and the whole re-gate: a floating ERROR now, nothing recorded → NEW, per kind
    r = regate(run, timeout_s=5.0)
    assert r["booted"] and r["language"] == "scene_blender" and r["after_kinds"] == Counter({"floating": 1})


def test_a_blender_record_without_its_glb_is_rebuilt_on_a_copy(tmp_path: Path, monkeypatch) -> None:
    """The runtime's probe rebuilds in Blender and writes its artifacts, so it runs on a copy of the
    agent's files, never in the recorded run."""
    import bench.scene_regate as sr

    run = _run(tmp_path, "bpy2", language="scene_blender")
    (run / "public" / "assets").mkdir(parents=True)
    (run / "public" / "assets" / "hero.glb").write_bytes(b"glTF")
    (run / "plan.json").write_text("{}")
    probed: list[Path] = []

    class FakeRuntime:
        def probe(self, ws, *, timeout_s):
            probed.append(ws.root)
            assert (ws.root / "src" / "scene.py").is_file() and (ws.root / "public" / "assets" / "hero.glb").is_file()
            return SimpleNamespace(ok=True, census={"placement": {"assets": []}, "language": "scene_blender"})

    monkeypatch.setattr(sr, "get_runtime", lambda language: FakeRuntime())
    census = probe(run, timeout_s=5.0, language=Language.SCENE_BLENDER)
    assert census == {"placement": {"assets": []}, "language": "scene_blender"}
    assert probed and probed[0] != run and not probed[0].is_relative_to(run)
    assert not (run / "artifacts").exists() and not probed[0].exists()      # the copy is gone, the run untouched


def test_the_report_tallies_errors_per_kind(tmp_path: Path, monkeypatch) -> None:
    import bench.scene_regate as sr

    run = _run(tmp_path, "k")
    (run / "record.json").write_text(json.dumps({
        "spec": {"track": "scene", "language": "scene_threejs"}, "plan": {"setting": "meadow", "zones": [], "assets": []},
        "rounds": [{"index": 0, "gates": [{"gate": "scene_placement", "passed": False, "findings": [
            {"severity": "error", "message": "Yard/Rock is sunken 0.5 m into Terrain", "data": {"kind": "sunken"}}]}]}]}))
    census = {"fog": {"type": "Fog"}, "background": "#aabbcc", "placement": {"assets": [
        {"name": "Lantern", "zone": "Yard", "ground_gap_m": 0.3, "support": "Ground",
         "bbox": {"min": [0, 0, 0], "max": [1, 1, 1], "size": [1, 1, 1]}}]}}
    monkeypatch.setattr(sr, "probe", lambda ws, timeout_s, language: census)
    text = report(tmp_path, timeout_s=1.0)
    assert "## ERRORs by kind" in text
    assert "| floating | 0 | 1 | 0 | 1 |" in text and "| sunken | 1 | 0 | 1 | 0 |" in text
    assert "| run | scene_threejs | 1 | 1 | 1 | 1 | |" in text      # the run directory (k/run), as before
