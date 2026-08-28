"""Scene texture pack: the 6-12 tileable albedo textures a ``ScenePlan`` implies,
generated BEFORE zone generation so scene code can load them.

* ``scene_pack_plan(plan, model_id)``: one text-only structured call → entries
  (name, family, subject, tile size, role, PBR factors); ``default_scene_pack_plan``
  is the keyword fallback (no model).
* ``scene_texture_pack(plan, out_dir, image_model, model_id)``: generate + seam-fix
  → ``out_dir/<name>.png`` + ``out_dir/manifest.json``
  ``{name: {file, tile_size_m, family, role, prompt, seam_score}}``.
* ``texture_pack_prompt(manifest)``: the snippet the scene track injects into zone /
  env prompts (URL, tile size, exact three.js loading lines).

Served URL: the workspace root is served at ``/`` (``runtime_js/lib/host_coverage.mjs``),
so ``public/textures/<name>.png`` is reachable as ``/public/textures/<name>.png``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from codeverse.contracts.chat import ChatMessage, ChatRequest
from codeverse.contracts.common import Usage
from codeverse.contracts.plan import ScenePlan
from codeverse.conventions import to_snake
from codeverse.models.schema_utils import parse_json_lenient
from codeverse.prompts import render
from codeverse.texturing.generate import TextureSet, generate_textures
from codeverse.texturing.plan import FAMILY_DEFAULTS, MaterialFamily, compose_image_prompt

log = logging.getLogger(__name__)

PACK_TEMPLATE = "texturing/scene_pack.md"
DEFAULT_URL_PREFIX = "/public/textures"
MANIFEST_NAME = "manifest.json"


class PackEntry(BaseModel):
    name: str
    material_family: MaterialFamily = "other"
    subject: str = ""
    prompt: str = ""
    tile_size_m: float = Field(default=1.0, gt=0.0, le=50.0)
    role: str = "ground"
    roughness: float = Field(default=0.8, ge=0.0, le=1.0)
    metallic: float = Field(default=0.0, ge=0.0, le=1.0)
    file: str = ""
    seam_score: float = 0.0
    error: str = ""


class PackOutput(BaseModel):
    textures: list[PackEntry]
    notes: str = ""


class ScenePack(BaseModel):
    out_dir: str
    entries: dict[str, PackEntry] = Field(default_factory=dict)
    notes: str = ""
    model_id: str = ""
    source: str = "default"
    usage: Usage = Field(default_factory=Usage)
    manifest_path: str = ""

    def manifest(self) -> dict[str, dict[str, Any]]:
        return {n: {"file": e.file, "tile_size_m": e.tile_size_m, "family": e.material_family, "role": e.role,
                    "prompt": e.prompt, "seam_score": e.seam_score, "roughness": e.roughness, "metallic": e.metallic}
                for n, e in self.entries.items() if e.file and not e.error}


# --------------------------------------------------------------------------- planning
_GROUND_RULES: list[tuple[tuple[str, ...], PackEntry]] = [
    (("grass", "lawn", "meadow", "garden", "park"), PackEntry(name="grass_ground", subject="short green lawn grass seen from above, fine blades, slight colour variation", tile_size_m=1.5, role="ground")),
    (("moss", "mossy", "forest", "woodland"), PackEntry(name="moss_ground", subject="dense green moss carpet, soft velvety clumps", tile_size_m=1.0, role="ground")),
    (("gravel", "karesansui", "zen", "raked"), PackEntry(name="fine_gravel", material_family="stone", subject="fine pale grey granite gravel, small even pebbles", tile_size_m=0.8, role="ground")),
    (("sand", "beach", "desert", "dune"), PackEntry(name="sand_ground", subject="fine pale beige sand with soft ripples", tile_size_m=1.0, role="ground")),
    (("soil", "dirt", "earth", "mud", "path", "trail"), PackEntry(name="packed_soil", subject="dark brown packed earth with small pebbles", tile_size_m=1.0, role="path")),
    (("cobble", "pavement", "plaza", "street", "square", "town"), PackEntry(name="cobblestone", material_family="stone", subject="grey granite cobblestones with dark mortar joints", tile_size_m=1.2, role="path")),
    (("stone", "rock", "boulder", "granite", "cliff"), PackEntry(name="rough_granite", material_family="stone", subject="rough grey granite rock with lichen speckles", tile_size_m=1.0, role="prop")),
    (("wood", "plank", "bridge", "deck", "dock", "pier", "timber", "cabin", "fence"), PackEntry(name="weathered_planks", material_family="wood", subject="weathered grey-brown wooden planks, straight grain, narrow gaps", tile_size_m=0.8, role="prop")),
    (("roof", "tile", "shingle", "house", "temple", "hut"), PackEntry(name="clay_roof_tiles", material_family="ceramic", subject="terracotta clay roof tiles in overlapping rows", tile_size_m=1.0, role="roof")),
    (("brick", "wall"), PackEntry(name="red_brick", material_family="stone", subject="red clay bricks with light grey mortar, running bond", tile_size_m=1.0, role="wall")),
    (("bark", "tree", "trunk", "maple", "oak", "pine"), PackEntry(name="tree_bark", material_family="wood", subject="dark grey-brown tree bark with vertical furrows", tile_size_m=0.5, role="prop")),
    (("water", "pond", "lake", "river", "sea", "harbour", "harbor"), PackEntry(name="pond_bed", material_family="stone", subject="dark silty pond bed with scattered small pebbles and algae", tile_size_m=1.0, role="water_bed")),
    (("fabric", "tent", "cloth", "banner", "awning", "canvas"), PackEntry(name="canvas_fabric", material_family="fabric", subject="natural off-white woven canvas", tile_size_m=0.3, role="fabric")),
    (("metal", "steel", "iron", "rust"), PackEntry(name="rusty_steel", material_family="metal", subject="rust-streaked painted steel plate, matte", tile_size_m=0.5, role="prop")),
    (("concrete", "asphalt", "road", "industrial"), PackEntry(name="concrete", material_family="stone", subject="smooth grey concrete with fine pores", tile_size_m=1.0, role="ground")),
    (("bamboo",), PackEntry(name="bamboo_poles", material_family="wood", subject="vertical cured yellow bamboo poles with dark nodes", tile_size_m=0.5, role="prop")),
]


def _plan_text(plan: ScenePlan) -> str:
    bits = [plan.title, plan.summary, plan.setting, plan.mood, plan.environment]
    bits += [f"{z.name} {z.description}" for z in plan.zones]
    bits += [f"{a.name} {a.description}" for a in plan.assets]
    return " ".join(bits)


def _finalize_entry(e: PackEntry) -> PackEntry:
    fam = e.material_family if e.material_family in FAMILY_DEFAULTS else "other"
    d = FAMILY_DEFAULTS[fam]
    name = to_snake(e.name) or f"{fam}_texture"
    subject = e.subject.strip() or name.replace("_", " ")
    return e.model_copy(update={
        "name": name, "material_family": fam, "subject": subject, "prompt": compose_image_prompt(subject, fam),
        "tile_size_m": float(e.tile_size_m) if e.tile_size_m > 0 else d.tile_size_m,
        "roughness": float(e.roughness) if e.roughness is not None else d.roughness,
        "metallic": float(e.metallic) if e.metallic is not None else d.metallic,
    })


def default_scene_pack_plan(plan: ScenePlan, *, n_min: int = 6, n_max: int = 12) -> list[PackEntry]:
    """Keyword fallback: textures whose trigger words appear in the plan; always a
    ground; padded with generic ground/stone/wood up to ``n_min``."""
    words = set(to_snake(_plan_text(plan)).split("_"))
    out: list[PackEntry] = []
    for keys, entry in _GROUND_RULES:
        if any(k in words for k in keys) and len(out) < n_max:
            out.append(entry)
    names = {e.name for e in out}
    for _, entry in _GROUND_RULES:
        if len(out) >= n_min:
            break
        if entry.name not in names:
            out.append(entry)
            names.add(entry.name)
    return [_finalize_entry(e) for e in out[:n_max]]


def scene_pack_plan(
    plan: ScenePlan, model_id: str, *, model: Any | None = None, n_min: int = 6, n_max: int = 12, temperature: float = 0.4,
) -> tuple[list[PackEntry], str, Usage]:
    """Structured text call → entries.  Empty ``model_id`` and no model → default plan."""
    if not model_id and model is None:
        return default_scene_pack_plan(plan, n_min=n_min, n_max=n_max), "default", Usage()
    if model is None:
        from codeverse.models import get_chat_model

        model = get_chat_model(model_id)
    zones = "\n".join(f"- {z.name}: {z.description}" for z in plan.zones)
    assets = "\n".join(f"- {a.name} ({a.kind}): {a.description}" for a in plan.assets) or "- (none)"
    text = render(PACK_TEMPLATE, n_min=n_min, n_max=n_max, title=plan.title, setting=plan.setting, mood=plan.mood,
                  environment=plan.environment, zones=zones, assets=assets)
    req = ChatRequest(messages=[ChatMessage.user(text)], system="You plan texture packs for 3D scenes. JSON only.",
                      response_schema=PackOutput.model_json_schema(), temperature=temperature, thinking="low",
                      max_output_tokens=65_536, max_wait_s=300.0, label="scene_texture_pack")
    resp = model.generate(req)
    payload = resp.parsed if resp.parsed is not None else parse_json_lenient(resp.text)
    try:
        out = PackOutput.model_validate(payload)
    except ValidationError as e:
        raise ValueError(f"scene pack planner returned invalid JSON: {e}") from e
    entries: list[PackEntry] = []
    seen: set[str] = set()
    for e in out.textures:
        fe = _finalize_entry(e)
        if fe.name in seen:
            continue
        seen.add(fe.name)
        entries.append(fe)
    if not entries:
        raise ValueError("scene pack planner returned no textures")
    return entries[:n_max], out.notes, resp.usage


# --------------------------------------------------------------------------- generation
def scene_texture_pack(
    scene_plan: ScenePlan,
    out_dir: Path,
    image_model: Any,
    model_id: str = "",
    *,
    model: Any | None = None,
    size: int = 1024,
    cache_dir: Path | None = None,
    n_min: int = 6,
    n_max: int = 12,
    max_workers: int = 6,
    use_cache: bool = True,
) -> ScenePack:
    """Plan + generate the pack into ``out_dir`` and write ``manifest.json``."""
    out_dir = Path(out_dir)
    entries, notes, usage = scene_pack_plan(scene_plan, model_id, model=model, n_min=n_min, n_max=n_max)
    pack = ScenePack(out_dir=str(out_dir), notes=notes, model_id=model_id, source="vlm" if (model_id or model) else "default",
                     usage=usage)
    ts: TextureSet = generate_textures({e.name: e.prompt for e in entries}, out_dir, image_model, size=size,
                                       cache_dir=cache_dir, max_workers=max_workers, use_cache=use_cache)
    pack.usage = pack.usage + ts.usage
    for e in entries:
        a = ts.textures.get(e.name)
        if a is None:
            continue
        e = e.model_copy(update={"file": Path(a.path).name if a.ok else "", "seam_score": round(a.seam_score, 4), "error": a.error})
        pack.entries[e.name] = e
    pack.manifest_path = str(write_manifest(pack, out_dir))
    return pack


def write_manifest(pack: ScenePack, out_dir: Path) -> Path:
    p = Path(out_dir) / MANIFEST_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pack.manifest(), indent=2))
    return p


def load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    p = Path(path)
    if p.is_dir():
        p = p / MANIFEST_NAME
    return json.loads(p.read_text())


def texture_pack_prompt(manifest: dict[str, dict[str, Any]], *, url_prefix: str = DEFAULT_URL_PREFIX) -> str:
    """Prompt snippet for zone/env generation: what exists and the exact loading idiom."""
    if not manifest:
        return ""
    lines = ["## Available textures (seamless tileable albedo PNGs, generated for this scene)"]
    for name, e in manifest.items():
        lines.append(f"- `{url_prefix}/{e['file']}` — {name} ({e.get('family', 'other')}, {e.get('role', '')}; "
                     f"one tile = {float(e['tile_size_m']):.2f} m; roughness {float(e.get('roughness', 0.8)):.2f})")
    lines.append(
        "Load with the provided loader ONLY (no TextureLoader of your own, no DataTexture):\n"
        "```js\n"
        f"const tex = loaders.texture.load('{url_prefix}/{next(iter(manifest.values()))['file']}');\n"
        "tex.wrapS = tex.wrapT = THREE.RepeatWrapping;\n"
        "tex.colorSpace = THREE.SRGBColorSpace;\n"
        "tex.anisotropy = 8;\n"
        "tex.repeat.set(sizeX / TILE_M, sizeZ / TILE_M);   // TILE_M = the tile size listed above, sizeX/Z = the surface's world size in m\n"
        "const mat = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.8, metalness: 0.0 });\n"
        "```\n"
        "Use `texture.clone()` (+ `needsUpdate = true`) when the same image needs a different repeat. "
        "Keep vertex-colour / procedural materials where no texture fits; never invent texture URLs."
    )
    return "\n".join(lines)
