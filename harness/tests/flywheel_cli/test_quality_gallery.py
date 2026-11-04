"""Quality tiers, (code, prompt) dedupe, richer meta.json, captions side-car."""

from __future__ import annotations

import json
from pathlib import Path

from codeverse3d.addons.dataset.captions import caption_sample
from codeverse3d.addons.dataset.export import export_samples, load_captions
from codeverse3d.record.record import load_record
from tests.flywheel_cli.test_captions import GOOD
from tests.orchestrator_tracks.fakes import FakeChatModel

# --------------------------------------------------------------------------- tiers + dedupe


# --------------------------------------------------------------------------- export extras


def test_a_side_car_caption_leaves_the_run_untouched_and_export_keeps_repeating_view_names(fake_run, tmp_path: Path):
    """Scene renders repeat a camera name per capture time; the sample must keep every file."""
    from codeverse3d.contracts.artifacts import RenderView
    from tests.flywheel_cli.conftest import tiny_png

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
