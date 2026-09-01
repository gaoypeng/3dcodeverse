# Exotic space recipes (black hole, wormhole, nebula, planet)

Ported 2026-09-01 from the scene_multifile_graphics reference ledger; the black
hole recipe was validated live there (mf36_blackhole, 2026-07-29) including two
measured failure modes quoted below. Everything is raw three.js + GLSL.

## Black hole — one shader, not meshes

Scene-graph rings read as neon toys. The Interstellar look is ONE
ShaderMaterial on a shell — `SphereGeometry(2.5 * r_out)`, `side: BackSide`,
`transparent: true`, `depthWrite: false` — centred on the hole, raymarched in
the fragment shader with bent rays. Camera-independent: ray origin from the
built-in `cameraPosition` uniform.

* FAST-FORWARD the ray to the hole first — `float toHole = dot(-ro, rd);
  ro += rd * max(0.0, toHole - 1.5 * r_out);` — or a distant camera exhausts
  the step budget before the disk and the hole renders as a bare ring
  (steps x dt must exceed ~3x r_out of REMAINING path, never camera distance).
* Step 30-40 times: `vec3 g = -p * strength / pow(dot(p,p), 1.5);
  rd = normalize(rd + g*dt); p += rd*dt;`
* Disk accumulation wherever the bent ray crosses the equatorial plane inside
  [r_in, r_out]: temperature ramp white-hot -> orange -> deep red by radius,
  x azimuthal fbm streaks sheared Keplerian (`phi + t / pow(r, 1.5)`),
  x Doppler boost `1.0 + 0.6 * dot(diskTangent, -rd)`.
* Sample the disk as a THIN SLAB, not only on sign crossings:
  `bool crossed = (pn.y > 0.) != (p.y > 0.); bool inSlab = abs(pn.y) < 1.5;`
  accumulate on either (slab samples weighted ~0.22). Crossing-only sampling
  makes the disk VANISH edge-on — rays parallel to the plane never flip sign
  (measured live in the reference run).
* Rays spiralling inside ~1.4 r_s terminate BLACK with alpha 1 — the shader
  paints its own shadow; remove any solid black sphere mesh or it occludes the
  fold. Scale `dt ~ r_out / 20` so ~90 steps cross the shell (a dt tuned on a
  small test hole renders a big one invisible).
* The far-side fold over/under the horizon and the photon ring come free from
  the bending — never model them as geometry. Background lensing: the
  starfield sphere's fragment offsets its sample direction toward the hole by
  `strength / b2` outside the shadow radius.

## Wormhole

Camera-facing open `TubeGeometry` along a curved spine (`side: BackSide`),
polar-uv fbm palette scrolling toward the far end, bright rim at the mouth,
radial streaks converging inward; a DIFFERENT star pattern visible through the
throat is what sells it.

## Nebula and starfield

10-30 large additive fbm-canvas billboards (alpha = fbm^2, magenta/cyan/orange)
plus a few near-black NORMAL-blended dust-lane sprites. Starfield: `Points`
5-10k with power-law brightness (`pow(rand(), 3.0)`), slight colour spread,
3-5 hero stars with cross-flare sprites.

## Planet

Sphere with canvas-baked fbm continents + bumpMap; atmosphere rim = 1.03x
sphere, `side: BackSide`, additive fresnel (`pow(1. - dot(N, V), 3.5)`); cloud
shell at 1.01x. Space background is never pure #000 (additive blending clips) —
use #020208.
