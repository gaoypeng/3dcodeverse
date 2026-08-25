"""Starter files for ``scene_threejs`` workspaces.

Without a plan: the complete example scene from ``starter/src`` (ground + sky
gradient shader + instanced pine trees + animated windmill/lantern + GLSL water)
— a concrete, working pattern the agent can run immediately.

With a ``ScenePlan``: the same env/shader/asset pattern files plus one stub per
planned zone (named group, plan bbox + contents in comments, a seated
placeholder of each listed asset), one stub per ``threejs`` asset (blockout box
at the planned size), GLB preloading for ``blender_glb`` assets, and a
``src/scene.js`` wired to all zones with the plan's cameras.
"""

from __future__ import annotations

import re
from pathlib import Path

from codeverse.contracts.plan import AssetPlan, Plan, ScenePlan, ZonePlan
from codeverse.conventions import to_pascal, to_snake
from codeverse.workspace import Workspace

STARTER_DIR = Path(__file__).resolve().parent / "starter" / "src"
#: pattern files copied verbatim in plan mode (shown as reusable examples)
PATTERN_FILES = ("shaders/sky.js", "shaders/water.js", "assets/pine_tree.js", "assets/windmill.js")


def _write(path: Path, text: str, written: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    written.append(path)


def write_example(ws: Workspace) -> list[Path]:
    """Copy the full example scene into ws.src (overwrites).

    Raises when the starter tree is not there: rglob over a missing directory yields
    nothing, so this used to write ZERO files and report success — which is exactly what
    a non-editable (wheel) install produced before the starter tree was added to
    package-data, and it surfaced much later as an empty scene rather than a bad install.
    """
    if not STARTER_DIR.is_dir():
        raise FileNotFoundError(
            f"scene_threejs starter tree missing: {STARTER_DIR} — this install has no package data "
            f"(a non-editable install needs [tool.setuptools.package-data] to ship languages/**/starter/**/*)")
    written: list[Path] = []
    for src in sorted(STARTER_DIR.rglob("*.js")):
        rel = src.relative_to(STARTER_DIR)
        _write(ws.src / rel, src.read_text(), written)
    if not written:
        raise FileNotFoundError(f"scene_threejs starter tree at {STARTER_DIR} contains no .js files")
    return written


def _env_for_plan(plan: ScenePlan) -> str:
    text = (STARTER_DIR / "env.js").read_text()
    ex = plan.bounds.extents
    span = max(ex[0], ex[2], 40.0)
    ground = int(max(120, span * 2.5))
    fog_near, fog_far = int(span * 0.8), int(max(span * 3.0, 200))
    text = re.sub(r"export const GROUND_SIZE = \d+;", f"export const GROUND_SIZE = {ground};", text)
    text = re.sub(r"const FOG_NEAR = \d+, FOG_FAR = \d+;", f"const FOG_NEAR = {fog_near}, FOG_FAR = {fog_far};", text)
    text = re.sub(r"const SKY_RADIUS = \d+;", f"const SKY_RADIUS = {int(max(600, ground * 3))};", text)
    header = (
        f"// ENV PLAN: {plan.environment.strip()}\n"
        f"// setting: {plan.setting.strip()}  mood: {plan.mood.strip()}\n"
        f"// bounds: centre {tuple(round(c, 1) for c in plan.bounds.center)} extents {tuple(round(e, 1) for e in ex)} m\n"
        "// Rewrite the ground/sky/fog/sun below to match the plan; keep buildEnv/heightAt exports.\n"
    )
    return header + text


def _asset_stub(a: AssetPlan) -> str:
    snake, pascal = to_snake(a.name), to_pascal(a.name)
    w, h, d = (max(0.05, float(v)) for v in a.approx_size_m)
    return f'''// src/assets/{snake}.js — asset "{pascal}": {a.description.strip()}
// approx size {w:g} x {h:g} x {d:g} m (w x h x d); expected instances: ~{a.instances_hint}
// CONTRACT: export function build{pascal}(THREE) → THREE.Group, origin at the base (y = 0), +Y up, +Z front, meters.
//   Build it from several parts with distinct materials (see assets/pine_tree.js, assets/windmill.js);
//   for many instances also export {snake}Parts(THREE) → [{{ geometry, material, castShadow }}].
import * as THREE from 'three';

export function build{pascal}(T = THREE) {{
  const g = new T.Group();
  g.name = '{pascal}';
  // PLACEHOLDER blockout at the planned size — REPLACE with real construction.
  const box = new T.Mesh(new T.BoxGeometry({w:g}, {h:g}, {d:g}), new T.MeshStandardMaterial({{ color: 0x9a9a9a, roughness: 0.8 }}));
  box.position.y = {h / 2:g};
  box.castShadow = box.receiveShadow = true;
  g.add(box);
  return g;
}}
'''


def _zone_stub(z: ZonePlan, assets: dict[str, AssetPlan]) -> str:
    snake, pascal = to_snake(z.name), to_pascal(z.name)
    c, e = z.bbox.center, z.bbox.extents
    imports, places = [], []
    for i, name in enumerate(z.contents):
        a = assets.get(to_snake(name))
        if a is None:
            continue
        asn, asp = to_snake(a.name), to_pascal(a.name)
        ox = (-e[0] / 4 + (i % 3) * e[0] / 4) if len(z.contents) > 1 else 0.0
        oz = (-e[2] / 4 + (i // 3) * e[2] / 4) if len(z.contents) > 3 else 0.0
        x, zz = round(c[0] + ox, 2), round(c[2] + oz, 2)
        if a.kind == "threejs":
            imports.append(f"import {{ build{asp} }} from '../assets/{asn}.js';")
            places.append(f"  place(build{asp}(THREE), {x}, {zz});")
        else:
            places.append(
                f"  if (ctx.assets && ctx.assets.{asn}) place(ctx.assets.{asn}.clone(), {x}, {zz});"
                f"  // GLB built in Blender → public/assets/{asn}.glb (preloaded by scene.js)"
            )
    imp = "\n".join(imports)
    body = "\n".join(places) if places else "  // TODO: build this zone's content here (use ctx.heightAt to seat objects)."
    return f'''// src/zones/{snake}.js — zone "{pascal}": {z.description.strip()}
// PLAN bbox: centre ({c[0]:g}, {c[1]:g}, {c[2]:g}) extents ({e[0]:g} x {e[1]:g} x {e[2]:g}) m. Contents: {", ".join(z.contents) or "-"}
// CONTRACT: export function build(ctx) → THREE.Group named '{pascal}'.  ctx = {{ THREE, scene, renderer, loaders, env, heightAt, assets }}
//   • seat objects on the ground with ctx.heightAt(x, z); keep everything inside the plan bbox
//   • repeats → THREE.InstancedMesh (see the pattern in assets/pine_tree.js: <asset>Parts(THREE))
//   • animation → zone.userData.update = (t, dt) => {{ ... }}  (scene.js fans update() out to every zone)
import * as THREE from 'three';
{imp}

export function build(ctx) {{
  const {{ heightAt }} = ctx;
  const zone = new THREE.Group();
  zone.name = '{pascal}';
  const place = (obj, x, z, ry = 0) => {{ obj.position.set(x, heightAt(x, z), z); obj.rotation.y = ry; zone.add(obj); return obj; }};
{body}
  zone.userData.update = (t, dt) => {{ /* animate zone elements here */ }};
  return zone;
}}
'''


def _scene_for_plan(plan: ScenePlan) -> str:
    zones = [(to_snake(z.name), to_pascal(z.name)) for z in plan.zones]
    glbs = [to_snake(a.name) for a in plan.assets if a.kind == "blender_glb"]
    zone_imports = "\n".join(f"import {{ build as build{p} }} from './zones/{s}.js';" for s, p in zones)
    zone_calls = ", ".join(f"build{p}(ctx)" for _, p in zones)
    cams = ",\n".join(
        f"    {{ name: '{to_snake(c.name)}', position: [{c.position[0]:g}, {c.position[1]:g}, {c.position[2]:g}], "
        f"lookAt: [{c.look_at[0]:g}, {c.look_at[1]:g}, {c.look_at[2]:g}], fov: {c.fov:g} }}"
        + (f"  // {c.purpose}" if c.purpose else "")
        for c in plan.cameras[:6]
    )
    glb_block = ""
    if glbs:
        files = ", ".join(f"{g}: '/assets/{g}.glb'" for g in glbs)
        glb_block = f'''
  // Blender-built assets (public/assets/*.glb) are preloaded once; zones clone ctx.assets.<name>.
  const assetFiles = {{ {files} }};
  const assets = {{}};
  for (const [key, url] of Object.entries(assetFiles)) {{
    try {{ assets[key] = (await loaders.gltf.loadAsync(url)).scene; }}
    catch (e) {{ console.warn(`[assets] missing ${{url}} (${{e && e.message}}) — build it with Blender into public/assets/`); }}
  }}
  ctx.assets = assets;
'''
    async_kw = "async " if glbs else ""
    header = (STARTER_DIR / "scene.js").read_text().split("import * as THREE")[0]
    return f'''{header}// PLAN: {plan.title} — {plan.summary.strip()}
// setting: {plan.setting.strip()}   animation: {"; ".join(plan.animation) or "-"}
import * as THREE from 'three';
import {{ buildEnv, heightAt, SUN_AZIMUTH_DEG }} from './env.js';
{zone_imports}

export {async_kw}function createScene({{ THREE: T = THREE, renderer, loaders }}) {{
  const scene = new THREE.Scene();
  const ctx = {{ THREE, scene, renderer, loaders, heightAt, sunAzimuthDeg: SUN_AZIMUTH_DEG, assets: {{}} }};
  const env = buildEnv(ctx);
  ctx.env = env;
{glb_block}
  const zones = [{zone_calls}];
  for (const z of zones) scene.add(z);

  const cameras = [
{cams}
  ];

  function update(t, dt) {{
    if (env.update) env.update(t, dt);
    for (const z of zones) if (z.userData.update) z.userData.update(t, dt);
  }}
  return {{ scene, cameras, update }};
}}
'''


def write_skeleton(ws: Workspace, plan: Plan | None = None) -> list[Path]:
    """Write starter files into ``ws.src``; returns the written paths."""
    if plan is None or not isinstance(plan, ScenePlan):
        return write_example(ws)
    written: list[Path] = []
    for rel in PATTERN_FILES:
        _write(ws.src / rel, (STARTER_DIR / rel).read_text(), written)
    _write(ws.src / "env.js", _env_for_plan(plan), written)
    assets = {to_snake(a.name): a for a in plan.assets}
    for a in plan.assets:
        if a.kind == "threejs":
            _write(ws.src / "assets" / f"{to_snake(a.name)}.js", _asset_stub(a), written)
    for z in plan.zones:
        _write(ws.src / "zones" / f"{to_snake(z.name)}.js", _zone_stub(z, assets), written)
    _write(ws.src / "scene.js", _scene_for_plan(plan), written)
    (ws.public / "assets").mkdir(parents=True, exist_ok=True)
    return written
