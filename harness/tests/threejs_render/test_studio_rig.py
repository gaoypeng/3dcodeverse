"""The object studio rig (rig v2): what it is made of, and that it stays deterministic.

Rig v2 replaced `RoomEnvironment` + three white directionals on a flat clear colour
with a procedural softbox environment, a swept backdrop, a camera-relative key and a
soft contact shadow.  Two properties have to hold for the harness around it to keep
working: the pictures must be reproducible (the render cache keys on file content,
not on a timestamp), and every file that can change the look must be inside the cache
signature — otherwise an edited rig silently serves stale PNGs to the judge.
"""

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
    """`_rig_signature` is what makes an edited rig invalidate the render cache.  Any
    module the browser rig imports must be in it; a new one silently breaks caching."""
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


def test_rig_is_procedural_and_deterministic_by_construction():
    """No randomness, no wall clock, no external asset: the environment, the backdrop
    and the contact shadow are all generated from constants in studio_env.js."""
    rt = runtime_js_dir()
    for rel in ("lib/browser/studio.js", "lib/browser/studio_env.js", "lib/browser/render_rig.js"):
        src = (rt / rel).read_text()
        assert "Math.random" not in src, rel
    for rel in ("lib/browser/studio.js", "lib/browser/studio_env.js"):
        assert "Date.now" not in (rt / rel).read_text(), rel
    studio = (rt / "lib/browser/studio.js").read_text()
    assert "environments/RoomEnvironment" not in studio, "rig v2 builds its own environment"
    assert "softboxEnvironment" in studio and "backdropTexture" in studio
    assert "export const RIG_VERSION = 2" in studio
    assert "aimStudio" in (rt / "lib/browser/render_rig.js").read_text()


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
    """Consistent framing: views in the orbit band (|elevation| <= 60) are pulled towards
    one shared camera distance so the object keeps its apparent size across the montage —
    but no view is pulled back by more than 10 % beyond its own exact fit, or a deep
    object shrinks every frame to what its widest side needs and the judge loses detail
    resolution.  Under the 14-view rig (D47) the band spans three elevation rings whose
    exact fits genuinely differ, so the 10 % cap leaves a bounded spread between rings;
    within a ring the distance must still be one number."""
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
