# Terrain and scatter — the deeper cut

## Domain warp, in full

Plain fbm terrain reads as static. Warping the INPUT coordinates by another
noise breaks the isotropy into ridgelines and valleys:

```js
function height(x, z) {
  const wx = x + 30 * fbm(x * 0.008, z * 0.008);
  const wz = z + 30 * fbm(x * 0.008 + 100, z * 0.008 + 100);
  return Math.pow(fbm(wx * 0.02, wz * 0.02), 1.6) * H_MAX;
}
```

* The warp amplitude (30) is in METRES — scale it with the scene.
* The power curve (1.6-2.2) flattens lowlands and steepens peaks; without it
  everything is rolling dough.
* This function is THE height authority: terrain mesh, water shoreline, every
  scattered instance's y, and building foundations all call it. Two height
  sources = floating trees, drowned paths (the placement gate measures both).

## Colour bands with jitter

Vertex colours, blended by smoothstep over height with a slope override:

    slope: normal.y < 0.65        -> rock gray
    height < shore + 1            -> wet sand
    < treeline                    -> grass (hue-jittered +-5%)
    < snowline                    -> rock
    else                          -> snow

Jitter every band's colour per-vertex by a high-frequency noise at +-5% value;
band edges get a 2-4 m smoothstep overlap, never a hard line.

## Scatter masks

Three multiplied gates, in order of visual weight:

1. Clump mask: low-frequency noise thresholded at ~0.5 — groves and clearings.
2. Slope rejection: no trees where normal.y < 0.75; no rocks on cliffs
   steeper than ~55 degrees (they roll off in the viewer's head).
3. Jittered grid: spacing = target mean distance, +-40% uniform jitter — the
   Poisson look without the Poisson cost.

Per instance: y from the height authority sunk 0.1 m, random Y rotation, scale
0.7-1.3, tilt up to ~4 degrees along the slope normal, hue/lightness jitter
from the materials checklist. InstancedMesh for anything over ~40 copies.
