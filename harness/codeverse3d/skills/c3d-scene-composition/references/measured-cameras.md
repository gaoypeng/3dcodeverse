# From bounds to cameras, by arithmetic

Companion to `c3d-scene-composition`. Constants read from
`codeverse3d/spatial/frame_metrics.py`, `codeverse3d/judges/rubrics/scene_v1.yaml`,
`runtime_js/lib/backdrop.mjs`, `runtime_js/lib/host_census.mjs` and
`runtime_js/lib/scene_host.mjs` on 2026-08-25.

## 1. One derivation, top to bottom

```js
import { BOUNDS, heightAt } from './env.js';

const span   = [BOUNDS.max[0] - BOUNDS.min[0], BOUNDS.max[2] - BOUNDS.min[2]];
const diag   = Math.hypot(span[0], span[1]);              // horizontal diagonal of the plan bounds
const fogSc  = Math.min(2.5, Math.max(0.3, diag / 100));  // the cookbook fog table is for 100 m
const fogFar = FOG_FAR_FOR_TIME_OF_DAY * fogSc;           // e.g. dusk = 140 * fogSc
const ground = 2.4 * fogFar;                              // ground plane side length, minimum
const ringR  = Math.max(0.6 * fogFar, diag);              // silhouette ring radius (2 x half-diagonal)
const skyR   = ringR * 1.35;                              // the dome must contain the ring
```

A 45 m garden at dusk: `fogSc = 0.45`, `fogFar = 63 m`, ground >= 151 m, ring at 38 m, dome
at 51 m. A 240 m valley at noon: `fogSc = 2.4`, `fogFar = 528 m`, ground >= 1267 m, ring at
317 m. Those are the numbers; do not carry the 100 m table across unscaled.

## 2. The three cameras, computed

```js
function planCameras(THREE, scene, heightAt, hero /* {x, z} of the hero prop */) {
  const box = new THREE.Box3();
  scene.traverse(o => { if (o.isMesh && !/sky|dome|horizon/i.test(o.name)) box.expandByObject(o); });
  const size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3());
  const d = Math.max(size.x, size.z);
  const eye = (x, z) => heightAt(x, z) + 1.6;
  return [
    { name: 'Establishing', fov: 42,
      position: [c.x + d * 0.55, size.y * 1.1 + 6, c.z + d * 0.6], lookAt: [c.x, 2, c.z] },
    { name: 'CourtyardMid', fov: 50,
      position: [c.x + 8, eye(c.x + 8, c.z + 6), c.z + 6], lookAt: [c.x, 2, c.z - 6] },
    { name: 'LanternDetail', fov: 45,
      position: [hero.x + 2.5, eye(hero.x + 2.5, hero.z + 1.5), hero.z + 1.5],
      lookAt: [hero.x, 0.6, hero.z] },
  ];
}
```

`hero` is the hero prop's own position — take it from the zone that built it, not from a
number you remember. Then check each one against the table in the skill before you
build anything else.

## 3. When a finding fires: what to change

| finding | the number that is wrong | change |
|---|---|---|
| `camera inside / touching geometry: inside ['CityTower']` (cap 0.50) | the camera sits in a mesh box | move it out along the lookAt direction until `nearest_hit_m >= 0.5`; do not shrink the building |
| `camera is N m below the ground level` with a dark or empty frame (cap 0.50) | you used an absolute y | `position[1] = heightAt(x, z) + 1.6` |
| same finding but the frame renders fine | nothing, probably | a hill or terrace is above the camera; verify with the frame, then leave it |
| `establishing shot shows too little content: content 11% (sky 52%, ground 37%)` (cap 0.60) | the shot is mostly backdrop | pull the camera in to about 1.2 x the content span, lower it, or add instanced content — a bigger ground plane makes this **worse**, because ground is not content |
| `flat frame: one luminance band holds 100% of the pixels` | no tonal range | aim at content, add a shadow-casting key, vary materials; recorded twice in one desert-canyon run at 85% and 100% |
| `frame too dark: mean luminance 0.06` | exposure | that is `c3d-scene-lighting`'s table, not composition |

## 4. Naming, because names change the classification

| you want it to be | name it | and shape it |
|---|---|---|
| ground | `Ground`, `Terrain`, `Water`, `Sand`, `Grass` | span > 20 m, height < 6% of span |
| sky | `SkyDome`, `Stars`, `Clouds` | span > 50 m |
| content (counts toward `content_frac`) | anything else | or make it an `InstancedMesh`, which is content unconditionally |

The trap is the accidental one: a wide flat mesh over 40 m across and flatter than 2% of its
span is classified **ground whatever its name**, so a big deck, jetty or plaza silently stops
counting as content, and its top surface becomes `ground_y` for every camera check.

## 5. Cheap self-check before you finish

```
scene_probe    -> triangles, draw calls, fps, console errors, custom-shader mesh count
scene_views    -> camera_checks per camera: mean_lum, modal_frac, content_frac,
                  nearest_hit_m, eye_height_m, ground_y
```

Six numbers per camera. Read them; the gate that scores you reads exactly the same six.
