# Exposure bands under Cycles + AgX at exposure 0

Companion to `c3d-bpy-scene-lighting`. The thresholds are `codeverse3d/spatial/frame_metrics.py`'s
(read 2026-10-14); the pixel statistics are `runtime_js/lib/host_metrics.mjs`'s and are applied to
the Blender PNGs unchanged: luminance is the Rec. 709 weighting of the 8-bit sRGB pixels, "near
black" is the bottom few of the 256 levels, "pure white" is all three channels at the top, and the
modal band is the fullest of eight equal luminance bands.

## 1. The gate's numbers

| statistic | threshold | finding |
|---|---|---|
| `mean_lum` | below 0.12 | `dark_frame` (ERROR on an authored camera) |
| `dark_frac` | above 35 % | `dark_frame` |
| `lum_std` | at least 0.12 while dark by the mean and not mostly black | the same finding downgraded to "dim but lit", a WARN |
| `blown_frac` | above 20 % | `blown_frame` |
| `modal_frac` | above 85 % | `flat_frame` WARN; ERROR above 92 % on an authored camera |

A frame that is dark is not also reported flat; a frame can be dark by the mean with not one
near-black pixel, or bright on average and still fail on the black fraction — fix whichever one
the message names.

## 2. How the bands were measured

Blender 5.2.2 LTS, the harness's `setup_render` verbatim (Cycles, CUDA, 32 samples, adaptive,
OIDN, AgX, look None, exposure 0, sRGB 8-bit PNG), 192 x 108 pixels, 2026-10-14. Scene: a 200 m
ground plane of linear albedo a (roughness 0.85), six boxes 0.9-3 m of albedo 0.4. Cameras: eye
level 1.6 m looking slightly down with the sky in the top third (`eye`), and a camera 25 m out at
the overview rig's azimuth and elevation (`overview`). Sun elevation 35 degrees. These are bands
for a bare scene, not rules: real content adds shadow, colour and contrast.

## 3. Daylight

The multiple-scattering sky alone, sun disc on, a = 0.3:

| sky strength | eye `mean_lum` | overview `mean_lum` | note |
|---|---|---|---|
| 0.02 | 0.45 | 0.44 | an overcast-looking day |
| 0.05 | 0.63 | 0.62 | daylight |
| 0.1 | 0.74 | 0.73 | bright daylight |
| 0.2 | 0.82 | 0.82 | hazy, washed |
| 0.5 | 0.91 | 0.91 | the ground is pale |
| 1.0 | 0.96 | 0.96 | `blown_frac` 0.11 on the eye camera: the sky clips |

Sky at 0.2 with the disc off plus ONE sun lamp (overview camera):

| sun lamp (W per square metre) | a = 0.03 | a = 0.08 | a = 0.15 | a = 0.3 | a = 0.6 |
|---|---|---|---|---|---|
| 1 | 0.28 | 0.37 | 0.45 | 0.57 | 0.68 |
| 3 | 0.32 | 0.42 | 0.52 | 0.63 | 0.72 |
| 5 | 0.36 | 0.47 | 0.56 | 0.67 | 0.76 |
| 10 | 0.43 | 0.55 | 0.64 | 0.73 | 0.81 |

Read the rows and the columns against each other: a tenfold sun moves the frame by about 0.15,
a twentyfold albedo by about 0.4. The eye camera, with the sky in it, sat at 0.59-0.81 across
the same grid — the sky carries an eye-level frame's mean on its own. With the sky's disc left on
beside the lamp (sun 3, a = 0.3) the overview rose from 0.63 to 0.83: two suns.

`modal_frac` on the overview camera was 0.80-0.95 in every daylight row: a single-colour ground
lit evenly IS a flat frame by the gate's definition. Value variance on the ground, shadows from
content and a horizon in the frame are what move it, not the sun.

## 4. Night

Flat dark-blue world (a night sky colour at strength 0.3), a = 0.3, no sun:

| setup | eye `mean_lum` / `lum_std` / `dark_frac` | overview | verdict |
|---|---|---|---|
| world alone | 0.02 / 0.02 / 0.57 | 0.01 / 0.00 / 1.00 | `dark_frame` ERROR |
| + moon sun 0.05 | 0.03 / 0.01 / 0.57 | 0.03 / 0.01 / 1.00 | ERROR |
| + moon sun 0.2 | 0.06 / 0.02 / 0.02 | 0.07 / 0.02 / 0.08 | ERROR: dark by the mean, no contrast to rescue it |
| + moon sun 0.5 | 0.10 / 0.05 / 0.01 | 0.13 / 0.03 / 0.07 | eye ERROR, overview passes |
| + two point lamps 3 m up, 100 W | 0.06 / 0.04 / 0.11 | 0.05 / 0.06 / 0.59 | ERROR |
| + two point lamps, 500 W | 0.13 / 0.12 / 0.07 | 0.14 / 0.13 / 0.21 | passes |
| + two point lamps, 2000 W | 0.25 / 0.22 / 0.04 | 0.28 / 0.20 / 0.09 | passes with room to spare |
| + two practical area lamps 2.5 m up, 45 W | 0.05 / 0.05 / 0.35 | 0.04 / 0.07 / 0.79 | ERROR: a glow-sized lamp, not illumination |
| + two practicals, 150 W | 0.08 / 0.09 / 0.14 | 0.08 / 0.12 / 0.55 | ERROR on the eye camera; the overview is dim-but-lit |
| + two practicals, 500 W | 0.14 / 0.16 / 0.07 | 0.14 / 0.18 / 0.28 | passes |
| + two 0.15 m emissive spheres 2.5 m up, strength 5 (no lamps) | 0.03 / 0.04 / 0.57 | 0.01 / 0.03 / 0.99 | ERROR: the fixtures glow, the ground does not |
| + the same spheres at strength 20 / 80 | 0.03 / 0.04 / 0.51 and 0.05 / 0.05 / 0.30 | 0.02 / 0.04 / 0.93 and 0.04 / 0.06 / 0.78 | ERROR |
| + the same spheres at strength 320 (white blobs) | 0.09 / 0.10 / 0.08 | 0.09 / 0.11 / 0.38 | ERROR on the eye camera still |
| + spheres at strength 8 AND two 45 W practicals | 0.05 / 0.06 / 0.35 | 0.04 / 0.07 / 0.81 | ERROR |
| + spheres at strength 8 AND two 500 W practicals | 0.14 / 0.16 / 0.08 | 0.12 / 0.17 / 0.38 | the eye camera passes; the overview rig is a dim-but-lit WARN |

The low-key escape needs contrast: an evenly moonlit plane has `lum_std` far below 0.12 at every
moon strength and fails as unlit however blue it is. Practicals create the contrast; what they
need is energy — hundreds of watts a lamp a few metres up, over a ground that is not black. An
emissive fixture is not that lamp: its strength decides how the fixture looks, and the frame's
mean barely moves until the fixture is a white blob, which the judge grades as blown.

```python
def lamp(ctx, name, kind, loc, color, watts, size=0.4):   # a lantern-lit night, one way
    light = bpy.data.lights.new(name, kind)
    light.energy, light.color = watts, color
    if kind == "AREA":
        light.size = size
    ob = bpy.data.objects.new(name, light)
    ob.location = loc
    ctx.collection.objects.link(ob)
    return ob
# each fixture: an emissive skin (the look) AND lamp(ctx, f"LanternLight_{i}", "AREA", ..., <hundreds of W>)
```

## 5. Interiors

A closed 6 m x 6 m x 3 m room (walls a = 0.6, floor a = 0.3, a table and a crate), one 1 m square
area lamp at the ceiling, an eye-level camera inside:

| lamp | `mean_lum` | `modal_frac` |
|---|---|---|
| 50 W | 0.34 | 0.45 |
| 200 W | 0.61 | 0.60 |
| 800 W | 0.82 | 0.85 |
| 3000 W | 0.94 | 0.89 |

Interiors need tens to hundreds of watts per lamp, and the failure mode above a few hundred is the
flat band (one pale tone), not clipping — AgX refused to blow out even the 3000 W room.

```python
def build_env(ctx):                       # a lit room
    ...                                   # the shell: walls, floor and a lid named Ceiling... under RoomShell...
    lid_lamp = bpy.data.lights.new("CeilingLight_0", "AREA")
    lid_lamp.energy, lid_lamp.size, lid_lamp.shape = <tens to hundreds of watts>, <about a metre>, "SQUARE"
    ob = bpy.data.objects.new("CeilingLight_0", lid_lamp)
    ob.location = (<cx>, <cy>, <just under the ceiling>)   # an area lamp shines down its -Z
    ctx.collection.objects.link(ob)
```

## 6. The check

`scene_views(cameras="all", times=[0.0])` writes `camera_checks` with the five statistics per
camera, authored and rig. Read them; then change one dial at a time.
