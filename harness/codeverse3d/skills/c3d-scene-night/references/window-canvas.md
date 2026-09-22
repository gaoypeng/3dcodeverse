# The emissive window canvas — exact construction

One CanvasTexture per building ARCHETYPE. 256x256 canvas:

1. Fill the facade colour first — near-black warm gray (#141210), not #000.
2. Window grid: compute rows/cols from storey height (~3 m) and window pitch
   (~1.8 m) so the texture's repeat over the building gives plausible storeys.
3. Per window rect (inset ~30% of the cell): lit with probability ~0.35.
   Lit colour: warm white base (#ffd9a0) with per-window jitter of +-8% hue and
   +-15% lightness; roughly 1 in 12 lit windows TV-blue (#9fc4ff) at lower
   alpha. Unlit windows: slightly darker than the facade, never pure black.
4. A faint 1-px sill line under each window sells the relief at distance.

Use the SAME canvas as `map` and `emissiveMap`; `emissiveIntensity` 1-2;
`roughness` 0.9 on the facade so the windows, not the wall, carry the light.

Pair each large lit facade with ONE dim warm PointLight at street level in
front of it (intensity ~0.3, decay 2, distance ~2 storeys): emissive maps do
not light the street, and the missing bounce is what makes fake buildings
float in the dark.

Distant buildings: skip geometry relief entirely — a box with this texture
reads correctly past ~80 m. Near buildings: model the ground floor for real
(doors, awnings) and let the texture carry storeys 2+.
