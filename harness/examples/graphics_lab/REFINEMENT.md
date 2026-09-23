# Graphics Lab refinement — 2026-09-23

This pass responds to visual review of the first gallery, especially water that
looked like a coloured sheet, repeated ocean ridges, regular bank stones and
uniform grass. It changes the shipped library itself as well as selected scene
composition. All assets remain procedural code; no generated image backgrounds
or photographic texture downloads were added.

## Water

The ocean uses 32 directional gravity-wave bands normalized to significant
height Hs, per-fragment analytic slopes, filtered small ripples, dielectric
Fresnel/GGX and roughness-aware reflections. Visible equirectangular sky radiance
and reflected scene objects are handled separately. Debug renders caught and
removed dark trough hatches and purple foam. The ocean study keeps its original
cameras but extends the sea to 1.8 km and uses a longer-period sea state; these
scene changes are excluded from the controlled library comparison.

The stream now uses Three's physical transmission at IOR 1.333. Colour describes
absorption through the graded water column, rather than alpha paint over the
bed. Eleven directional ripple bands replace the regular warped sine pattern.
Stone wakes have shorter, less continuous foam; cobbles sit flatter in the bed.
Direct bed light receives a bounded caustic approximation from the same moving
surface. Optional planar bank reflection is off by default and enabled at 1024
pixels in the brook study. Unsupported offscreen transmission fades smoothly
to the unshifted sample instead of stretching the final screen row.

An independent review also found a curved-channel normal error: the old basis
ignored the derivative of the lateral path offset. CPU and GPU now use the
offset surface derivatives. Regression cases cover tighter bends, nonzero grade
and changing channel width; sampled normals agree with independent position
differences within 0.15 degrees.

The stream remains a kinematic surface. Refraction samples opaque screen-space
geometry, and bank reflection approximates the channel with a mean grade. It
does not solve water/solid collisions, refraction of transparent objects,
overturning breakers or photon transport.

## Rock and meadow

Rock geometry retains fracture edges, independently oriented chipped corners,
recessed sandstone bedding and selective edge wear. Mineral and alteration
detail uses filtered 3D shading. Stream-sized granite was checked separately
because its low-detail silhouettes revealed problems hidden by the hero view.
The matched rock study rises from 195,192 to 509,996 triangles; revised scene
dressing brings the final rock study to 766,060 triangles. Close views remain
visibly procedural rather than scanned geological assets.

Meadow leaves vary by growth form, age and habitat, with arching/twisted blades,
dry fragments and optional sparse geometric seed heads. Leaf count stays at
193,000 in the study. The 579 seed culms add two instanced draws and approximately
6.8% triangles (3.14 million for the complete study). Thin-leaf transmission
obeys the actual shadow map. Seeded geometry and absolute-time updates are
deterministic; whole-renderer PNG replay is not promised to be bit-identical.

## Controlled evidence and transfer packages

`compare.py` renders the retained baseline twice and changes exactly one library
module in the second workspace. Scene source, cameras, lighting, time and other
library modules have identical hashes. The nine camera pairs produce 18 real
GPU stills. `output/comparison.html` exposes four representative pairs with
sliders and links the complete hash/render manifest.

The first transfer package preserves the gallery exactly as it was when
requested: six scene modules, all 50 matching library modules, six six-second
films, a hearth film, previews, capture records and an offline video index.
It has 82 files and a 42.45 MiB ZIP. Its source hashes match the published gallery
manifest and staged workspaces; all seven films decoded, and archive CRC and
extracted-file hashes passed. Subsequent refinement packages use distinct names
so this snapshot is never silently replaced.

No new paid coding-agent runs were made in this refinement. The A1/A2 scene
sources remain the retained Luna outputs; updated gallery rendering uses the
current shared library. Earlier Luna/Gemini trial results remain documented in
[VALIDATION.md](VALIDATION.md).

## Automated checks

**842 tests passed** across scene runtime/library behavior, Three.js rendering,
portability and scene prompts:

```bash
C3D_CACHE_DIR=/tmp/c3d-graphics-refine-v2 python -m pytest tests/scene_runtime tests/threejs_render tests/core/test_portability.py tests/scene_prompts -q -n4
```

The new stream optics test renders a submerged coloured checkerboard twice with
fixed geometry/time and different IOR. It checks actual displacement of colour
boundaries, which an alpha-only overlay cannot produce. Other additions cover
curved-channel CPU/GPU normals, reflector ownership, spectral energy and
environment ownership, rock concavity/topology, and seeded meadow morphology.
One existing Pillow deprecation warning remains.

New video sidecars record the SHA-256 of each staged source module, and capture
fails if those files change during rendering. Packaging validates those hashes
when available; earlier sidecars remain explicitly marked as legacy evidence.

The rebuilt gallery retains 60 stills, eight six-second films and a separate
hearth film, all at 1280×720. All nine capture records match the final staged
source hashes and report zero shader/console errors. Encoded ocean and stream
frames at 0, 2 and 4 seconds were inspected for actual motion.

Browser validation passed all eight scenes, 20 cameras, video decoding,
pause/scrub/tab switching, boot cancellation and all four comparison sliders,
with zero browser errors. Its English-source scan checked 1,695 files; the
final expanded source/document/JSON scan checked 2,014 files, including both
transfer packages, and found no Han characters.

The separate refined transfer package is
`output/packages/graphics_lab_first_six_refined_2026-09-23.zip`: 81 files,
44,711,120 bytes (42.64 MiB), with published/staged/capture-time source validation
and ZIP CRC/extracted-file hash verification. The original requested snapshot
remains unchanged beside it.
