# The emissive window canvas — the hand-made version

`makeNightWindows` and `patchWindowInteriors` in `lib/windows.js` do this for you; what
follows is how to build the same thing by hand when a facade needs a layout the library
does not have.

One CanvasTexture per building ARCHETYPE, not per building:

1. Fill the facade colour first — a near-black warm grey, not pure black.
2. A window grid whose rows and columns come from the storey height and window pitch,
   so the texture's repeat over the building gives plausible storeys.
3. Per window rect, lit with a probability well under half, in a warm white with
   per-window hue and lightness jitter and the occasional cooler TV-blue at lower alpha.
   Unlit windows slightly darker than the facade, never pure black.
4. A faint sill line under each window sells the relief at distance.

Use the SAME canvas as `map` and `emissiveMap`, with a high facade roughness so the windows,
not the wall, carry the light. The post chain blooms the emissive; a peak of a few units is
enough.

Pair each large lit facade with ONE dim warm PointLight at street level in front of it:
emissive maps do not light the street, and the missing bounce is what makes fake buildings
float in the dark.

Distant buildings: a box with this texture reads correctly at distance. Near buildings:
model the ground floor for real (doors, awnings) and let the texture carry the storeys
above.
