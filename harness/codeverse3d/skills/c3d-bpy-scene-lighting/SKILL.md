---
name: c3d-bpy-scene-lighting
description: "Use when lighting a scene_blender env, zone, refine or rebuild session, or when scene_frames called a Blender frame too dark, blown out or flat. The harness renders with Cycles under AgX at exposure 0 and owns every render setting, so lamp energies, the world strength and the albedos are the only dials. Gives what the frame gate measures with its thresholds, the exposure bands measured for sky, sun, night, practical and interior setups under this renderer, the interface facts (the world is build_env's, what the census counts as atmosphere, which ceilings the overview rig lifts), lib.sky and lib.lighting as an optional toolbox, why an emissive mesh is not a lamp at 32 samples, and how to prove the band on every camera including the harness rig."
license: Apache-2.0
compatibility: Blender 5.2.2 LTS headless, scene_blender runtime; Cycles 32 samples + OIDN, AgX view transform, look None, exposure 0 — all harness-owned (render_bpy_scene.setup_render).
metadata:
  evidence: mixed
  evidence_note: 'The gate thresholds are read from live code (spatial/frame_metrics.py, runtime_js/lib/host_metrics.mjs) and the render setup from languages/wrappers/render_bpy_scene.py on 2026-10-14; the exposure bands were measured that day on Blender 5.2.2 LTS with that exact setup on a synthetic test scene (references/exposure-bands.md says how), not on run output. The corpus half is empty: the argon loop iteration 0 (2026-10-15) has not yet been tabulated per frame finding.'
  verified: "2026-10-15"
  target_metric: "bpy_exposure_findings"
  target_direction: "down"
  target_unit: "findings per run"
  target_measurable: "true"
  target_baseline: "n=0 — the argon loop iteration 0 (2026-10-15, 30 scene_blender runs) is the first sample; not yet tabulated"
---

# Lighting a Cycles scene the frame gate will accept

## What is fixed, and what is measured

The harness renders the authored cameras and the first overview rig views with Cycles at 32
samples plus denoising, AgX view transform, look None, exposure 0 — and it owns every one of
those settings: an assignment to `scene.render`, `scene.cycles` or `scene.view_settings` is a lint
ERROR (harness-owned). There is no exposure knob. Lamp energies, the world strength and the
albedos are the whole of your control.

The gate (`scene_frames`) reads the Blender PNGs with the statistics it reads three.js frames
with — per camera `mean_lum`, `lum_std`, `dark_frac`, `blown_frac` and `modal_frac` in the
`camera_checks` of `metrics.json`:

| finding | fires when | authored camera | harness rig view |
|---|---|---|---|
| `dark_frame` | mean luminance below 0.12, or more than 35 % of the pixels near black | ERROR | WARN |
| `dark_frame`, "dim but lit" | dark by the mean, but with contrast (`lum_std`) of at least 0.12 and not mostly black | WARN | WARN |
| `blown_frame` | more than 20 % of the pixels pure white | ERROR | WARN |
| `flat_frame` | one luminance band holds more than 85 % of the frame | WARN; ERROR above 92 % | WARN |

An ERROR of each kind is a score ceiling in the scene rubric, and every finding's hint carries the
scene_blender wording (lamps, world nodes, `ctx.height_at`), not the three.js one. The rig views
(`overview_*`) are WARN-only; the judge reads those pictures too.

## The bands, measured on this renderer

Measured 2026-10-14 on Blender 5.2.2 LTS with the harness's exact render setup, on a synthetic
scene (a flat ground plane of linear albedo a and six boxes; an eye-level camera with sky in the
top third, and a camera at the overview rig's angle). Numbers are `mean_lum`; the full tables
and the other four statistics are in `references/exposure-bands.md`.

| setup | `mean_lum` | what it says |
|---|---|---|
| the multiple-scattering sky alone, sun disc ON (a hand-built world), strength 0.05 / 0.2 / 1.0 (eye camera) | 0.63 / 0.82 / 0.96 | with its disc the physical sky IS daylight at strength 0.05-0.1; at 1.0 the sky starts to clip |
| sky 0.2 with the disc off + ONE sun lamp at 1 / 3 / 10 W per square metre, a = 0.3 (overview) | 0.57 / 0.63 / 0.73 | the lamp is a weak dial under AgX |
| the same sun at 3, ground albedo 0.03 / 0.15 / 0.6 (overview) | 0.32 / 0.52 / 0.72 | albedo is the strong dial |
| sun lamp 3 with the sky's disc ALSO on (two suns) | 0.83 | the doubled sun: washed, shadows fight |
| dark-blue night world alone / with a moon sun of 0.2 / 0.5 | 0.02 / 0.06 / 0.10 | too dark, and evenly so: no low-key escape, because `lum_std` stays under 0.05 |
| that night + two practical area lamps 2.5 m up at 45 / 150 / 500 W | 0.05 / 0.08 / 0.14 | 45 W is a look, not a light; 500 W per lamp passes, with contrast |
| that night + two fixture-sized emissive spheres at strength 5 / 80 / 320, no lamps | 0.03 / 0.05 / 0.09 | emission does not carry a frame; it needs a lamp beside it |
| a closed 6 m room, one 1 m ceiling area lamp at 50 / 200 / 800 W | 0.34 / 0.61 / 0.82 | interiors are easy: tens to hundreds of watts |

Two things fall out of the table. Under any daylight world nothing is dark and the risk is the
other two kinds: a uniformly coloured ground seen from the overview rig sat in the flat band
(`modal_frac` 0.84-0.95) in every daylight row, so value variance on the ground and
shadow-casting content are what break it, not more light. And at night the lamps that make the
picture pass are an order of magnitude stronger than the ones that make the fixtures glow.

## Interface, requirements, and ways that measured inside the band

* **Interface: the world is `build_env`'s.** A world created or assigned anywhere else is a
  harness-owned lint ERROR. Zones add lamps.
* **Requirement: one consistent sun direction, shadows that do not fight** (judged) **and every
  frame in the band** (measured). A physical sky with its disc on plus a sun lamp measured 0.83,
  washed, with doubled shadows; so did a second sun lamp in a zone. `lib.sky.sun_world(scene, elevation_deg=..., azimuth_deg=..., strength=..., dust=...)`
  and `lib.lighting.sun(collection, azimuth_deg=..., elevation_deg=..., color=..., energy=..., angle_deg=...)`
  are a matched pair (the world's disc off, the lamp casting the shadows, the same compass
  angles); a hand-built world is fine when it meets the same. A dark day moves with the albedos
  and the sun's `energy` more than with the world strength (the second and third rows).
* **The brief's hour decides the palette; the band still applies.** `lib.sky.gradient_world(scene, stops, clouds=..., strength=...)`
  and `lib.sky.night_world(scene, color=..., strength=...)` with a low or cool `sun` are measured
  ways to sit in it for dusk, overcast and night; a warm key under a cool sky (a sky texture
  gives that for free) is what keeps shade from reading black or the frame from reading one
  orange tone (judged). Soft shadows come from a light's size (`angle` on a sun, `size` on an
  area lamp), which is one lamp, not two.
* **Interface: atmosphere is counted from the census.** The `no_fog` plan check fires when the
  census finds no volume at all; a volume-only mesh (only the Material Output's Volume socket
  linked) counts, and the placement gate ignores it. `lib.sky.haze(collection, loc, size, density=..., color=...)`
  makes one as a box; a local mist over a pond or a hollow, or a light shaft (a higher
  `Anisotropy` where the sun crosses an opening) is the same material on any volume-only mesh. A
  world volume over a large scene measured as swallowing the sun and flattening the frame. How
  thick the air is, and its colour, is the brief's; `fog_short` warns when the density would
  dissolve the plan's own span.
* **Lamps in zones are yours** — `lib.lighting.area_light`, `point_light`, `spot`, and
  `practical(collection, name, loc, color=..., energy=..., size=..., target=...)`, a small soft
  area lamp beside a lantern, window, fire or sign, aimed with `aim`. Its default energy is the
  fixture's own glow on the surfaces beside it, not illumination that carries a frame: two of
  them at that default measured `mean_lum` 0.05 (an ERROR); hundreds of watts each is where a
  night frame passed. The lib creates lamps through `bpy.data.lights.new`; operators in a loop
  are a hang-risk lint finding, and `bpy.ops.object.light_add` wants a view-layer context the
  build does not give.
* **An emissive mesh is the look, not the light.** Cycles does scatter light from an emissive
  surface, but a fixture-sized emitter throws almost nothing a frame can measure: two 0.15 m
  emissive spheres at the strengths that make a lantern read as lit left the night frame at
  `mean_lum` 0.03, and at forty times that strength — white blobs — it was still an ERROR; the
  same scene passed once each sphere had a 500 W `practical` beside it (reference, section 4).
  Emission on the fixture (`lib.materials.emissive`), illumination in a lamp, and the emission
  modest: AgX pulls a saturated emitter toward white, so a strong neon tube renders as a white bar.
* **Interiors.** The band applies on every authored camera inside, and the overview rig lifts a
  lid it can identify: a mesh named `Ceiling…` whose parent is named `RoomShell…` and whose
  evaluated bbox top is below the camera is hidden for that rig frame (`lift_lids` in
  `render_bpy_scene`, the three.js host's rule). `lib.architecture.room_shell` builds a shell
  named that way; a ceiling area lamp sized to the room measured 0.34-0.82 at 50-800 W, and an
  emissive window pane with an `area_light` just inside it is one way to light a room by day. The
  indoor placement tolerance is tighter, so a lamp on a table is a `rest_on` case too.
* **Albedo before light.** A ground whose Base Color is near black stays dark under any sun, and a
  plaster wall clips under the sun that lights a lawn correctly. The large surfaces' albedo is the
  strong dial (the third row), the sun the weak one, the practicals the local one.

## Night: the band, not the floor

Target: the "dim but lit" band on every authored camera — mean low, `lum_std` at least 0.12, not
mostly black. Both ends fail: a sky-lit average on a lantern scene is a grey wash (a flat frame,
and the judge's unlit-or-blown defect), and glowing fixtures with no lamps behind them are a black
frame with stickers. Measured on this renderer: a low night world, each fixture paired with a
`practical` at hundreds of watts so the pools overlap into a readable picture, dark materials
with a low but non-zero Base Color and shadows left dark reached the band; a fill lamp that lifts
the mean pushed it to a flat wash.

## The hero must be visible

The frames gate also reports `hero_unseen` and `hero_small_in_its_camera`; a hero in shadow at
night is a hero the judge cannot find. Its own key — a `practical` or a `spot` aimed at it with
`aim(obj, target)` — and a look at its camera's frame.

## Prove it

`scene_views(cameras="all", times=[0.0])`, then read `camera_checks` per camera against the
first table: the same five numbers the gate uses. A Blender view costs seconds, so render once
per change, all cameras, and fix whatever the finding names on EVERY camera, rig views included.

Full measured tables, the night and interior setups as code: `references/exposure-bands.md`.
