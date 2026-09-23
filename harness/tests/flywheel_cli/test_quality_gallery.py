"""Quality tiers, (code, prompt) dedupe, richer meta.json, captions side-car, gallery."""

from __future__ import annotations

import json
from pathlib import Path

from codeverse3d.addons.dataset.captions import caption_sample
from codeverse3d.addons.dataset.export import export_samples, load_captions
from codeverse3d.addons.dataset.quality import (
    find_duplicates,
    mark_duplicates,
    prompt_hash,
    quality_tier,
)
from codeverse3d.contracts.common import Language
from codeverse3d.record.record import load_record
from tests.flywheel_cli.conftest import make_fake_run
from tests.flywheel_cli.test_captions import GOOD
from tests.orchestrator_tracks.fakes import FakeChatModel

# --------------------------------------------------------------------------- tiers + dedupe


def test_quality_tier_rule():
    assert quality_tier(passed=True, gate_errors=0, score=0.9) == "A"
    assert quality_tier(passed=True, gate_errors=3, score=0.9) == "B"
    assert quality_tier(passed=False, gate_errors=0, score=0.61) == "C"
    assert quality_tier(passed=None, gate_errors=0, score=0.6) == "C"
    assert quality_tier(passed=False, gate_errors=0, score=0.59) == "D"
    assert quality_tier(passed=None, gate_errors=5, score=None) == "D"


def test_only_raw_hash_duplicates_are_dropped_normalised_ones_are_only_marked():
    """``duplicate_of`` (the DROP set) is keyed on the RAW code_sha256; the normalised
    fingerprint only ever stamps ``near_duplicate_of`` — row c differs from a/b/e in
    bytes alone and must survive."""
    rows = [
        {"id": "a", "key": "a", "code_sha256": "R", "code_fingerprint": "X", "prompt_hash": "p", "quality_tier": "B", "score": 0.8},
        {"id": "b", "key": "b", "code_sha256": "R", "code_fingerprint": "X", "prompt_hash": "p", "quality_tier": "A", "score": 0.8},
        {"id": "c", "key": "c", "code_sha256": "S", "code_fingerprint": "X", "prompt_hash": "p", "quality_tier": "A", "score": 0.8},
        {"id": "d", "key": "d", "code_sha256": "R", "code_fingerprint": "X", "prompt_hash": "q", "quality_tier": "A", "score": 0.9},
        {"id": "e", "key": "e", "code_sha256": "R", "code_fingerprint": "X", "prompt_hash": "p", "quality_tier": "A", "score": 0.8,
         "has_captions": True},
        {"id": "f", "key": "f", "code_sha256": "", "code_fingerprint": "", "prompt_hash": "p", "quality_tier": "A", "score": 0.9},
    ]
    groups = find_duplicates(rows)
    assert len(groups) == 1 and groups[0].canonical == "e" and set(groups[0].duplicates) == {"a", "b"}
    mark_duplicates(rows)
    assert {r["id"]: r["duplicate_of"] for r in rows} == {"a": "e", "b": "e", "c": "", "d": "", "e": "", "f": ""}
    assert {r["id"]: r["near_duplicate_of"] for r in rows} == {"a": "e", "b": "e", "c": "e", "d": "", "e": "", "f": ""}
    assert prompt_hash("  a chair ") == prompt_hash("a chair") and len(prompt_hash("x")) == 16


def test_whitespace_inside_a_string_is_a_near_duplicate_not_a_duplicate(runs_dir: Path, tmp_path: Path):
    """``LABEL = "a b"`` and ``LABEL="ab"`` normalise identically but are different
    programs: --drop-duplicates must keep both and only mark them, while the
    byte-identical chair/codex pair is still dropped."""
    common = '# round 1\nimport bpy\nLABEL={}\nbpy.ops.mesh.primitive_cube_add(size=2.0)\n'
    make_fake_run(runs_dir, "stool_spaced", prompt="a stool", code_v2=common.format('"a b"'))
    make_fake_run(runs_dir, "stool_tight", prompt="a stool", code_v2=common.format('"ab"'))
    out = tmp_path / "ds"
    rep = export_samples(runs_dir, out, drop_duplicates=True)
    rows = {json.loads(ln)["key"]: json.loads(ln) for ln in (out / "metadata.jsonl").read_text().splitlines()}
    assert {"stool_spaced", "stool_tight"} <= set(rows)
    assert rows["stool_spaced"]["code_sha256"] != rows["stool_tight"]["code_sha256"]
    assert rows["stool_spaced"]["code_fingerprint"] == rows["stool_tight"]["code_fingerprint"]
    assert [rows[k]["duplicate_of"] for k in ("stool_spaced", "stool_tight")] == ["", ""]
    assert rows["stool_tight"]["near_duplicate_of"] == "3dcodeverse/static_object/blender/stool_spaced"
    assert "wooden_chair_codex" not in rows and rep.n_duplicates == 1  # byte-identical → dropped


# --------------------------------------------------------------------------- export extras


def test_export_meta_tiers_duplicates_and_captions_sidecar(runs_dir: Path, tmp_path: Path):
    # a fourth run = exact duplicate (same code + prompt) of the first, and a urdf run with link meshes
    make_fake_run(runs_dir, "wooden_chair_again")
    ws_u, rec_u = make_fake_run(runs_dir, "cabinet_urdf", prompt="a cabinet", language=Language.URDF_BLENDER)
    for r in rec_u.rounds:  # each round keeps its own link meshes (artifacts/rNN/meshes/)
        (ws_u.round_artifacts(r.index) / "meshes").mkdir()
        (ws_u.round_artifacts(r.index) / "meshes" / "Body.glb").write_bytes(b"glTF" + b"\0" * 8)
    side = tmp_path / "caps"
    side.mkdir()
    (side / "lamp_three.json").write_text(json.dumps({**GOOD, "provenance": {"captioner": "gemini:x"}}))

    out = tmp_path / "ds"
    rep = export_samples(runs_dir, out, captions_dir=side)
    # the codex run shares prompt + round-1 code with the chair → also an exact duplicate
    assert rep.n_exported == 5 and rep.n_indexed == 5 and rep.n_duplicates == 2
    assert rep.duplicates[0].canonical == "3dcodeverse/static_object/blender/wooden_chair_ab12cd34"
    assert set(rep.duplicates[0].duplicates) == {"3dcodeverse/static_object/blender/wooden_chair_again",
                                                 "3dcodeverse/static_object/blender/wooden_chair_codex"}
    assert rep.tiers == {"A": 3, "C": 2}  # codex run 0.6 (not passed) and lamp 0.72 → C
    meta = json.loads((out / "static_object" / "blender" / "wooden_chair_ab12cd34" / "meta.json").read_text())
    assert meta["quality_tier"] == "A" and meta["gate_errors"] == 0 and meta["gate_summary"] == {"lint": 0}
    assert meta["acceptance"] == [{"id": "a1", "text": "", "how": "", "priority": "", "passed": True}]
    assert len(meta["code_fingerprint"]) == 64 and meta["prompt_hash"] == prompt_hash("a wooden dining chair")
    assert [r["index"] for r in meta["rounds_summary"]] == [0, 1] and meta["round"] == 1
    u_meta = json.loads((out / "articulated_object" / "urdf_blender" / "cabinet_urdf" / "meta.json").read_text())
    assert "meshes/Body.glb" in u_meta["files"] and (out / "articulated_object" / "urdf_blender" / "cabinet_urdf" / "meshes" / "Body.glb").is_file()
    # side-car captions are picked up for lamp_three only
    rows = {json.loads(line)["key"]: json.loads(line) for line in (out / "metadata.jsonl").read_text().splitlines()}
    assert rows["lamp_three"]["has_captions"] is True and rows["lamp_three"]["captions"]["factory"] == GOOD["factory"]
    assert rows["wooden_chair_ab12cd34"]["has_captions"] is False
    assert rows["wooden_chair_again"]["duplicate_of"] == "3dcodeverse/static_object/blender/wooden_chair_ab12cd34"
    assert (out / "duplicates.json").is_file()
    import pyarrow.parquet as pq

    table = pq.read_table(out / "metadata.parquet")
    assert {"quality_tier", "gate_errors", "cost_usd", "rounds", "status", "code_fingerprint", "code_sha256",
            "prompt_hash", "duplicate_of", "near_duplicate_of", "has_captions"} <= set(table.column_names)
    # drop duplicates from the index (folder stays)
    rep2 = export_samples(runs_dir, out, captions_dir=side, drop_duplicates=True)
    assert rep2.n_indexed == 3 and rep2.n_duplicates == 2 and rep2.tiers == {"A": 2, "C": 1}
    assert (out / "static_object" / "blender" / "wooden_chair_again").is_dir()


def test_export_scene_views_repeating_names(fake_run, tmp_path: Path):
    """Scene renders repeat a camera name per capture time; the sample must keep every file."""
    from codeverse3d.contracts.artifacts import RenderView
    from tests.flywheel_cli.conftest import tiny_png

    ws, rec = fake_run
    rnd = rec.rounds[1]
    rd = ws.renders_dir(1)
    assert rnd.renders is not None
    rnd.renders.views = [RenderView(name="Establishing", path=str(tiny_png(rd / "Establishing_t0.png"))),
                         RenderView(name="Establishing", path=str(tiny_png(rd / "Establishing_t1p5.png")))]
    ws.write_json(ws.record_path, rec)
    out = tmp_path / "ds"
    export_samples(ws.root.parent, out)
    meta = json.loads(next(out.rglob("meta.json")).read_text())
    assert {"renders/view_Establishing_t0.png", "renders/view_Establishing_t1p5.png"} <= set(meta["renders"])
    assert len(meta["files"]) == len(set(meta["files"]))


def test_caption_out_dir_leaves_run_untouched(fake_run, tmp_path: Path):
    ws, rec = fake_run
    before = ws.record_path.read_text()
    side = tmp_path / "caps"
    caps = caption_sample(ws, rec, "fake:fake", model=FakeChatModel([GOOD]), out_dir=side)
    assert caps.factory == GOOD["factory"]
    assert ws.record_path.read_text() == before and not (ws.root / "captions.json").exists()
    data = json.loads((side / f"{ws.root.name}.json").read_text())
    assert data["provenance"]["captioner"] == "fake:fake" and data["provenance"]["images_used"][0].startswith("artifacts/")
    assert load_captions(ws, load_record(ws), side)["detailed"] == GOOD["detailed"]
    assert load_captions(ws, load_record(ws), None) == {}


# --------------------------------------------------------------------------- gallery


