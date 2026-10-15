# Exotic space notes (black hole, wormhole, nebula, planet)

Ported 2026-09-01 from the scene_multifile_graphics reference ledger; the black
hole approach was validated live there (mf36_blackhole, 2026-07-29) including two
measured failure modes quoted below. Everything is raw three.js + GLSL; each is one
option that has been seen to work, for a brief that asks for it.

## Black hole — one shader, not meshes

Scene-graph rings read as neon toys. The lensed look is ONE ShaderMaterial on a
`BackSide` shell around the hole, raymarched in the fragment shader with bent rays,
camera-independent (ray origin from the built-in `cameraPosition` uniform).

* Fast-forward the ray to the hole first (`ro += rd * max(0.0, toHole - 1.5 * r_out)`),
  or a distant camera exhausts the step budget before the disk and the hole renders as
  a bare ring: steps x dt has to cover the REMAINING path, not the camera distance.
* Bend per step: `rd = normalize(rd + g*dt)` with `g = -p * strength / pow(dot(p,p), 1.5)`.
* Accumulate the disk wherever the bent ray crosses the equatorial plane inside the
  disk's radii: a temperature ramp by radius, azimuthal fbm streaks sheared Keplerian,
  a Doppler boost from the disk tangent.
* Sample the disk as a THIN SLAB, not only on sign crossings — crossing-only sampling
  makes the disk VANISH edge-on, because rays parallel to the plane never flip sign
  (measured live in the reference run).
* Rays spiralling inside the horizon terminate BLACK with alpha 1 — the shader paints
  its own shadow; a solid black sphere mesh occludes the fold. Scale `dt` with the shell
  (a dt tuned on a small test hole renders a big one invisible).
* The far-side fold and the photon ring come free from the bending; modelling them as
  geometry is what reads as a toy. Background lensing: the starfield's fragment offsets
  its sample direction toward the hole outside the shadow radius.

## Wormhole

A camera-facing open `TubeGeometry` along a curved spine (`side: BackSide`), a polar-uv
fbm palette scrolling toward the far end, a bright rim at the mouth; a DIFFERENT star
pattern visible through the throat is what sells it.

## Nebula and starfield

Large additive fbm-canvas billboards (alpha = fbm squared) in two or three hues, plus a
few near-black NORMAL-blended dust-lane sprites. Starfield: `makeStars` from
`lib/celestial.js`, or `Points` with power-law brightness, a slight colour spread and a
few hero stars with cross-flare sprites.

## Planet

A sphere with canvas-baked fbm continents and a bump map; an atmosphere rim as a slightly
larger `BackSide` shell with an additive fresnel; a cloud shell just above the surface. A
space background of pure black clips additive blending; a very dark blue-black does not.
