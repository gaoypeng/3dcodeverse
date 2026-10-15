# Terrain and scatter — the deeper cut

Optional practices for a brief whose place has rolling or broken ground and natural
cover. A quay, a plaza, a street or a cave has other grounds; the brief decides.

## Domain warp

Plain fbm terrain reads as static. Warping the INPUT coordinates by another noise breaks
the isotropy into ridgelines and valleys:

```js
function height(x, z) {
  const wx = x + W * fbm2(x * 0.008, z * 0.008);
  const wz = z + W * fbm2(x * 0.008 + 100, z * 0.008 + 100);
  return Math.pow(fbm2(wx * 0.02, wz * 0.02), P) * H_MAX;
}
```

* `W` (the warp) is in METRES — scale it with the scene; `fbm2` is `lib/noise.js`.
* The power curve `P` above 1 flattens lowlands and steepens peaks; without it everything
  is rolling dough.
* This function is THE height authority: terrain mesh, water shoreline, every scattered
  instance's y, and building foundations all call it. Two height sources = floating trees,
  drowned paths (the placement gate measures both).
* A landform is more than noise: a slope that drains to the water, a flat where the path
  runs, a terrace the buildings sit on. Add those terms to the function deliberately.

## Colour by slope and height

Vertex colours or `patchSlopeSplat` (`lib/terrain_shade.js`): a steep-slope material
(rock, bare earth) overriding by `normal.y`, then height bands blended by smoothstep with
the materials the place has, each band jittered by a high-frequency noise, band edges
overlapping rather than a hard line.

## Scatter masks

Three multiplied gates, in order of visual weight:

1. Clump mask: low-frequency noise thresholded — groves and clearings.
2. Slope rejection: no trees on cliffs; rocks roll off steep faces in the viewer's head.
3. A jittered grid (`scatterGrid` from `lib/instancing.js`) — the Poisson look without
   the Poisson cost.

Per instance: y from the height authority, yaw and scale varied, hue/lightness from
`tint`; `InstancedMesh` for anything repeated more than a handful of times (the contract's
efficiency rule). Keep each authored camera's first metres free of scatter: the frame gate
needs `nearest_hit_m` >= 0.5 m.
