"""scene_threejs: multi-file three.js scenes (+GLSL, +GLB assets) — lint, probe-build, skeleton, assembler."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.config import get_settings
from codeverse.contracts.artifacts import BuildResult, GateFinding, GateReport, Severity
from codeverse.contracts.common import ENTRY_FILE, Language
from codeverse.contracts.plan import AssetPlan, CameraPlan, Plan, ScenePlan, ZonePlan
from codeverse.conventions import to_pascal, to_snake
from codeverse.languages._docs import RuntimeDocs
from codeverse.languages._js_lint import ImportKind, ImportVerdict, check_imports, node_check_syntax
from codeverse.workspace import Workspace

# ===================================================================== lint
GATE = "lint:scene_threejs"
MAX_LINES = 800

_CREATE_SCENE_RE = re.compile(r"export\s+(?:async\s+)?function\s+createScene\b|export\s+(?:const|let)\s+createScene\b|export\s*\{[^}]*\bcreateScene\b[^}]*\}")
_BUILD_RE = re.compile(r"export\s+(?:async\s+)?function\s+build\b|export\s+(?:const|let)\s+build\b|export\s*\{[^}]*\bbuild\b[^}]*\}")
_BUILD_ANY_RE = re.compile(r"export\s+(?:async\s+)?function\s+build[A-Za-z0-9_]*\b|export\s+(?:const|let)\s+build[A-Za-z0-9_]*\b")

#: (regex, severity, message, hint)
_PATTERNS: list[tuple[re.Pattern[str], Severity, str, str]] = [
    (re.compile(r"\brequestAnimationFrame\s*\("), Severity.ERROR, "requestAnimationFrame: the host drives update(t, dt)", "delete your render loop; animate inside update(t, dt)"),
    (re.compile(r"\bsetAnimationLoop\s*\("), Severity.ERROR, "renderer.setAnimationLoop: the host drives rendering", "remove it; animate inside update(t, dt)"),
    (re.compile(r"new\s+THREE\.WebGLRenderer\b|new\s+WebGLRenderer\b|new\s+THREE\.WebGPURenderer\b"), Severity.ERROR, "creating a renderer: the host owns the WebGLRenderer", "use the `renderer` passed to createScene only for capability checks"),
    (re.compile(r"\brenderer\.render\s*\("), Severity.ERROR, "renderer.render(): the host renders", "remove; return {scene, cameras, update} and let the host render"),
    (re.compile(r"\bfetch\s*\(\s*['\"]https?://|XMLHttpRequest|new\s+WebSocket\b|['\"]https?://[^'\"]+\.(?:js|mjs|glb|gltf|png|jpg|hdr)['\"]"), Severity.ERROR, "network / CDN access", "no network: import 'three' / 'three/addons/*' and load assets from '/assets/<name>.glb'"),
    (re.compile(r"\bdocument\.(?:body|getElementById|querySelector|querySelectorAll|head)\b|\bwindow\.addEventListener\b|\.innerHTML\b|\blocalStorage\b"), Severity.ERROR, "DOM/page access outside the host contract", "the host owns the page; only document.createElement('canvas') for procedural textures is allowed"),
    (re.compile(r"\bTHREE\.Geometry\b|\bFace3\b|\bexamples/js/"), Severity.ERROR, "removed three.js API (Geometry/Face3/examples/js)", "use BufferGeometry and 'three/addons/...' imports"),
    (re.compile(r"\b(?:Box|Plane|Sphere|Cylinder|Cone|Torus|Circle|Ring|Shape|Extrude|Lathe|Tube|Icosahedron|Dodecahedron|Octahedron|Tetrahedron|Polyhedron|Edges|Wireframe)BufferGeometry\b"), Severity.ERROR, "*BufferGeometry aliases were removed (r144)", "drop the 'Buffer' infix: THREE.BoxGeometry"),
    (re.compile(r"\.outputEncoding\b|\bsRGBEncoding\b|\bLinearEncoding\b|\.encoding\s*="), Severity.ERROR, "texture/renderer .encoding was removed (r152)", "use texture.colorSpace = THREE.SRGBColorSpace (colour maps only)"),
    (re.compile(r"\bMath\.random\s*\("), Severity.WARN, "Math.random makes renders non-reproducible", "use a seeded PRNG (e.g. mulberry32) so rounds can be compared"),
    (re.compile(r"\brequire\s*\(|\bprocess\.env\b|\bmodule\.exports\b"), Severity.ERROR, "CommonJS / node globals in browser ESM", "use ESM import/export only"),
    (re.compile(r"\bTHREE\.ImageUtils\b|\bTHREE\.SceneUtils\b|\bMeshFaceMaterial\b|\bMultiMaterial\b"), Severity.ERROR, "removed three.js helper", "use TextureLoader / material arrays"),
]


def _f(sev: Severity, msg: str, *, target: str, hint: str = "", **data: object) -> GateFinding:
    return GateFinding(gate=GATE, severity=sev, target=target, message=msg, fix_hint=hint, data=dict(data))


def _js_files(ws: Workspace) -> list[Path]:
    if not ws.src.is_dir():
        return []
    return sorted(p for p in ws.src.rglob("*.js") if "node_modules" not in p.parts) + sorted(ws.src.rglob("*.mjs"))


def _node_check(path: Path, rel: str) -> GateFinding | None:
    p = node_check_syntax(path, get_settings().binaries.node or "node")
    if p is None:
        return None
    return _f(Severity.ERROR, f"syntax: {p.message}", target=f"{rel}:{p.line}" if p.line else rel,
              hint="fix the syntax error at the quoted line (node --check)", line=p.line, detail=p.stderr_tail)


def _check_imports(rel: str, text: str, ws: Workspace, path: Path) -> list[GateFinding]:
    def make_finding(v: ImportVerdict, spec: str, line: int) -> GateFinding:
        tgt = f"{rel}:{line}"
        if v.kind is ImportKind.ESCAPES:
            return _f(Severity.ERROR, f"import '{spec}' escapes src/", target=tgt, hint="keep all code under src/")
        if v.kind is ImportKind.MISSING:
            return _f(Severity.ERROR, f"import of missing file '{spec}'", target=tgt,
                      hint="create the file or fix the path (relative imports need the .js extension)")
        if v.kind is ImportKind.ABSOLUTE:
            return _f(Severity.ERROR, f"absolute import '{spec}'", target=tgt, hint="use relative imports ('./x.js')")
        return _f(Severity.ERROR, f"import '{spec}' is not allowed (only 'three', 'three/addons/*', relative files)", target=tgt,
                  hint="no CDN / npm packages; write the helper yourself in src/")

    return check_imports(text, path, ws.src, make_finding=make_finding)[0]


def lint(ws: Workspace) -> GateReport:
    """Run all static checks; passed iff no ERROR findings."""
    t0 = time.time()
    findings: list[GateFinding] = []
    files = _js_files(ws)
    scene = ws.src / "scene.js"
    if not scene.is_file():
        findings.append(_f(Severity.ERROR, "src/scene.js is missing", target="src/scene.js",
                           hint="create src/scene.js exporting createScene({THREE, renderer, loaders})"))
    big: list[str] = []
    for path in files:
        rel = path.relative_to(ws.root).as_posix()
        text = path.read_text(errors="replace")
        syntax = _node_check(path, rel)
        if syntax:
            findings.append(syntax)
            continue
        findings.extend(_check_imports(rel, text, ws, path))
        for pat, sev, msg, hint in _PATTERNS:
            m = pat.search(text)
            if m:
                line = text.count("\n", 0, m.start()) + 1
                findings.append(_f(sev, msg, target=f"{rel}:{line}", hint=hint))
        n_lines = text.count("\n") + 1
        if n_lines > MAX_LINES:
            big.append(f"{rel} ({n_lines} lines)")
        if path == scene and not _CREATE_SCENE_RE.search(text):
            findings.append(_f(Severity.ERROR, "src/scene.js does not export createScene", target=rel,
                               hint="export function createScene({ THREE, renderer, loaders }) { ... return { scene, cameras, update } }"))
        if path.parent == ws.src / "zones" and not _BUILD_RE.search(text):
            findings.append(_f(Severity.ERROR, "zone module does not export build(ctx)", target=rel,
                               hint="export function build(ctx) { const g = new THREE.Group(); g.name = 'ZoneName'; ...; return g; }"))
        if path.parent == ws.src / "assets" and not _BUILD_ANY_RE.search(text):
            findings.append(_f(Severity.WARN, "asset module exports no build<Pascal>(THREE) factory", target=rel,
                               hint="export function buildLamp(THREE) { ... return group; }"))
    for b in big:
        findings.append(_f(Severity.WARN, f"large file {b} > {MAX_LINES} lines", target=b.split(" ")[0],
                           hint="split into zones/assets/shaders modules"))
    passed = not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=GATE, passed=passed, findings=findings, duration_ms=int((time.time() - t0) * 1000))


# ===================================================================== skeleton
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


# ===================================================================== assemble
PROBE_REL = "src/_c3v_assemble_probe.js"
_PREFIX = "[3dcv-assemble]"
_SUN_RE = re.compile(r"export\s+const\s+SUN_AZIMUTH_DEG\s*=\s*(-?\d+(?:\.\d+)?)")


class ZoneProbe(BaseModel):
    name: str
    file: str
    ok: bool
    error: str = ""
    bbox: dict[str, Any] | None = None
    meshes: int = 0
    triangles: int = 0


class ZoneProbeReport(BaseModel):
    """``probe_zone_modules`` outcome.  Unpacks as ``probes, census, other = ...``
    (historical 3-tuple); ``camera_specs`` carries the driver-fitted cameras
    (``{azimuth, overview, zones:[{group, position, lookAt, fov}]}``)."""

    probes: dict[str, ZoneProbe] = Field(default_factory=dict)
    census: dict[str, Any] = Field(default_factory=dict)
    other_errors: list[str] = Field(default_factory=list)
    camera_specs: dict[str, Any] = Field(default_factory=dict)

    def __iter__(self):  # type: ignore[override]
        yield self.probes
        yield self.census
        yield self.other_errors


class AssembleResult(BaseModel):
    scene_path: str
    zones_included: list[str] = Field(default_factory=list)
    zones_failed: dict[str, str] = Field(default_factory=dict)
    env_ok: bool = True
    cameras: list[CameraPlan] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    census: dict[str, Any] = Field(default_factory=dict)


def zone_files(ws: Workspace) -> list[Path]:
    d = ws.src / "zones"
    return sorted(p for p in d.glob("*.js") if not p.name.startswith("_")) if d.is_dir() else []


def glb_assets(ws: Workspace) -> list[str]:
    d = ws.public / "assets"
    return sorted(p.stem for p in d.glob("*.glb")) if d.is_dir() else []


def sun_azimuth(ws: Workspace, default: float = 45.0) -> float:
    env = ws.src / "env.js"
    if env.is_file():
        m = _SUN_RE.search(env.read_text(errors="replace"))
        if m:
            return float(m.group(1))
    return default


# --------------------------------------------------------------------------- probing
def _probe_module(zones: list[Path], glbs: list[str]) -> str:
    entries = ",\n".join(f"    ['{to_snake(p.stem)}', './zones/{p.name}']" for p in zones)
    glb_lines = ", ".join(f"{g}: '/assets/{g}.glb'" for g in glbs)
    return f"""// generated by codeverse assembler — probes env + zone modules independently; deleted after use
import * as THREE from 'three';
const ZONES = [
{entries}
];
export async function createScene({{ renderer, loaders }}) {{
  const scene = new THREE.Scene();
  const ctx = {{ THREE, scene, renderer, loaders, heightAt: () => 0, assets: {{}}, env: {{}} }};
  const assetFiles = {{ {glb_lines} }};
  for (const [k, url] of Object.entries(assetFiles)) {{
    try {{ ctx.assets[k] = (await loaders.gltf.loadAsync(url)).scene; }}
    catch (e) {{ console.error('{_PREFIX} asset ' + k + ' failed: ' + ((e && e.message) || e)); }}
  }}
  let env = null;
  try {{
    const m = await import('./env.js');
    if (typeof m.heightAt === 'function') ctx.heightAt = m.heightAt;
    if (typeof m.buildEnv === 'function') env = await m.buildEnv(ctx);
    ctx.env = env || {{}};
  }} catch (e) {{ console.error('{_PREFIX} env failed: ' + ((e && e.stack) || e)); }}
  const wraps = [];
  for (const [name, rel] of ZONES) {{
    try {{
      const m = await import(rel);
      if (typeof m.build !== 'function') throw new Error('module has no export build(ctx)');
      const g = await m.build(ctx);
      if (!g || !g.isObject3D) throw new Error('build(ctx) did not return a THREE.Object3D');
      const wrap = new THREE.Group();
      wrap.name = '__zone__' + name;
      wrap.add(g);
      scene.add(wrap);
      wraps.push(wrap);
    }} catch (e) {{ console.error('{_PREFIX} zone ' + name + ' failed: ' + ((e && e.stack) || e)); }}
  }}
  return {{
    scene,
    cameras: [{{ name: 'probe', position: [30, 20, 30], lookAt: [0, 0, 0], fov: 50 }}],
    update(t, dt) {{
      try {{ if (env && env.update) env.update(t, dt); }} catch (e) {{ console.error('{_PREFIX} env update failed: ' + e); }}
      for (const w of wraps) {{
        const z = w.children[0];
        try {{ if (z && z.userData.update) z.userData.update(t, dt); }} catch (e) {{ console.error('{_PREFIX} zone ' + w.name.slice(8) + ' update failed: ' + e); }}
      }}
    }},
  }};
}}
"""


def probe_zone_modules(ws: Workspace, *, timeout_s: float = 90.0, sun_azimuth_deg: float | None = None) -> ZoneProbeReport:
    """Probe env + every zone module independently in the browser host.

    Returns a :class:`ZoneProbeReport` (unpacks as the historical 3-tuple of
    zone probes by snake name, census, other console errors).  When
    ``sun_azimuth_deg`` is given the driver also fits overview + per-group
    cameras from the measured bboxes (``camera_specs``).
    """
    from codeverse.spatial.render_scene import run_scene_script

    zones = zone_files(ws)
    probe_path = ws.root / PROBE_REL
    probe_path.write_text(_probe_module(zones, glb_assets(ws)))
    args = ["--ws", str(ws.root), "--scene", PROBE_REL, "--timeout-ms", str(int(timeout_s * 1000))]
    if sun_azimuth_deg is not None:
        args += ["--sun-azimuth", str(sun_azimuth_deg)]
    try:
        res = run_scene_script("probe_scene.mjs", args, timeout_s=timeout_s + 20)
    finally:
        probe_path.unlink(missing_ok=True)
    s = res.summary
    boot = s.get("boot") or {}
    errors: list[str] = list(s.get("console_errors", []))
    if not boot.get("ok"):
        errors.insert(0, f"probe scene did not boot [{boot.get('stage')}]: {boot.get('error', '')}")
    failed: dict[str, str] = {}
    other: list[str] = []
    for e in errors:
        m = re.match(rf"(?:console: )?{re.escape(_PREFIX)} zone (\S+) failed: (.*)", e, re.S)
        if m:
            failed[m.group(1)] = m.group(2).strip()[:600]
        else:
            other.append(e)
    census = s.get("census") or {}
    groups = {g["name"]: g for g in census.get("groups", [])}
    probes: dict[str, ZoneProbe] = {}
    for p in zones:
        name = to_snake(p.stem)
        g = groups.get(f"__zone__{name}")
        if name in failed or g is None:
            probes[name] = ZoneProbe(name=name, file=f"src/zones/{p.name}", ok=False,
                                     error=failed.get(name, "zone not present after probe (build returned nothing visible?)"))
        else:
            probes[name] = ZoneProbe(name=name, file=f"src/zones/{p.name}", ok=True, bbox=g.get("bbox"),
                                     meshes=g.get("meshes", 0), triangles=g.get("triangles", 0))
    return ZoneProbeReport(probes=probes, census=census, other_errors=other, camera_specs=s.get("fitted_cameras") or {})


# --------------------------------------------------------------------------- cameras
_FALLBACK_CAMERA = CameraPlan(name="overview", position=(30.0, 18.0, 30.0), look_at=(0.0, 1.0, 0.0), fov=50.0,
                              purpose="fallback: no measurable zones")


def _plan_from_spec(spec: dict[str, Any], *, name: str, purpose: str) -> CameraPlan:
    return CameraPlan(name=name, position=tuple(spec["position"]), look_at=tuple(spec["lookAt"]),
                      fov=float(spec.get("fov", 50.0)), purpose=purpose)


def cameras_from_specs(specs: dict[str, Any], probes: dict[str, ZoneProbe], *, max_cameras: int = 6) -> list[CameraPlan]:
    """Convert driver-fitted camera specs (``probe_scene.mjs --sun-azimuth``;
    math owned by ``runtime_js/lib/orbit.mjs``) into CameraPlans: overview
    first, then per-zone cameras, largest footprints first.  Falls back to a
    fixed overview when nothing was measurable."""
    healthy = [p for p in probes.values() if p.ok and p.bbox]
    overview = (specs or {}).get("overview")
    if not healthy or not overview:
        return [_FALLBACK_CAMERA.model_copy()]
    cams = [_plan_from_spec(overview, name="overview", purpose="overview fitted to measured content bounds, sun side")]
    by_zone = {str(z.get("group", "")).removeprefix("__zone__"): z for z in (specs or {}).get("zones", [])}
    by_area = sorted(healthy, key=lambda p: -(p.bbox["size"][0] * p.bbox["size"][2]))
    for p in by_area[: max_cameras - 1]:
        spec = by_zone.get(p.name)
        if spec:
            cams.append(_plan_from_spec(spec, name=f"{to_snake(p.name)}_view", purpose=f"zone {to_pascal(p.name)} from the sun side"))
    return cams


# --------------------------------------------------------------------------- scene.js
def _cam_literal(c: CameraPlan) -> str:
    p, la = c.position, c.look_at
    return (f"    {{ name: '{to_snake(c.name)}', position: [{p[0]:g}, {p[1]:g}, {p[2]:g}], "
            f"lookAt: [{la[0]:g}, {la[1]:g}, {la[2]:g}], fov: {c.fov:g} }},")


def render_scene_js(zones: list[str], cameras: list[CameraPlan], glbs: list[str], *, env_ok: bool = True) -> str:
    """Deterministic src/scene.js source for the given healthy zone snake names."""
    imports = "\n".join(f"import {{ build as build{to_pascal(z)} }} from './zones/{z}.js';" for z in zones)
    calls = "\n".join(f"  await addZone(build{to_pascal(z)}, '{to_pascal(z)}');" for z in zones)
    env_import = "import { buildEnv, heightAt } from './env.js';" if env_ok else ""
    # buildEnv/build may be async (lint + the assembler probe both accept it): await consistently
    env_build = "  const env = (await buildEnv(ctx)) || {};\n  ctx.env = env;" if env_ok else "  const env = {};"
    height = "heightAt" if env_ok else "() => 0"
    glb_lines = ", ".join(f"{g}: '/assets/{g}.glb'" for g in glbs)
    cams = "\n".join(_cam_literal(c) for c in cameras)
    return f"""// src/scene.js — ASSEMBLED BY THE HARNESS (codeverse assembler). Edit zones/env instead; re-assembly overwrites this file.
import * as THREE from 'three';
{env_import}
{imports}

export async function createScene({{ renderer, loaders }}) {{
  const scene = new THREE.Scene();
  const ctx = {{ THREE, scene, renderer, loaders, heightAt: {height}, assets: {{}} }};
  const assetFiles = {{ {glb_lines} }};
  for (const [key, url] of Object.entries(assetFiles)) {{
    try {{ ctx.assets[key] = (await loaders.gltf.loadAsync(url)).scene; }}
    catch (e) {{ console.error(`[assets] failed to load ${{url}}: ${{e && e.message}}`); }}
  }}
{env_build}

  const zones = [];
  const addZone = async (build, name) => {{
    const g = await build(ctx);
    if (!g || !g.isObject3D) throw new Error(`zone ${{name}}: build(ctx) must return a THREE.Group`);
    if (!g.name) g.name = name;
    scene.add(g);
    zones.push(g);
  }};
{calls}

  const cameras = [
{cams}
  ];

  function update(t, dt) {{
    if (env.update) env.update(t, dt);
    for (const z of zones) if (z.userData.update) z.userData.update(t, dt);
  }}
  return {{ scene, cameras, update }};
}}
"""


def assemble(ws: Workspace, plan: ScenePlan | None = None, *, cameras: str = "derive", probe: bool = True,
             timeout_s: float = 90.0) -> AssembleResult:
    """Probe zones, derive cameras, write ``src/scene.js``.  ``cameras='plan'`` keeps the plan's."""
    warnings: list[str] = []
    zones = zone_files(ws)
    if not zones:
        raise FileNotFoundError(f"no zone modules under {ws.src / 'zones'}")
    env_ok = (ws.src / "env.js").is_file()
    if not env_ok:
        warnings.append("src/env.js missing: scene assembled without env (flat ground at y=0, no lights unless zones add them)")
    probes: dict[str, ZoneProbe] = {}
    census: dict[str, Any] = {}
    camera_specs: dict[str, Any] = {}
    if probe:
        report = probe_zone_modules(ws, timeout_s=timeout_s, sun_azimuth_deg=sun_azimuth(ws))
        probes, census, camera_specs = report.probes, report.census, report.camera_specs
        for o in report.other_errors:
            if _PREFIX in o and "env failed" in o:
                env_ok = False
                warnings.append(f"env.js failed in probe; assembled without it: {o[:300]}")
            else:
                warnings.append(o[:300])
    else:
        probes = {to_snake(p.stem): ZoneProbe(name=to_snake(p.stem), file=f"src/zones/{p.name}", ok=True) for p in zones}
    healthy = [n for n, p in probes.items() if p.ok]
    failed = {n: p.error for n, p in probes.items() if not p.ok}
    if not healthy:
        warnings.append("no zone module survived probing; scene.js assembled with zero zones")
    if cameras == "plan" and plan is not None and plan.cameras:
        cams = list(plan.cameras[:6])
    else:
        cams = cameras_from_specs(camera_specs, probes)
    text = render_scene_js(healthy, cams, glb_assets(ws), env_ok=env_ok)
    out = ws.src / "scene.js"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    (ws.artifacts).mkdir(parents=True, exist_ok=True)
    result = AssembleResult(scene_path=str(out), zones_included=healthy, zones_failed=failed, env_ok=env_ok,
                            cameras=cams, warnings=warnings, census=census)
    (ws.artifacts / "assemble.json").write_text(json.dumps(result.model_dump(mode="json"), indent=1))
    return result


# ===================================================================== runtime


class SceneThreeJsRuntime(RuntimeDocs):
    language = Language.SCENE_THREEJS
    entry_globs: tuple[str, ...] = (ENTRY_FILE[Language.SCENE_THREEJS], "src/zones/*.js", "src/assets/*.js", "src/env.js", "src/shaders/*.js")

    def skeleton(self, ws: Workspace, plan: Plan | None) -> list[Path]:
        return write_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        return lint(ws)  # the module-level gates function (class ns is not in method scope)

    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        """Probe + shader preflight (one browser boot); ok iff the module loads
        and no shader errors.  External contract unchanged: the same two
        GateReports land under ``artifacts/gates/``."""
        from codeverse.config import get_settings

        t0 = time.time()
        tmo = min(float(timeout_s or get_settings().limits.build_timeout_s), 120.0)
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        # a probe/driver crash must not leave the previous round's census or the root
        # driver outputs (scene_probe.json / shader_preflight.json) looking current;
        # census.json is only rewritten `if census:` below, so it MUST be wiped here
        ws.stage_artifacts("census.json", "scene_probe.json", "shader_preflight.json", "build.json").invalidate()
        probe, shaders, census = _probe_and_preflight(ws, timeout_s=tmo)
        if census:
            (ws.artifacts / "census.json").write_text(json.dumps(census, indent=1))
        gates_dir = ws.artifacts / "gates"
        gates_dir.mkdir(exist_ok=True)
        (gates_dir / "scene_probe.json").write_text(probe.model_dump_json(indent=1))
        (gates_dir / "shader_preflight.json").write_text(shaders.model_dump_json(indent=1))
        errors = sorted(probe.errors + shaders.errors, key=lambda f: 0 if _target_line(f.target) else 1)
        first = errors[0] if errors else None
        res = BuildResult(
            ok=probe.passed and shaders.passed,
            language=self.language.value,
            glb_path=None,
            extra_paths={
                "census": str(ws.artifacts / "census.json"),
                "scene_probe": str(gates_dir / "scene_probe.json"),
                "shader_preflight": str(gates_dir / "shader_preflight.json"),
            },
            stdout_tail=_summary_text(probe, shaders),
            error_type=(first.data.get("kind") or first.gate) if first else "",
            error_message=first.message if first else "",
            error_file=_target_file(first.target) if first else "",
            error_line=_target_line(first.target) if first else None,
            duration_ms=int((time.time() - t0) * 1000),
            census=census,
        )
        (ws.artifacts / "build.json").write_text(json.dumps(res.model_dump(mode="json"), indent=1))
        return res


def _probe_and_preflight(ws: Workspace, *, timeout_s: float) -> tuple[GateReport, GateReport, dict]:
    """One ``probe_scene.mjs --compile`` run → (scene_probe, shader_preflight, census).

    The driver boots the scene once and runs both stages on the same page;
    when the scene never boots the preflight is skipped, matching the old
    two-call behaviour (a failed-empty shader gate).
    """
    from codeverse.contracts.artifacts import GateFinding
    from codeverse.spatial.probes import PROBE_GATE, SHADER_GATE, probe_report, shader_report
    from codeverse.spatial.render_scene import SceneRenderError, run_scene_script

    t0 = time.time()
    args = [
        "--ws", str(ws.root), "--compile",
        "--out", str(ws.artifacts / "scene_probe.json"),
        "--shaders-out", str(ws.artifacts / "shader_preflight.json"),
        "--timeout-ms", str(int(timeout_s * 1000)),
    ]
    try:
        res = run_scene_script("probe_scene.mjs", args, timeout_s=timeout_s + 20)
    except SceneRenderError as e:
        dur = int((time.time() - t0) * 1000)
        finding = GateFinding(gate=PROBE_GATE, severity=Severity.ERROR, target="src/scene.js",
                              message=f"scene probe could not run: {e}"[:1500],
                              fix_hint="this is a harness/driver failure, not your code; retry or report",
                              data={"harness_failure": True})
        return (GateReport(gate=PROBE_GATE, passed=False, findings=[finding], duration_ms=dur),
                GateReport(gate=SHADER_GATE, passed=False, findings=[]), {})
    dur = int((time.time() - t0) * 1000)
    probe, census = probe_report(res.summary, duration_ms=dur)
    rep = res.summary.get("shader_report") or {}
    if not rep or rep.get("skipped"):
        shaders = GateReport(gate=SHADER_GATE, passed=False, findings=[])
    else:
        shaders = shader_report(rep, duration_ms=int(rep.get("duration_ms") or 0))
    return probe, shaders, census


def _summary_text(probe: GateReport, shaders: GateReport) -> str:
    lines = [f"scene_probe: {'ok' if probe.passed else 'FAILED'} ({len(probe.errors)} errors)"]
    lines += [f"  - {f.target or ''}: {f.message}"[:400] for f in probe.findings if f.severity != Severity.INFO][:12]
    lines.append(f"shader_preflight: {'ok' if shaders.passed else 'FAILED'} ({len(shaders.errors)} errors)")
    lines += [f"  - {f.target or ''}: {f.message}"[:400] for f in shaders.findings if f.severity != Severity.INFO][:12]
    return "\n".join(lines)


def _target_file(target: str | None) -> str:
    if not target:
        return ""
    head, sep, tail = target.rpartition(":")
    return head if sep and tail.isdigit() else target


def _target_line(target: str | None) -> int | None:
    if not target:
        return None
    _, sep, tail = target.rpartition(":")
    return int(tail) if sep and tail.isdigit() else None
