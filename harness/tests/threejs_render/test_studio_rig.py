"""The object studio rig: renders are reproducible and the backdrop is a sweep, not a flat fill."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from codeverse3d.conventions import OBJECT_VIEWS_QUICK
from codeverse3d.spatial.render import render_glb


@pytest.mark.node
def test_studio_render_is_reproducible_and_stamps_the_rig_version(stool_glb: Path, tmp_path: Path):
    a = render_glb(stool_glb, tmp_path / "a", views=OBJECT_VIEWS_QUICK, width=256, height=256,
                   sheet=False, use_cache=False)
    b = render_glb(stool_glb, tmp_path / "b", views=OBJECT_VIEWS_QUICK, width=256, height=256,
                   sheet=False, use_cache=False)
    meta = json.loads((tmp_path / "a" / "views.json").read_text())
    assert meta["rig_version"] == 2
    # The v1 flat fill put 79% of a frame in one luminance bucket.  Reuse this
    # real render to pin the v2 sweep: its corners stay darker than the centre.
    with Image.open(a.views[0].path) as im:
        pixels = np.asarray(im.convert("L"), dtype=np.float32)
    corner = float(np.mean([
        pixels[:24, :24].mean(), pixels[:24, -24:].mean(),
        pixels[-24:, :24].mean(), pixels[-24:, -24:].mean(),
    ]))
    top_centre = float(pixels[:24, 116:140].mean())
    assert top_centre - corner > 4.0, (
        f"backdrop looks flat: centre {top_centre:.1f} vs corners {corner:.1f}")
    if a.renderer != b.renderer:
        pytest.skip(f"different GL backends between runs ({a.renderer} vs {b.renderer})")
    for va, vb in zip(a.views, b.views, strict=True):
        assert Path(va.path).read_bytes() == Path(vb.path).read_bytes(), f"{va.name} is not reproducible"
