# Gerstner waves and shoreline foam — the deeper cut

`makeOceanSurface` in `lib/ocean.js` already does all of this; what follows is for a water
body the library's options cannot shape.

## Why Gerstner and not displaced noise

Noise-displaced water bobs; Gerstner water TRAVELS. Each wave moves vertices in a circle —
horizontally toward the crest, vertically over it — which is what sharpens crests and
flattens troughs like real deep-water waves.

Per wave i, with direction D_i (unit, xz), wavelength L_i, amplitude A_i, steepness Q_i:

    k = 2*PI / L
    c = sqrt(9.8 / k)              // deep-water phase speed, m/s
    f = k * dot(D, p.xz) - k*c*t
    xz += Q * A * D * cos(f)
    y  += A * sin(f)

* Sum a few waves: one long swell, a couple of mid chops some tens of degrees off the
  swell, one short ripple; randomize phases.
* STABILITY: the sum of Q*A*k stays below 1.0, or crests loop into self-intersecting
  curls. Budget Q per wave as a share of that total.
* Normals: finite-difference the DISPLACED positions (sample the full sum at p+dx and
  p+dz). Analytic normals from one wave look wrong the moment you sum.
* Grid density follows the shortest wavelength: halve the wavelength, double the grid.

## Foam, ranked by payoff

1. SHORELINE foam (best): needs the shared height function. In the fragment shader,
   `shore = 1. - smoothstep(0.0, 1.2, waterLevel - h)` then multiply by scrolling fbm and
   add as near-white. Hugs banks, islands, piers.
2. Contact foam around obstacles: the same trick with distance to the obstacle's
   footprint.
3. Crest foam: a threshold on the Jacobian of the Gerstner displacement; expensive to get
   right, subtle in stills.

## Frozen-frame checks

At the fixed capture time the surface shows asymmetric crests (travel direction readable),
no through-floor dips at the shore (the plane biased a little below the bank), and foam
only where geometry explains it.
