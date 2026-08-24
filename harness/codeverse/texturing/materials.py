"""THE material-family table: real PBR numbers for the finishes objects are made of.

Why this file exists.  The judge's weakest object criterion after ``geometry_detail``
is ``materials`` (mean 0.701 over 110 recorded verdicts), and its complaints are
almost always one of two sentences:

* *"the chrome parts lack metallic reflections and appear as matte grey"*
  (`mus_easy_drum` r0) / *"the drill bit and chuck lack metallic material
  properties, appearing as flat grey plastic"* (`tool_med_hand_drill` r1);
* *"materials are flat colors without any character such as wood grain"*
  (`bld_easy_shed` r0) / *"the blue material lacks roughness variation or bump
  mapping, making it look like smooth plastic rather than cast iron"*
  (`tool_hard_bench_vise` r1).

The numbers below are the ones a materials artist would type — measured
dielectric IORs, the metallic/roughness pairs the glTF and Disney/Substance
guides publish, and the clear-coat that separates lacquered wood from raw
timber.  They are used three ways:

1. as **cookbook text** for the agent (:func:`cookbook_block`) — concrete beats
   abstract (design law 2), so the agent gets a copyable table, not an adjective;
2. as the target of the **post-build normaliser**
   (:mod:`codeverse.texturing.normalise`), which fixes a material whose numbers
   are an untouched framework default or are impossible for the finish its own
   name claims;
3. as the PBR factors the **texture pass** writes next to a generated albedo
   (:mod:`codeverse.texturing.apply`).

Everything here is data + pure functions; nothing imports a model or touches a file.
"""

from __future__ import annotations

from typing import NamedTuple

from pydantic import BaseModel, Field

#: glTF/Blender/three framework defaults.  A material sitting exactly on one of
#: these pairs was never authored — see ``normalise.is_untouched``.
FRAMEWORK_DEFAULTS: tuple[tuple[float, float], ...] = (
    (1.0, 1.0),   # glTF 2.0 material default (metallic 1, roughness 1) — renders as dark mud
    (0.0, 1.0),   # three.js MeshStandardMaterial default
    (0.0, 0.5),   # Blender Principled BSDF default
)


class Pbr(BaseModel):
    """One material family's physically-plausible factors.

    ``ior``/``clearcoat``/``sheen``/``transmission`` have no home in core glTF
    ``pbrMetallicRoughness`` (they are KHR extensions / Blender + three
    ``MeshPhysicalMaterial`` inputs) — they are carried here because the
    cookbook and the Blender and three.js authoring paths do use them.
    """

    family: str
    metallic: float = Field(ge=0.0, le=1.0)
    roughness: float = Field(ge=0.0, le=1.0)
    ior: float = 1.5
    clearcoat: float = 0.0
    clearcoat_roughness: float = 0.1
    sheen: float = 0.0
    transmission: float = 0.0
    #: linear-ish sRGB base colour a real sample of this finish sits near (a hint,
    #: never forced onto an authored colour)
    base_hint: tuple[float, float, float] = (0.5, 0.5, 0.5)
    #: how far the roughness may plausibly wander before the value is "wrong for
    #: this family" (the normaliser's confidence band)
    roughness_band: tuple[float, float] = (0.0, 1.0)
    metallic_band: tuple[float, float] = (0.0, 1.0)
    note: str = ""


def _m(
    family: str, metallic: float, roughness: float, *, ior: float = 1.5, clearcoat: float = 0.0,
    ccr: float = 0.1, sheen: float = 0.0, transmission: float = 0.0,
    base: tuple[float, float, float] = (0.5, 0.5, 0.5),
    rband: tuple[float, float] = (0.0, 1.0), mband: tuple[float, float] = (0.0, 1.0), note: str = "",
) -> Pbr:
    return Pbr(family=family, metallic=metallic, roughness=roughness, ior=ior, clearcoat=clearcoat,
               clearcoat_roughness=ccr, sheen=sheen, transmission=transmission, base_hint=base,
               roughness_band=rband, metallic_band=mband, note=note)


#: family -> factors.  Ordered as the cookbook prints them (metals, woods, polymers, rest).
MATERIALS: dict[str, Pbr] = {
    "painted_metal": _m("painted_metal", 0.0, 0.35, clearcoat=0.35, ccr=0.10, base=(0.30, 0.32, 0.36),
                        rband=(0.08, 0.7), mband=(0.0, 0.35),
                        note="appliance / machine enamel: a DIELECTRIC coat over steel, metallic 0 + clearcoat"),
    "brushed_metal": _m("brushed_metal", 1.0, 0.32, base=(0.62, 0.63, 0.65), rband=(0.15, 0.6), mband=(0.6, 1.0),
                        note="stainless / brushed aluminium; grain is anisotropic — fake it with a streaked roughness map"),
    "chrome": _m("chrome", 1.0, 0.06, base=(0.83, 0.85, 0.88), rband=(0.0, 0.3), mband=(0.7, 1.0),
                 note="mirror plating: needs an environment with SHAPE to reflect or it reads as flat grey"),
    "machined_steel": _m("machined_steel", 1.0, 0.22, base=(0.56, 0.57, 0.60), rband=(0.05, 0.5), mband=(0.6, 1.0),
                         note="tool steel, bright bar, drill bits"),
    "cast_iron": _m("cast_iron", 1.0, 0.62, base=(0.11, 0.11, 0.12), rband=(0.3, 0.95), mband=(0.5, 1.0),
                    note="sand-cast surface: dark, rough, still metallic — pebbly roughness map sells it"),
    "brass": _m("brass", 1.0, 0.25, base=(0.85, 0.65, 0.28), rband=(0.05, 0.6), mband=(0.6, 1.0),
                note="lacquered brass hardware"),
    "copper_patina": _m("copper_patina", 0.20, 0.72, base=(0.36, 0.56, 0.50), rband=(0.4, 1.0), mband=(0.0, 0.6),
                        note="verdigris is an oxide CRUST: mostly dielectric, only the worn edges stay metallic"),
    "hardwood": _m("hardwood", 0.0, 0.45, clearcoat=0.25, ccr=0.15, base=(0.34, 0.20, 0.10),
                   rband=(0.15, 0.85), mband=(0.0, 0.2),
                   note="oak / walnut / teak with a satin varnish; grain must modulate roughness, not just colour"),
    "softwood": _m("softwood", 0.0, 0.72, base=(0.55, 0.40, 0.24), rband=(0.4, 1.0), mband=(0.0, 0.2),
                   note="raw pine / construction timber: no coat, high roughness"),
    "glossy_plastic": _m("glossy_plastic", 0.0, 0.22, ior=1.46, clearcoat=0.20, base=(0.45, 0.45, 0.48),
                         rband=(0.02, 0.5), mband=(0.0, 0.25), note="injection-moulded ABS/PC housing"),
    "rough_plastic": _m("rough_plastic", 0.0, 0.62, ior=1.46, base=(0.40, 0.40, 0.42),
                        rband=(0.25, 1.0), mband=(0.0, 0.25), note="textured / bead-blasted tool housing, nylon"),
    "rubber": _m("rubber", 0.0, 0.88, ior=1.52, base=(0.06, 0.06, 0.06), rband=(0.55, 1.0), mband=(0.0, 0.2),
                 note="grips, tyres, feet: nearly no specular lobe"),
    "fabric": _m("fabric", 0.0, 0.92, sheen=0.30, base=(0.48, 0.44, 0.40), rband=(0.6, 1.0), mband=(0.0, 0.15),
                 note="woven upholstery: sheen at grazing angles is what says 'cloth'"),
    "leather": _m("leather", 0.0, 0.58, ior=1.45, clearcoat=0.10, base=(0.24, 0.13, 0.08),
                  rband=(0.25, 0.9), mband=(0.0, 0.15), note="pores + a faint coat; never mirror-smooth"),
    "glass": _m("glass", 0.0, 0.03, ior=1.52, transmission=1.0, base=(0.94, 0.96, 0.96),
                rband=(0.0, 0.25), mband=(0.0, 0.3), note="transmissive; roughness > 0.15 turns it to frosted"),
    "ceramic": _m("ceramic", 0.0, 0.12, ior=1.60, clearcoat=0.50, ccr=0.03, base=(0.85, 0.84, 0.80),
                  rband=(0.0, 0.60), mband=(0.0, 0.2),
                  note="glaze is a thick clear coat over a matte body; unglazed terracotta goes to 0.6"),
    "concrete": _m("concrete", 0.0, 0.90, base=(0.52, 0.51, 0.49), rband=(0.5, 1.0), mband=(0.0, 0.2),
                   note="also render / plaster / unglazed terracotta"),
    "stone": _m("stone", 0.0, 0.42, base=(0.45, 0.44, 0.42), rband=(0.05, 1.0), mband=(0.0, 0.2),
                note="polished marble 0.20, granite 0.45, slate 0.70"),
    "paper": _m("paper", 0.0, 0.85, base=(0.82, 0.80, 0.76), rband=(0.5, 1.0), mband=(0.0, 0.15),
                note="card, book pages, lampshade paper"),
    "painted_wood": _m("painted_wood", 0.0, 0.42, clearcoat=0.12, ccr=0.20, base=(0.72, 0.71, 0.68),
                       rband=(0.15, 0.85), mband=(0.0, 0.25),
                       note="paint on timber: flatter than car enamel, and the grain still shows through"),
}

#: substrate keywords — WHAT the part is made of.  Checked before finishes, because
#: "varnished beech" is wood with a coat, not a coating on nothing.
SUBSTRATE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("wood_hard", ("oak", "walnut", "teak", "maple", "beech", "birch", "mahogany", "cherry", "ash",
                   "hardwood", "veneer", "plywood", "bamboo", "rosewood")),
    ("wood_soft", ("pine", "spruce", "fir", "cedar", "timber", "softwood", "lumber", "plank", "batten")),
    ("glass", ("glass", "acrylic", "perspex", "crystal", "lens", "glazing", "pane", "windscreen")),
    ("ceramic", ("ceramic", "porcelain", "china", "earthenware", "stoneware")),
    ("fabric", ("fabric", "cloth", "textile", "linen", "cotton", "canvas", "wool", "felt", "velvet",
                "upholstery", "upholstered", "boucle", "webbing", "denim", "burlap")),
    ("leather", ("leather", "suede", "hide", "saddle")),
    ("rubber", ("rubber", "silicone", "neoprene", "tyre", "tire", "grip", "gasket", "grommet")),
    ("copper", ("copper", "verdigris")),
    ("brass", ("brass", "bronze", "gilt")),
    ("cast_iron", ("castiron", "cast", "wrought", "iron")),
    ("light_metal", ("aluminium", "aluminum", "stainless", "nickel", "pewter", "zinc", "titanium",
                     "galvanised", "galvanized")),
    ("steel", ("steel", "machined", "hardened", "gunmetal", "metal", "metallic", "alloy")),
    ("concrete", ("concrete", "cement", "plaster", "render", "stucco", "terracotta", "brick", "mortar")),
    ("stone", ("stone", "marble", "granite", "slate", "terrazzo", "rock", "pebble", "gravel")),
    ("paper", ("paper", "card", "cardboard", "parchment", "lampshade")),
    ("plastic", ("plastic", "abs", "polycarbonate", "polypropylene", "nylon", "resin", "vinyl", "pvc",
                 "melamine", "bakelite", "composite", "acrylonitrile", "laminate")),
    ("wood", ("wood", "wooden", "woodgrain")),
)

#: finish keywords — HOW the substrate is surfaced.
FINISH_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("plated", ("chrome", "chromed", "plated", "mirrored")),
    ("brushed", ("brushed", "satin", "anodised", "anodized", "sandblasted", "beadblasted")),
    ("painted", ("painted", "paint", "enamel", "enamelled", "enameled", "lacquer", "lacquered",
                 "powdercoated", "powdercoat", "coated", "sprayed", "primed")),
    ("varnished", ("varnished", "varnish", "oiled", "waxed", "shellac", "stained", "glazed")),
    ("patina", ("patina", "verdigris", "oxidised", "oxidized", "weathered", "rusted", "rusty", "aged",
                "tarnished", "corroded")),
    ("matte", ("matte", "matt", "textured", "rough", "gritty")),
    ("gloss", ("gloss", "glossy", "shiny", "high-gloss", "highgloss")),
)

#: (substrate, finish) -> family.  ``None`` finish = bare; a substrate row missing a
#: finish falls back to its ``None`` entry.
_RESOLVE: dict[tuple[str, str | None], str] = {
    ("wood_hard", None): "hardwood", ("wood_hard", "painted"): "painted_wood",
    ("wood_soft", None): "softwood", ("wood_soft", "painted"): "painted_wood",
    ("wood_soft", "varnished"): "hardwood",
    ("wood", None): "hardwood", ("wood", "painted"): "painted_wood", ("wood", "matte"): "softwood",
    ("cast_iron", None): "cast_iron", ("cast_iron", "painted"): "painted_metal",
    ("cast_iron", "plated"): "chrome", ("cast_iron", "brushed"): "brushed_metal",
    ("steel", None): "machined_steel", ("steel", "plated"): "chrome", ("steel", "brushed"): "brushed_metal",
    ("steel", "painted"): "painted_metal", ("steel", "patina"): "cast_iron", ("steel", "matte"): "cast_iron",
    ("brass", None): "brass", ("brass", "painted"): "painted_metal",
    ("copper", None): "brass", ("copper", "patina"): "copper_patina",
    ("light_metal", None): "brushed_metal", ("light_metal", "plated"): "chrome",
    ("light_metal", "painted"): "painted_metal", ("light_metal", "patina"): "copper_patina",
    ("glass", None): "glass",
    ("ceramic", None): "ceramic",
    ("rubber", None): "rubber",
    ("fabric", None): "fabric",
    ("leather", None): "leather",
    ("concrete", None): "concrete", ("concrete", "painted"): "painted_wood",
    ("stone", None): "stone",
    ("paper", None): "paper",
    ("plastic", None): "rough_plastic", ("plastic", "gloss"): "glossy_plastic",
    ("plastic", "plated"): "glossy_plastic", ("plastic", "matte"): "rough_plastic",
}

#: a finish with no substrate named at all
_FINISH_ONLY: dict[str, str] = {
    "plated": "chrome", "brushed": "brushed_metal", "painted": "painted_metal",
    "varnished": "hardwood", "patina": "copper_patina", "matte": "rough_plastic",
    "gloss": "glossy_plastic",
}

#: the coarse families :mod:`codeverse.texturing.plan` speaks -> a representative
#: fine family here, so the texture pass and the normaliser agree on the numbers.
COARSE_TO_FINE: dict[str, str] = {
    "wood": "hardwood", "metal": "machined_steel", "fabric": "fabric", "stone": "stone",
    "plastic": "rough_plastic", "leather": "leather", "glass": "glass", "ceramic": "ceramic",
    "painted": "painted_metal", "rubber": "rubber", "other": "rough_plastic",
}


class FamilyMatch(NamedTuple):
    family: str
    keyword: str


def _tokens(text: str) -> set[str]:
    """Whole words of ``text``, splitting snake_case, kebab-case AND CamelCase."""
    s = str(text or "")
    parts: list[str] = []
    cur: list[str] = []
    prev_lower = False
    for ch in s:
        if ch.isalnum():
            if ch.isupper() and prev_lower and cur:
                parts.append("".join(cur))
                cur = []
            cur.append(ch.lower())
            prev_lower = ch.islower() or ch.isdigit()
        else:
            if cur:
                parts.append("".join(cur))
                cur = []
            prev_lower = False
    if cur:
        parts.append("".join(cur))
    joined = "".join(parts)
    return {*parts, joined} if joined else set(parts)


#: word ENDINGS that name a substrate on their own ("basswood", "sandstone", "plexiglass")
SUBSTRATE_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("wood", "wood_hard"), ("stone", "stone"), ("glass", "glass"), ("leather", "leather"),
    ("board", "wood"), ("brick", "concrete"),
)


def _first_hit(table: tuple[tuple[str, tuple[str, ...]], ...], toks: set[str]) -> tuple[str, str] | None:
    for key, keywords in table:
        for kw in keywords:
            if kw in toks:
                return key, kw
    return None


def _substrate(toks: set[str]) -> tuple[str, str] | None:
    hit = _first_hit(SUBSTRATE_KEYWORDS, toks)
    if hit is not None:
        return hit
    for tok in sorted(toks):
        for suffix, key in SUBSTRATE_SUFFIXES:
            if len(tok) > len(suffix) and tok.endswith(suffix):
                return key, tok
    return None


def family_for(*texts: str) -> FamilyMatch | None:
    """Resolve a material family from free text.

    Two orderings do the work.  Across arguments: the FIRST text that resolves wins,
    so callers pass the most specific evidence first (material name, then node name,
    then the plan's prose) — a node called ``SteamWand`` whose material is
    ``SteamWandChrome`` must not become silicone because the plan's sentence
    mentions a silicone tip.  Within one text: substrate before finish, so
    *"varnished beech"* is ``hardwood`` (a coat on wood), *"satin white lacquer"* is
    ``painted_metal`` and *"chrome-plated steel"* is ``chrome``.
    ``None`` when nothing matches."""
    for text in texts:          # material name beats node name beats the plan's prose
        hit = _resolve_one(_tokens(text))
        if hit is not None:
            return hit
    return _resolve_one({t for text in texts for t in _tokens(text)})


def _resolve_one(toks: set[str]) -> FamilyMatch | None:
    if not toks:
        return None
    sub = _substrate(toks)
    fin = _first_hit(FINISH_KEYWORDS, toks)
    if sub is not None:
        key, kw = sub
        if fin is not None and (key, fin[0]) in _RESOLVE:
            return FamilyMatch(_RESOLVE[(key, fin[0])], f"{kw}+{fin[1]}")
        fam = _RESOLVE.get((key, None))
        if fam:
            return FamilyMatch(fam, kw)
    if fin is not None and fin[0] in _FINISH_ONLY:
        return FamilyMatch(_FINISH_ONLY[fin[0]], fin[1])
    return None


def pbr_for(family: str) -> Pbr | None:
    """Factors for a fine family, or for one of the coarse ``plan.py`` families."""
    if family in MATERIALS:
        return MATERIALS[family]
    fine = COARSE_TO_FINE.get(family)
    return MATERIALS.get(fine) if fine else None


def is_framework_default(metallic: float | None, roughness: float | None, *, eps: float = 1e-3) -> bool:
    """True when (metallic, roughness) sits exactly on a framework default pair.

    ``None`` means the glTF exporter omitted the factor, which means the glTF
    default (1.0) — that is precisely the untouched case.
    """
    m = 1.0 if metallic is None else float(metallic)
    r = 1.0 if roughness is None else float(roughness)
    return any(abs(m - dm) <= eps and abs(r - dr) <= eps for dm, dr in FRAMEWORK_DEFAULTS)


def out_of_band(pbr: Pbr, metallic: float | None, roughness: float | None) -> list[str]:
    """Which of (metallic, roughness) are impossible for ``pbr``'s family."""
    m = 1.0 if metallic is None else float(metallic)
    r = 1.0 if roughness is None else float(roughness)
    bad: list[str] = []
    if not (pbr.metallic_band[0] - 1e-6 <= m <= pbr.metallic_band[1] + 1e-6):
        bad.append("metallic")
    if not (pbr.roughness_band[0] - 1e-6 <= r <= pbr.roughness_band[1] + 1e-6):
        bad.append("roughness")
    return bad


# --------------------------------------------------------------------------- cookbook
_HEAD = (
    "### Material families — copy these numbers\n\n"
    "A Principled BSDF / MeshPhysicalMaterial left on its defaults renders as grey mud, and\n"
    "the judge says so (`materials` is the weakest object criterion: mean 0.70).  Pick the row\n"
    "your part is made of and copy the numbers.  Two rules that carry most of the realism:\n\n"
    "* **Paint, glaze and varnish are DIELECTRIC.**  A painted machine body is `metallic 0.0`\n"
    "  with a clear coat — not `metallic 0.8`.  Only bare metal is metallic.\n"
    "* **Nothing in the real world has one uniform roughness.**  Vary it across a part\n"
    "  (grain, wear on edges, casting pebble) even when the colour stays flat.\n"
)


def cookbook_row(p: Pbr) -> str:
    extras = []
    if p.clearcoat:
        extras.append(f"coat {p.clearcoat:.2f}/{p.clearcoat_roughness:.2f}")
    if p.sheen:
        extras.append(f"sheen {p.sheen:.2f}")
    if p.transmission:
        extras.append(f"transmission {p.transmission:.2f}")
    base = ", ".join(f"{c:.2f}" for c in p.base_hint)
    return (f"| {p.family} | {p.metallic:.2f} | {p.roughness:.2f} | {p.ior:.2f} | "
            f"{', '.join(extras) or '—'} | {base} | {p.note} |")


def cookbook_block() -> str:
    """The whole table as cookbook markdown (see ``not_done`` for the cookbook patch)."""
    rows = [
        _HEAD,
        "| family | metallic | roughness | IOR | extras | base colour (linear sRGB) | notes |",
        "|---|---|---|---|---|---|---|",
        *[cookbook_row(p) for p in MATERIALS.values()],
        "",
        "**Blender (bpy)**",
        "```python",
        "bsdf = mat.node_tree.nodes['Principled BSDF']",
        "bsdf.inputs['Base Color'].default_value = (0.11, 0.11, 0.12, 1.0)   # cast_iron",
        "bsdf.inputs['Metallic'].default_value = 1.0",
        "bsdf.inputs['Roughness'].default_value = 0.62",
        "bsdf.inputs['IOR'].default_value = 1.5",
        "bsdf.inputs['Coat Weight'].default_value = 0.0        # 0.35 for painted_metal",
        "bsdf.inputs['Coat Roughness'].default_value = 0.10",
        "```",
        "",
        "**three.js**",
        "```js",
        "new THREE.MeshPhysicalMaterial({",
        "  color: 0x1c1c1e, metalness: 1.0, roughness: 0.62, ior: 1.5,",
        "  clearcoat: 0.0, clearcoatRoughness: 0.10,   // 0.35 / 0.10 for painted_metal",
        "});",
        "```",
        "",
        "**Roughness variation without a texture file.**  GLB export keeps only base colour,",
        "metallic, roughness, emission, alpha and normal *textures* — a Blender noise node tree",
        "does NOT survive it.  What does survive: give one part 2-3 materials that differ only in",
        "roughness and assign them per face group (a worn edge strip at -0.15, a recessed panel at",
        "+0.12, the underside at +0.20).  Two materials on one part is the cheapest thing that",
        "stops a surface reading as plastic.",
    ]
    return "\n".join(rows) + "\n"


#: families that describe BARE metal.  A strongly chromatic base colour contradicts
#: them — real cast iron, steel and chrome are neutral (or warm, for brass/copper);
#: "DarkGreenCastIron" is cast iron with PAINT on it, and paint is a dielectric.
BARE_METAL_FAMILIES = frozenset({"cast_iron", "machined_steel", "brushed_metal", "chrome"})
#: saturation above which a base colour cannot be a bare metal
PAINT_SATURATION = 0.30


def saturation(rgb: tuple[float, float, float]) -> float:
    """HSV saturation of a 0..1 RGB triple."""
    hi, lo = max(rgb), min(rgb)
    return 0.0 if hi <= 1e-6 else (hi - lo) / hi


def is_warm_metal_hue(rgb: tuple[float, float, float]) -> bool:
    """Gold / brass / copper / titanium-nitride hue: warm, with green sitting between
    red and blue rather than collapsing onto blue the way a red paint does."""
    r, g, b = rgb
    if not (r > g > b):
        return False
    span = r - b
    return span > 1e-6 and 0.28 <= (g - b) / span <= 0.88


def looks_painted(family: str, rgb: tuple[float, float, float] | None) -> bool:
    """Does this base colour rule out the bare-metal family it claims?"""
    if rgb is None or family not in BARE_METAL_FAMILIES:
        return False
    return saturation(rgb) > PAINT_SATURATION and not is_warm_metal_hue(rgb)


__all__ = [
    "COARSE_TO_FINE", "FINISH_KEYWORDS", "FRAMEWORK_DEFAULTS", "MATERIALS", "SUBSTRATE_KEYWORDS",
    "FamilyMatch", "Pbr",
    "BARE_METAL_FAMILIES", "cookbook_block", "cookbook_row", "family_for", "is_framework_default",
    "is_warm_metal_hue",
    "looks_painted", "out_of_band", "pbr_for", "saturation",
]
