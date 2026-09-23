"""A synthetic multi-root run tree: two batteries, a good/failing/pending/corrupt run.

Reuses ``tests.flywheel_cli.conftest.make_fake_run`` so the gallery is exercised
against exactly the records the rest of the flywheel tests use.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.contracts.common import Language
from tests.flywheel_cli.conftest import make_fake_run, tiny_png


@pytest.fixture
def gallery_tree(tmp_path: Path) -> dict[str, Path]:
    """``{"runs": …, "battery": …}`` — two roots holding six run directories."""
    runs = tmp_path / "runs"
    battery = tmp_path / "eval" / "bench" / "out" / "static_v9" / "runs"
    battery.mkdir(parents=True)

    make_fake_run(runs, "wooden_chair_ab12cd34")                                     # passes
    make_fake_run(runs, "lamp_three", prompt="a desk lamp", language=Language.THREEJS,
                  scores=(0.70, 0.72))                                               # fails
    make_fake_run(battery, "ctrl_med_toaster", prompt="a chrome toaster", scores=(0.4, 0.9))
    make_fake_run(battery, "art_easy_hinge", prompt="a cabinet door",
                  language=Language.URDF_BLENDER, scores=(0.6, 0.81))

    corrupt = battery / "half_written"                                               # bench mid-write
    corrupt.mkdir()
    (corrupt / "spec.json").write_text(json.dumps({"prompt": "a stool", "track": "static_object",
                                                   "language": "blender"}))
    (corrupt / "record.json").write_text('{"spec": {"id": "half', encoding="utf-8")

    pending = battery / "not_started"
    pending.mkdir()
    (pending / "spec.json").write_text(json.dumps({"prompt": "a bookshelf", "track": "static_object",
                                                   "language": "blender"}))
    tiny_png(pending / "artifacts" / "renders" / "r00" / "sheet.png")
    return {"runs": runs, "battery": battery, "root": tmp_path}
