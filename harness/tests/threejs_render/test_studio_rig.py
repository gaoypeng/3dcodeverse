"""The object studio rig: every look-changing file is in the cache signature, and renders are reproducible."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from codeverse3d.conventions import OBJECT_VIEWS_QUICK
from codeverse3d.spatial import render as render_mod
from codeverse3d.spatial.node import runtime_js_dir
from codeverse3d.spatial.render import render_glb

#: the page-side modules that decide what a frame LOOKS like (host plumbing —
#: lib/cli.mjs, lib/host_env.mjs, lib/host_page.mjs — deliberately stays out: it
#: cannot change a pixel, and hashing it would drop the cache on unrelated edits)
PAGE_RIG = ("lib/browser/render_rig.js", "lib/browser/studio.js", "lib/browser/studio_env.js",
            "lib/browser/renderer.js", "lib/browser/camera_fit.js", "lib/orbit.mjs")


def test_rig_signature_covers_every_file_that_can_change_the_look():
    rt = runtime_js_dir().resolve()
    entry = "lib/browser/render_rig.js"
    reachable = {entry}
    frontier = [entry]
    while frontier:
        rel = frontier.pop()
        here = (rt / rel).parent
        for token in (rt / rel).read_text().split("'"):
            if not token.endswith((".js", ".mjs")) or token.startswith(("http", "three")):
                continue
            if token.startswith("."):
                target = (here / token).resolve()
            elif "/__runtime/" in token:
                target = (rt / token.split("/__runtime/", 1)[1]).resolve()
            else:
                continue
            if not target.is_file() or rt not in target.parents:
                continue
            found = target.relative_to(rt).as_posix()
            if found not in reachable:
                reachable.add(found)
                frontier.append(found)
    signed = set(render_mod.RIG_FILES)
    missing = sorted(reachable - signed)
    assert not missing, f"add these to spatial/render.py::RIG_FILES: {missing}"
    assert set(PAGE_RIG) <= signed and "render_glb.mjs" in signed
    sig = render_mod._rig_signature()
    assert len(sig) == 12 and sig != "d41d8cd98f00"


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


@pytest.mark.node   # renders through the node studio rig
def test_orbit_views_share_one_camera_distance(stool_glb: Path, tmp_path: Path):
    """D47: one camera distance per elevation ring; the 10 % pullback cap bounds the spread."""
    from codeverse3d.conventions import OBJECT_VIEWS

    render_glb(stool_glb, tmp_path / "o", views=OBJECT_VIEWS, width=192, height=192,
               sheet=False, use_cache=False)
    meta = json.loads((tmp_path / "o" / "views.json").read_text())
    band, steep = [], []
    rings: dict[float, list[float]] = {}
    for v in meta["views"]:
        d = float(np.linalg.norm(np.array(v["camera_position"]) - np.array(v["look_at"])))
        if abs(v["elevation"]) > 60:
            steep.append(d)
        else:
            band.append(d)
            rings.setdefault(abs(float(v["elevation"])), []).append(d)
    assert len(band) >= 4 and len(rings) >= 2
    for el, ds in rings.items():
        assert max(ds) / min(ds) <= 1.0 + 1e-6, f"ring |el|={el} not uniform: {sorted(ds)}"
    # the pullback cap bounds how far rings can drift apart; 1.25 is slack over the
    # stool's measured 1.14 (eye ring vs the ±30° rings), a real regression is far larger
    assert max(band) / min(band) <= 1.25 + 1e-6, f"orbit distances spread too far: {sorted(band)}"
    assert steep, "OBJECT_VIEWS should still contain a plan view that fits itself"
