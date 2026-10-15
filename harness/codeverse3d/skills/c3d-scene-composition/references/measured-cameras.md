# The camera instruments, and what each finding asks you to change

Companion to `c3d-scene-composition`. Constants read from
`codeverse3d/spatial/frame_metrics.py`, `codeverse3d/judges/rubrics/scene_v2.yaml`,
`runtime_js/lib/backdrop.mjs`, `runtime_js/lib/host_census.mjs` and
`runtime_js/lib/scene_host.mjs` on 2026-08-25, re-read 2026-10-15.

## 1. Measure the content, then place the eye

The content box is what the cameras are derived from — measured, with the sky excluded:

```js
const box = new THREE.Box3();
scene.traverse(o => { if (o.isMesh && !/sky|dome/i.test(o.name)) box.expandByObject(o); });
const size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3());
const eye = (x, z) => heightAt(x, z) + EYE;        // the brief's eye height, on the terrain
```

From there every camera is the brief's: what it looks at, from where, how many. A hero's
position comes from the zone that built it, not from a number you remember. The first
camera in the array is measured as the establishing shot (>= 20 % of its frame content).
`establishingShot`, `seat` and `shot` in `lib/place.js` derive a camera from an object's
measured box and the surfaces you pass, if you want the arithmetic done for you.

## 2. When a finding fires: what to change

| finding | the number that is wrong | change |
|---|---|---|
| `camera inside / touching geometry: inside ['CityTower']` (cap 0.50) | the camera sits in a mesh box | move it out along the lookAt direction until `nearest_hit_m >= 0.5`; do not shrink the building |
| `camera is N m below the ground level` with a dark or empty frame (cap 0.50) | you used an absolute y | `position[1] = heightAt(x, z) + 1.6` |
| same finding but the frame renders fine | nothing, probably | a hill or terrace is above the camera; verify with the frame, then leave it |
| `establishing shot shows too little content: content 11% (sky 52%, ground 37%)` (cap 0.60) | the shot is mostly backdrop | bring the camera closer to the subject, lower it, or put more of the place in that view — a bigger ground plane makes this **worse**, because ground is not content |
| `flat frame: one luminance band holds 100% of the pixels` | no tonal range | aim at content, add a shadow-casting key, vary materials; recorded twice in one desert-canyon run at 85% and 100% |
| `frame too dark: mean luminance 0.06` | exposure | that is `c3d-scene-lighting`'s table, not composition |

## 3. Naming, because names change the classification

| you want it to be | name it | and shape it |
|---|---|---|
| ground | `Ground`, `Terrain`, `Water`, `Sand`, `Grass` | span > 20 m, height < 6% of span |
| sky | `SkyDome`, `Stars`, `Clouds` | span > 50 m |
| content (counts toward `content_frac`) | anything else | or make it an `InstancedMesh`, which is content unconditionally |

The trap is the accidental one: a wide flat mesh over 40 m across and flatter than 2% of its
span is classified **ground whatever its name**, so a big deck, jetty or plaza silently stops
counting as content, and its top surface becomes `ground_y` for every camera check.

## 4. Cheap self-check before you finish

```
scene_probe    -> triangles, draws, console errors, custom-shader mesh count
scene_views    -> camera_checks per camera: mean_lum, modal_frac, content_frac,
                  nearest_hit_m, eye_height_m, ground_y
```

Six numbers per camera. Read them; the gate that scores you reads exactly the same six.
