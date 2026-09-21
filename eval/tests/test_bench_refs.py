"""bench/refs/<prompt_id>/ is the reference folder: present → attached, absent → nothing changes."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
from run_bench import Battery, BenchPrompt, discover_references  # noqa: E402

from codeverse.contracts.common import Track  # noqa: E402


def test_folder_images_attach_in_name_order_with_track_roles(tmp_path):
    refs = tmp_path / "refs" / "tsr_gfx_aurora_ridge"
    refs.mkdir(parents=True)
    for name in ("b_green_spiral.png", "a_purple_lake.jpg", "notes.txt"):
        (refs / name).write_bytes(b"x")
    item = BenchPrompt(id="tsr_gfx_aurora_ridge", prompt="an aurora", tier="medium")
    gfx = discover_references(item, Track.GRAPHICS, refs_dir=tmp_path / "refs")
    assert [Path(r.path).name for r in gfx] == ["a_purple_lake.jpg", "b_green_spiral.png"], "sorted, images only"
    assert {r.role for r in gfx} == {"likeness"} and gfx[0].note == "a purple lake"
    obj = discover_references(item, Track.STATIC_OBJECT, refs_dir=tmp_path / "refs")
    assert [r.role for r in obj] == ["target", "detail"]
    assert discover_references(BenchPrompt(id="other", prompt="x", tier="easy"), Track.GRAPHICS, refs_dir=tmp_path / "refs") == []


def test_explicit_references_resolve_against_the_battery_file(tmp_path):
    (tmp_path / "pics").mkdir()
    (tmp_path / "pics" / "ref.png").write_bytes(b"x")
    y = tmp_path / "b.yaml"
    y.write_text(yaml.safe_dump({"name": "b", "track": "graphics", "language": "glsl_shader",
                                 "prompts": [{"id": "p1", "prompt": "x", "tier": "easy", "references": ["pics/ref.png"]}]}))
    b = Battery.load(y)
    assert b.source_dir == tmp_path
    refs = discover_references(b.prompts[0], b.track, battery_dir=b.source_dir, refs_dir=tmp_path / "none")
    assert [Path(r.path).name for r in refs] == ["ref.png"] and refs[0].role == "likeness"
