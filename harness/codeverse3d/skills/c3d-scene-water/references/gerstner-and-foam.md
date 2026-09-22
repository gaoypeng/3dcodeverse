# Gerstner waves and shoreline foam — the deeper cut

## Why Gerstner and not displaced noise

Noise-displaced water bobs; Gerstner water TRAVELS. Each wave moves vertices in
a circle — horizontally toward the crest, vertically over it — which is what
sharpens crests and flattens troughs like real deep-water waves.

Per wave i, with direction D_i (unit, xz), wavelength L_i, amplitude A_i,
steepness Q_i:

    k = 2*PI / L
    c = sqrt(9.8 / k)              // deep-water phase speed, m/s
    f = k * dot(D, p.xz) - k*c*t
    xz += Q * A * D * cos(f)
    y  += A * sin(f)

* Sum 3-4 waves: one long swell (L 18-40 m, A 0.4-0.8), two mid chops at
  30-60 degrees off the swell, one short ripple. Randomize phases.
* STABILITY: sum of Q*A*k must stay below 1.0, or crests loop into
  self-intersecting curls. Budget Q per wave as Q_total / n_waves.
* Normals: finite-difference the DISPLACED positions (sample the full sum at
  p+dx and p+dz). Analytic normals from one wave look wrong the moment you sum.
* Grid: 256x256 minimum for L >= 18 m; halve the wavelength, double the grid.

## Foam, ranked by payoff

1. SHORELINE foam (best): needs the shared height function. In the fragment
   shader, `shore = 1. - smoothstep(0.0, 1.2, waterLevel - h)` then multiply by
   scrolling fbm and add as near-white. Hugs banks, islands, piers.
2. Contact foam around obstacles: same trick with distance to the obstacle's
   footprint circle.
3. Crest foam (skip unless asked): threshold on the Jacobian of the Gerstner
   displacement; expensive to get right, subtle in stills.

## Frozen-frame checks

At the fixed capture time the surface must show asymmetric crests (travel
direction readable), no through-floor dips at the shore (bias the plane down
0.02), and foam only where geometry explains it.
