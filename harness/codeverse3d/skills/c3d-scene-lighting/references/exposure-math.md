# Exposure arithmetic for this renderer

The renderer is not yours to change (`runtime_js/lib/browser/renderer.js`): ACES Filmic
tone mapping, `toneMappingExposure = 1.0`, sRGB output, three r0.182. So the mapping from
"how bright is this surface in the scene" to "what number does the gate read" is fixed and
computable. Every figure here was computed from those settings on 2026-08-25.

## Step 1 — a hex colour is not its brightness

three decodes every hex colour from sRGB into the linear working space before lighting.
The relative luminance of the decoded colour is the *albedo* that matters.

## Step 2 — albedo, through the tone curve, to the frame value

Read "key x1" as a surface that is fully and directly lit. `frame` is the value the gate
averages into `mean_lum`.

| swatch | linear albedo | frame at key x0.5 | x1 | x2 |
|---|---|---|---|---|
| `0x1d3d22 dark foliage` | 0.037 | 0.07 | 0.15 | 0.27 |
| `0x2f6b2a mid foliage` | 0.113 | 0.22 | 0.37 | 0.56 |
| `0x5a3b22 bark` | 0.054 | 0.11 | 0.21 | 0.36 |
| `0x5e7a3a grass` | 0.166 | 0.30 | 0.48 | 0.67 |
| `0x6b6152 dry earth` | 0.123 | 0.23 | 0.39 | 0.59 |
| `0x8e9aa6 slate` | 0.316 | 0.46 | 0.66 | 0.82 |
| `0xc3cad1 pale stone` | 0.584 | 0.64 | 0.80 | 0.91 |
| `0xf0f4f8 plaster` | 0.900 | 0.75 | 0.87 | 0.94 |


Dark foliage lands at 0.15 fully lit and 0.07 at half key — which is why a night
woodland scene reads black no matter how the lights are tuned, and the only real fix is a lighter albedo (or emissive practicals that put bright
pixels in the frame).

## Step 3 — where the headroom is

|---|---|
| 0.12 | 0.029 |
| 0.15 | 0.037 |
| 0.20 | 0.051 |
| 0.30 | 0.085 |
| 0.50 | 0.181 |

The curve is steep at the bottom: getting from a frame value of 0.12 to 0.20 needs the
linear scene luminance to go from 0.029 to 0.051, i.e. roughly +75 % of light — but getting
from 0.30 to 0.50 needs it to more than double. At the dark end, modest increases move
the number a lot; that is the cheapest place to fix a dark scene.

## Step 4 — sanity numbers for the coloured-darkness rows

Relative luminance of the cookbook's dusk and night palette entries, so you can see that
"dark" there means roughly 0.03 and not 0.00:

| swatch | linear luminance |
|---|---|
| dusk zenith `0x33285c` | 0.030 |
| dusk horizon `0xd9703a` | 0.267 |
| night zenith `0x0d1430` | 0.008 |
| night horizon `0x22305a` | 0.032 |
| dusk hemisphere ground `0x3a2e22` | 0.030 |
| night hemisphere ground `0x1a1c24` | 0.012 |

The horizon band is the brightest thing in a dusk frame by an order of magnitude. That is
why a black `scene.background` fails a scene that would otherwise pass: it deletes the one
region carrying the frame's mean.

## Step 5 — the check

`scene_views` writes `camera_checks` into `metrics.json` with `mean_lum`, `dark_frac`,
`blown_frac` and `modal_frac` per camera — the same four numbers the gate reads. Read them,
and fix whatever finding the gate names.
