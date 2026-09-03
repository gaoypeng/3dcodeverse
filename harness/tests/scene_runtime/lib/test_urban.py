"""urban.js: the glazed facade, the cables over the street, the hoarding.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_urban_lib.py).  Its six laws are kept — the catenary, the
grazing-angle fresnel, no second render target, no transparent card left
in the occlusion pass, a compile in an asset AND in a fogged scene, and
one seed rebuilding the same street — because none of them depend on
their renderer.  Their asset/scene split is rebuilt on OUR wrapper
(`_probe.compile_scene`).

THE PORT'S OWN LAWS, each measured on this host.  Frames:
fx/out/urban/{before,after,before_night,after_night}, one fixture
(fx/fixtures/urban.js: two glazed towers, a four-span line of poles, a
lit hoarding and a lit light box, both spilling onto the ground).

1. A POSTER IS PRINTED INK, NOT A LIGHT SOURCE.  The sheet peaked at
   L 0.92 and clipped under its own gooseneck lamps: blown_frac over the
   poster's pixels was 0.120 by day and 0.276 at night.  Every texel now
   sits in 0.155..0.80 sRGB (0.02..0.60 linear), held on LUMINANCE so a
   dark ink stays a dark ink instead of turning pink-grey.  Measured:
   blown 0.120 -> 0.000 (day), 0.276 -> 0.000 (night).

2. THE GRAIN WAS A BEAT, NOT A HASH.  `sin(u * 91.7 + v * 47.3) *
   43758.5 % 1` is a 1-D function of a linear combination of u and v,
   and a 128 px sheet magnified over a 5.4 m board printed it as
   DIAGONAL STRIPES (crop fx/u_poster_before.png).  It is fBm now, and
   the law that pins it is band-limiting: neighbouring texels must
   differ by far less than the sheet's own spread.  Measured ratio
   1.153 (old, white noise) -> 0.032.

3. A HALO IS LIGHT IN THE AIR, NOT A VEIL OVER THE ARTWORK.  The glow
   card peaked at the CENTRE, i.e. over the opaque board, and with no
   bloom pass to justify it that additive sheet just clipped the top
   band of the poster in daylight and at night both.  It is hollowed
   out over the board's own outline now, and its gain falls off as
   (1 - ambient) ** 1.7 so one card reads at night and vanishes by noon.

4. A RUN OF POLES IS NOT ONE FLAT BROWN.  Weathering rides in a vertex
   colour that only ever darkens, so `color` stays the true albedo.
   Measured over the 2 572 pole pixels of the mid view: lum_std 0.0517
   -> 0.0688, saturation 0.0702 -> 0.0870, mean luminance 0.307 ->
   0.268 (creosote at the foot, bleach at the top).

5. A COATING REFLECTS A TINTED IMAGE.  The reflected sky was untinted,
   so under a hazy sky every tower returned the haze and read as one
   grey sheet whatever `tint` said.  `coat` (default 0.45) puts the
   glass hue into the reflection and a per-panel batch varies it.
   Measured saturation over the facade: 0.205 -> 0.318 (blue tower),
   0.111 -> 0.173 (green round tower), and the HUE SEPARATION between
   the two towers in one frame 0.0257 -> 0.0897 — before this they were
   the same pale grey-blue whatever glass they were given.
"""

from __future__ import annotations

import math

from tests.scene_runtime.lib._probe import LIB_DIR, compile_scene, measure

_LIBS = ("shader.js", "noise.js", "materials.js", "neon.js", "urban.js")


# The poster texture, read straight off the built material.
_POSTER = """
import { makeBillboard } from './lib/urban.js';
const g = makeBillboard(%s);
let tex = null;
g.traverse((o) => { if (o.name === 'Face') tex = o.material.map; });
const d = tex.image.data;
const rgb = [];
for (let i = 0; i < d.length; i += 4) rgb.push(d[i], d[i + 1], d[i + 2]);
console.log(JSON.stringify({ size: tex.image.width, rgb }));
"""


def test_a_cable_hangs_as_a_catenary_and_not_as_a_parabola():
    """A hanging cable is cosh, not x squared. The two agree to a
    fraction of a percent at the shallow sags a tidy street has, so the
    only honest test is a DEEP sag, where the catenary is visibly
    fuller at the ends. Both models are fitted from the measured span
    and dip by the same rule, not assumed."""
    out = measure("""
import * as THREE from 'three';
import { makePowerLines } from './lib/urban.js';
// A deep sag: the shallow case cannot tell the two curves apart.
const g = makePowerLines({ points: [[-15, 8, 0], [15, 8, 0]], spans: 1,
                           sag: 9, seed: 4 });
const pts = [];
g.traverse((o) => {
  if (!o.isMesh) return;
  const p = o.geometry.getAttribute('position');
  if (!p) return;
  for (let i = 0; i < p.count; i++) {
    pts.push([p.getX(i), p.getY(i), p.getZ(i)]);
  }
});
console.log(JSON.stringify({ pts }));
""", _LIBS)
    prof: dict[float, float] = {}
    for x, y, _z in out["pts"]:
        k = round(x, 2)
        prof[k] = min(prof.get(k, 1e9), y)
    ks = sorted(k for k in prof if -14.5 <= k <= 14.5)
    assert len(ks) >= 15, "too few samples to tell the two curves apart"
    ends = max(prof[ks[0]], prof[ks[-1]])
    dip = ends - min(prof[k] for k in ks)
    assert dip > 1.0, f"the cable barely sags ({dip:.2f} m)"
    half = (ks[-1] - ks[0]) / 2
    mid = (ks[-1] + ks[0]) / 2

    # Solve the catenary's own parameter from this span and dip, the
    # same bisection the library documents.
    lo, hi = 1e-6, 1.0
    while half / hi * (math.cosh(hi) - 1) < dip and hi < 1e4:
        hi *= 2
    for _ in range(80):
        m = 0.5 * (lo + hi)
        if half / m * (math.cosh(m) - 1) < dip:
            lo = m
        else:
            hi = m
    u = 0.5 * (lo + hi)

    def rms(model) -> float:
        s = sum((prof[k] - (ends - dip * model((k - mid) / half))) ** 2
                for k in ks)
        return (s / len(ks)) ** 0.5

    cat = rms(lambda v: (math.cosh(u) - math.cosh(u * v))
              / (math.cosh(u) - 1))
    par = rms(lambda v: 1 - v * v)
    assert cat < par * 0.5, (
        f"the profile fits a parabola at least as well as a catenary "
        f"(catenary rms {cat:.4f}, parabola rms {par:.4f}) — a parabola "
        "is the small-sag limit and gets the end curvature wrong")


def test_the_facade_reflects_more_at_a_grazing_angle():
    """Fresnel is what makes a tower a tower: it shows its rooms head-on
    and the sky along its flank. A constant reflectivity is one mirror
    sheet, which is the failure this patch exists to avoid."""
    out = measure(r"""
import * as THREE from 'three';
import { patchCurtainWall } from './lib/urban.js';
const m = new THREE.MeshStandardMaterial();
patchCurtainWall(m, {});
const s = { uniforms: {},
            vertexShader: 'void main() { #include <begin_vertex> }',
            fragmentShader: '#include <color_fragment>' };
m.onBeforeCompile(s);
const fs = s.fragmentShader;
console.log(JSON.stringify({
  // The reflected term has to ride a view-dependent quantity.
  hasFresnel: /astraFresnel|Fresnel\(|1\.0\s*-\s*(abs\()?dot\(/.test(fs),
  usesViewVector: /cameraPosition/.test(fs),
  // Per-panel variation: without it the whole wall shares one ray.
  perPanel: /cwPn|panel|uCwPanel/i.test(fs),
  liftsLight: /totalEmissiveRadiance\s*\+=/.test(fs),
}));
""", _LIBS)
    assert out["hasFresnel"] and out["usesViewVector"], (
        "reflectivity must depend on the view angle")
    assert out["perPanel"], "panels must differ or the wall is one mirror"
    assert out["liftsLight"], (
        "reflected radiance is light; albedo cannot carry it")


def test_the_facade_spends_no_second_render_target():
    """The scene has ONE render-target budget and mirror.js or
    wetground.js has usually already spent it. A curtain wall that
    quietly allocates a second one costs a frame nobody authorised, so
    the fresnel sky has to be computed, not rendered."""
    src = (LIB_DIR / "urban.js").read_text(encoding="utf-8")
    # Comments may NAME the thing they refuse to use; code may not.
    code = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith(("*", "//", "/*")))
    for banned in ("WebGLRenderTarget", "WebGLCubeRenderTarget",
                   "new Reflector", "CubeCamera"):
        assert banned not in code, (
            f"urban.js reaches for {banned}; the scene's one RTT is "
            "already spent by mirror.js or wetground.js")


def test_every_glow_is_kept_out_of_the_occlusion_pass():
    """A depth/occlusion override pass redraws the scene with an opaque
    material, where a transparent glow card is a solid wall. It also
    stops the mesh casting a shadow, which a light source must not do."""
    out = measure("""
import * as THREE from 'three';
import { makeBillboard, makePowerLines } from './lib/urban.js';
const check = (g) => {
  let transparent = 0, guarded = 0, casting = 0;
  g.traverse((o) => {
    if (!o.isMesh) return;
    for (const m of [].concat(o.material || [])) {
      if (m && m.transparent) {
        transparent++;
        if (o.userData.astraNoOverride) guarded++;
        if (o.castShadow) casting++;
      }
    }
  });
  return { transparent, guarded, casting };
};
console.log(JSON.stringify({
  billboard: check(makeBillboard({ lit: true, seed: 3 })),
  lines: check(makePowerLines({ seed: 3 })),
}));
""", _LIBS)
    for who, r in out.items():
        assert r["transparent"], f"{who} built no transparent mesh at all"
        assert r["guarded"] == r["transparent"], (
            f"{who} leaves {r['transparent'] - r['guarded']} transparent "
            "meshes in the occlusion buffer")
        assert r["casting"] == 0, f"{who} casts a shadow from a glow"


_FIXTURE = """
import * as THREE from 'three';
import { patchCurtainWall, makePowerLines, makeBillboard }
    from './lib/urban.js';
const g = new THREE.Group();
const glass = new THREE.MeshStandardMaterial({ color: 0x2b3a4a });
patchCurtainWall(glass, {});
g.add(new THREE.Mesh(new THREE.BoxGeometry(8, 30, 8), glass));
// Instanced too: the patch folds instanceMatrix in by hand, and that
// branch only compiles when something instanced carries the material.
g.add(new THREE.InstancedMesh(new THREE.BoxGeometry(2, 2, 2), glass, 3));
g.add(makePowerLines({ seed: 5 }));
g.add(makeBillboard({ lit: true, seed: 6 }));
const round = new THREE.MeshStandardMaterial({ color: 0x223028 });
patchCurtainWall(round, { curve: 4.5, coat: 1, blinds: 0.4 });
g.add(new THREE.Mesh(new THREE.CylinderGeometry(4, 4, 12, 24, 1, true),
                     round));
"""


def test_the_three_compile_in_a_fogged_scene():
    """rain.js shipped fog chunks that could not compile for months
    because only asset mode was ever checked: asset mode builds an
    UNFOGGED scene, so USE_FOG is never defined and the branch is never
    compiled.  So the fogged half is built as a real scene here, through
    `_probe.compile_scene` — the asset half of the reference test proves
    strictly less and is not ported."""
    code, out = compile_scene(_FIXTURE + """
export const BOUNDS = { min: [-20, 0, -20], max: [20, 34, 20] };
export function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xc9d6e0, 0.006);
  scene.add(new THREE.HemisphereLight(0xbcd6ef, 0x4a4130, 0.9));
  const sun = new THREE.DirectionalLight(0xfff0d6, 2.5);
  sun.position.set(5, 8, 7);
  sun.castShadow = true;
  scene.add(sun);
  scene.add(g);
  // A camera, or the host reports a scene that never booted and the
  // compile stage is never reached.
  return { scene, update() {},
           cameras: [{ name: 'a', position: [16, 8, 20],
                       lookAt: [0, 6, 0], fov: 45 }] };
}
""", _LIBS)
    assert code == 0, out
    assert "WARN" not in out, out


def test_one_seed_builds_the_same_street_twice():
    out = measure("""
import { makePowerLines, makeBillboard } from './lib/urban.js';
// Names and vertex COUNTS do not move with a seed; the vertices do.
const shape = (g) => {
  const v = [];
  g.traverse((o) => {
    if (!o.isMesh) return;
    const p = o.geometry.getAttribute('position');
    let s = 0;
    if (p) for (let i = 0; i < p.count; i += 7) s += p.getX(i) + p.getY(i);
    v.push(o.name, p ? p.count : 0, s.toFixed(5));
  });
  return v.join('|');
};
console.log(JSON.stringify({
  same: shape(makePowerLines({ seed: 8 }))
      === shape(makePowerLines({ seed: 8 })),
  differs: shape(makePowerLines({ seed: 8 }))
      !== shape(makePowerLines({ seed: 12 })),
  boardSame: shape(makeBillboard({ seed: 2 }))
      === shape(makeBillboard({ seed: 2 })),
}));
""", _LIBS)
    assert out["same"] and out["differs"] and out["boardSame"]


# ------------------------------------------------- the port's own laws

def _poster(opts: str = "{ lit: true, seed: 2 }") -> tuple[int, list]:
    out = measure(_POSTER % opts, _LIBS)
    return out["size"], out["rgb"]


def _lum(rgb: list, i: int) -> float:
    r, g, b = rgb[i * 3] / 255, rgb[i * 3 + 1] / 255, rgb[i * 3 + 2] / 255
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_a_poster_is_printed_ink_and_not_a_light_source():
    """LAW 1. Paper reflects; it does not emit. The sheet used to print
    its headline at L 0.92 and clipped under its own lamps (blown_frac
    0.120 by day, 0.276 at night, measured over the poster's pixels).
    The band is held on luminance so a dark ink keeps its hue."""
    size, rgb = _poster()
    lum = [_lum(rgb, i) for i in range(size * size)]
    assert min(lum) >= 0.15, (
        f"the darkest ink is a hole at {min(lum):.3f} (0.155 sRGB is "
        "0.02 linear, the floor of the albedo band)")
    assert max(lum) <= 0.805, (
        f"the sheet peaks at {max(lum):.3f}; 0.80 sRGB is 0.60 linear "
        "and leaves the top of the band for the lamps")
    # It must still be a POSTER: several inks over paper that is not one
    # flat fill, not a tinted rectangle inside the band.
    assert len({round(v, 2) for v in lum}) >= 10, (
        "the whole sheet is two or three flat tones — ink density, the "
        "paste seams and the bleach all have to survive the clamp")
    mean = sum(lum) / len(lum)
    std = (sum((v - mean) ** 2 for v in lum) / len(lum)) ** 0.5
    assert std > 0.08, f"the sheet is one tone (lum std {std:.3f})"


def test_the_paper_grain_is_band_limited_and_not_a_beat():
    """LAW 2. `sin(u * 91.7 + v * 47.3) * 43758.5 % 1` is not a hash at
    those frequencies: neighbouring texels are uncorrelated (white
    noise), which magnified 40x over a hoarding beats against the
    sampler and prints diagonal stripes. Real grain is band-limited —
    neighbours differ by a fraction of the sheet's own spread. Measured
    ratio: 1.153 for the old field, 0.032 for fBm."""
    size, rgb = _poster()
    lum = [_lum(rgb, i) for i in range(size * size)]
    mean = sum(lum) / len(lum)
    std = (sum((v - mean) ** 2 for v in lum) / len(lum)) ** 0.5
    steps, n = 0.0, 0
    for y in range(size):
        row = lum[y * size:(y + 1) * size]
        for x in range(size - 1):
            steps += abs(row[x + 1] - row[x])
            n += 1
    ratio = (steps / n) / max(std, 1e-6)
    assert ratio < 0.35, (
        f"neighbouring texels differ by {ratio:.3f} of the sheet's own "
        "spread — that is per-texel white noise, which is what printed "
        "as diagonal stripes over the board")


def test_the_halo_never_veils_the_board_it_glows_around():
    """LAW 3. The glow card peaked over the OPAQUE board, and with no
    bloom pass in our renderer that additive sheet only flattened the
    artwork — it clipped the poster's top band by day and at night.
    Hollowed out over the board's outline, and gone in daylight."""
    out = measure("""
import { makeBillboard } from './lib/urban.js';
const read = (opts) => {
  const g = makeBillboard(opts);
  let glow = null, board = null;
  g.traverse((o) => {
    if (o.name === 'Glow') glow = o;
    if (o.name === 'Board') board = o;
  });
  if (!glow) return { none: true };
  const u = glow.material.uniforms;
  glow.geometry.computeBoundingBox();
  board.geometry.computeBoundingBox();
  const gb = glow.geometry.boundingBox, bb = board.geometry.boundingBox;
  return {
    core: u.uBbCore.value,
    inner: [u.uBbInner.value.x, u.uBbInner.value.y],
    gain: u.uBbGain.value,
    hollows: /uBbInner/.test(glow.material.fragmentShader),
    cardW: gb.max.x - gb.min.x, boardW: bb.max.x - bb.min.x,
    cardH: gb.max.y - gb.min.y, boardH: bb.max.y - bb.min.y,
  };
};
console.log(JSON.stringify({
  hoarding: read({ lit: true, kind: 'hoarding', ambient: 0.15, seed: 2 }),
  panel: read({ lit: true, kind: 'panel', ambient: 0.15, seed: 2 }),
  noon: read({ lit: true, kind: 'hoarding', ambient: 0.95, seed: 2 }),
  unlit: read({ lit: false, seed: 2 }),
}));
""", _LIBS)
    h = out["hoarding"]
    assert h["hollows"], "the halo does not read the board's own outline"
    assert h["core"] == 0, (
        "a pasted sheet does not glow through itself; only a light box "
        f"keeps a core (got {h['core']})")
    assert out["panel"]["core"] > 0, (
        "a light box's face really does emit — its core is not zero")
    # The hollow only means something if the card is bigger than the board.
    for who in ("hoarding", "panel"):
        r = out[who]
        assert r["cardW"] > r["boardW"] * 1.2 and r["cardH"] > r["boardH"] * 1.2, (
            f"{who}: the halo has no air to stand in "
            f"({r['cardW']:.2f} x {r['cardH']:.2f} vs the board's "
            f"{r['boardW']:.2f} x {r['boardH']:.2f})")
        assert abs(r["inner"][0] - r["boardW"] / r["cardW"]) < 0.02, (
            f"{who}: uBbInner does not match where the board falls on "
            "the card, so the hollow is cut in the wrong place")
    assert out["noon"]["gain"] < out["hoarding"]["gain"] * 0.25, (
        "a halo is light scattered in the air: in open daylight it has "
        f"to all but vanish (gain {out['noon']['gain']:.3f} against "
        f"{out['hoarding']['gain']:.3f} on an unlit street)")
    assert out["unlit"].get("none"), "an unlit hoarding built a halo"


def test_a_run_of_poles_is_not_one_flat_brown():
    """LAW 4. Twenty poles merged into one mesh share one colour, which
    reads as the same prop repeated. The weathering rides in a vertex
    colour — and only DARKENS, so `color` stays the true albedo."""
    out = measure("""
import { makePowerLines } from './lib/urban.js';
const read = (name, g) => {
  let mesh = null;
  g.traverse((o) => { if (o.name === name) mesh = o; });
  const c = mesh.geometry.getAttribute('color');
  const v = [];
  for (let i = 0; i < c.count; i++) v.push(c.getX(i), c.getY(i), c.getZ(i));
  return { vertexColors: !!mesh.material.vertexColors,
           max: Math.max(...v), min: Math.min(...v),
           mean: v.reduce((a, b) => a + b, 0) / v.length,
           n: v.length };
};
const g = makePowerLines({ points: [[-30, 0, 0], [30, 0, 0]], spans: 6,
                           seed: 5 });
console.log(JSON.stringify({ poles: read('Poles', g),
                             ins: read('Insulators', g) }));
""", _LIBS)
    for who, r in out.items():
        assert r["vertexColors"], f"{who} ignores the weathering it carries"
        assert r["max"] <= 1.0001, (
            f"{who} brightens its own albedo to {r['max']:.3f}; a vertex "
            "colour here may only darken or `color` stops being the albedo")
        assert r["max"] - r["min"] > 0.15, (
            f"{who} varies by only {r['max'] - r['min']:.3f} — that is one "
            "flat prop again")
        assert 0.72 < r["mean"] < 1.0, (
            f"{who} is dimmed to {r['mean']:.3f} on average, which is a "
            "new colour rather than weathering on the documented one")


def test_the_glass_reflects_a_tinted_image():
    """LAW 5. An untinted reflection returns the sky, so under a hazy
    sky every tower read as one grey sheet whatever `tint` said —
    measured saturation 0.205 over a blue facade, 0.111 over a green
    one. `coat` puts the glass hue into what it mirrors."""
    out = measure(r"""
import * as THREE from 'three';
import { patchCurtainWall } from './lib/urban.js';
const uni = (o) => {
  const m = new THREE.MeshStandardMaterial();
  patchCurtainWall(m, o);
  return m.userData.uniforms;
};
const m = new THREE.MeshStandardMaterial();
patchCurtainWall(m, {});
const s = { uniforms: {}, vertexShader: 'void main() {}',
            fragmentShader: '#include <color_fragment>' };
m.onBeforeCompile(s);
const fs = s.fragmentShader;
// The reflected radiance must be multiplied by the tint, and the panel
// key must reach it, before it is added as light.
const body = fs.slice(0, fs.indexOf('totalEmissiveRadiance'));
console.log(JSON.stringify({
  dflt: uni({}).uCwCoat.value,
  off: uni({ coat: 0 }).uCwCoat.value,
  clamped: uni({ coat: 4 }).uCwCoat.value,
  tintsReflection: /cwRefl\s*\*=[^;]*uCwTint/.test(body),
  perPanelReflection: /cwRefl\s*\*=[^;]*astraCwJit|cwJb|cwJh/.test(body),
}));
""", _LIBS)
    assert 0.2 <= out["dflt"] <= 0.7, (
        f"the default coat is {out['dflt']} — 0 gives back the grey "
        "sheet and 1 is a mirror-glass tower, not a default")
    assert out["off"] == 0 and out["clamped"] == 1, "coat must clamp to 0..1"
    assert out["tintsReflection"], (
        "the reflected image carries no glass hue, so the facade "
        "returns whatever the sky is and reads grey")
    assert out["perPanelReflection"], (
        "every panel reflects the identical radiance — one coating "
        "batch, i.e. one mirror sheet again")
