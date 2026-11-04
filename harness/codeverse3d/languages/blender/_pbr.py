"""The material-family table: PBR factors (metallic, roughness, IOR, clear-coat) per finish.

``languages/blender`` reads it (``family_for`` / ``pbr_for``).  Data + pure functions only.
"""

from __future__ import annotations

from typing import NamedTuple

from pydantic import BaseModel, Field

from codeverse3d.conventions import to_snake


class Pbr(BaseModel):
    """One material family's physically-plausible factors."""

    family: str
    metallic: float = Field(ge=0.0, le=1.0)
    roughness: float = Field(ge=0.0, le=1.0)
    ior: float = 1.5
    clearcoat: float = 0.0
    clearcoat_roughness: float = 0.1
    sheen: float = 0.0
    transmission: float = 0.0
#: base colour a real sample of this finish sits near (a hint, never forced)
    base_hint: tuple[float, float, float] = (0.5, 0.5, 0.5)
#: plausible roughness spread (the normaliser's confidence band)
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


#: family -> PBR factors with plausible bands
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

#: substrate keywords (what the part is made of), checked before finishes
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

#: (substrate, finish) -> family; ``None`` finish = bare
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

#: the coarse families (wood, metal, ...) -> a representative fine family, for ``pbr_for``.
COARSE_TO_FINE: dict[str, str] = {
    "wood": "hardwood", "metal": "machined_steel", "fabric": "fabric", "stone": "stone",
    "plastic": "rough_plastic", "leather": "leather", "glass": "glass", "ceramic": "ceramic",
    "painted": "painted_metal", "rubber": "rubber", "other": "rough_plastic",
}


class FamilyMatch(NamedTuple):
    family: str
    keyword: str


def _tokens(text: str) -> set[str]:
    """Whole words of ``text`` as ``to_snake`` splits them, plus the words joined."""
    s = str(text or "")
    parts = [w for w in to_snake(s).split("_") if w] if any(c.isascii() and c.isalnum() for c in s) else []
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
    """Resolve a material family from free text; ``None`` when nothing matches.

    The first argument that resolves wins; within one text, substrate before finish."""
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


__all__ = ["COARSE_TO_FINE", "FINISH_KEYWORDS", "MATERIALS", "SUBSTRATE_KEYWORDS", "FamilyMatch", "Pbr", "family_for", "pbr_for"]
