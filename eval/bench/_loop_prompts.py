"""The rung ladder and the planner-model request behind ``bench.loop generate`` (private).

A loop iteration runs ~15 scene prompts per language (owner, 2026-10-15; was 30 — enough to
pair across iterations, small enough to turn an iteration in hours), and the prompts have to be
two things at once: HARD enough to move (an iteration that scores 0.8 on every prompt has nothing
to learn), and PAIRABLE across iterations (``bench.loop compare`` pairs by prompt id).  So the
difficulty lives in a ladder of five rungs, each a set of OBJECTIVE knobs the owner fixed
(zones / asset types / instance cap / lighting / effects), a rung's prompts are generated
ONCE and cached, and an iteration's battery is a mix of the cached rungs up to ``--rung``.

The model does not write the must_have lines.  It returns STRUCTURED fields (zones, asset
types with counts, a luminance band, one effect, one animation, a camera brief, ...) and the
loop renders the scenes_v2 composition contract from them (:func:`contract_lines`) — so the
counts always parse, the band is always a band, and the rung rules are checked on typed
fields (:func:`check_prompt`), never by reading numbers back out of prose.

The brief describes a PLACE, never a layout (owner's rule, 2026-10-15): how its zones sit relative
to each other, how high its viewpoint stands and what continues past the detailed area
(``beyond``) are the brief's own choices, and the contract states them as requirements the judge
verifies — not as a shape the builder copies.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, ValidationError

from codeverse3d.contracts.chat import ChatMessage, ChatRequest
from codeverse3d.contracts.common import Language
from codeverse3d.models.schema_utils import JsonParseError, parse_json_lenient
from codeverse3d.spatial.frame_metrics import DARK_MEAN_LUM

if TYPE_CHECKING:
    from bench.run_bench import BenchPrompt

Lighting = Literal["day", "dusk", "night"]
Extra = Literal["interior", "water", "particles"]
Category = Literal["urban", "rural", "nature", "waterfront", "interior", "industrial",
                   "transport", "fantasy", "historic", "garden"]

#: the four features rungs 4 and 5 count ("two of night+water+interior+particles", "everything")
FEATURE_POOL: tuple[str, ...] = ("night", "water", "interior", "particles")
#: the band every generated luminance band must sit inside — a band outside it asks for a gated
#: scene by construction, and the diagnosis would then blame the variant for the battery.  The
#: floor IS the scene_frames gate's threshold (``frame_metrics.DARK_MEAN_LUM``, imported, never a
#: copy that drifts when the gate moves): an authored frame whose mean luminance is below it is a
#: ``dark_frame`` ERROR, capped by the scene_v2 rubric.  The gate has no ceiling on the mean
#: (``blown_frame`` is a fraction of pure-white pixels, ``frame_metrics.BLOWN_FRAC``), so the
#: ceiling is the loop's own margin under the white point.
LUMINANCE_FLOOR = DARK_MEAN_LUM
LUMINANCE_CEILING = 0.85


@dataclass(frozen=True)
class Rung:
    """One difficulty rung: the objective knobs the owner fixed on 2026-10-14."""

    level: int
    zones: int          # at least this many named zones
    asset_types: int    # at least this many distinct asset types
    instances: int      # the counts of every asset type sum to at most this
    tier: str           # the battery tier the rung's prompts are filed under
    require_all: tuple[str, ...] = ()   # every one of these features
    require_any: tuple[str, ...] = ()   # at least one of these
    require_count: int = 0              # at least this many of FEATURE_POOL
    forbid: tuple[str, ...] = ()
    hero: bool = False                  # a moving hero asset is named
    scale: bool = False                 # precise scale constraints are stated
    brief: str = ""                     # the one line the generation prompt states


RUNGS: dict[int, Rung] = {
    1: Rung(1, zones=3, asset_types=6, instances=40, tier="medium", require_all=("day",), forbid=("water",),
            brief="daylight, no water — a small readable place"),
    2: Rung(2, zones=4, asset_types=8, instances=80, tier="medium", require_any=("dusk", "interior"),
            brief="dusk lighting OR an interior"),
    3: Rung(3, zones=5, asset_types=10, instances=150, tier="hard", require_any=("night", "water", "particles"),
            brief="night, water or particles"),
    4: Rung(4, zones=6, asset_types=12, instances=250, tier="hard", require_count=2, hero=True,
            brief="two of night / water / interior / particles, plus a moving hero"),
    5: Rung(5, zones=6, asset_types=14, instances=400, tier="hard", require_all=FEATURE_POOL, hero=True, scale=True,
            brief="night AND water AND an interior AND particles, a moving hero, precise scale constraints"),
}


class AssetCount(BaseModel):
    name: str = Field(description="plural noun phrase, e.g. 'market stalls'")
    count: int = Field(ge=1, description="how many instances the scene must place")
    designs: int = Field(default=1, ge=1, description="distinct designs among them (1 = all alike)")


class GeneratedPrompt(BaseModel):
    """What the planner model returns per prompt — typed, so the contract is rendered, not parsed."""

    title: str = Field(description="3-6 words")
    category: Category
    prompt: str = Field(description="the brief: 2-4 sentences naming the place, its zones, the time of day and one activity")
    zones: list[str] = Field(description="named zones, short lowercase phrases; how they relate in space (side by side, "
                                         "nested, stacked, across water, along a road that leaves the frame) is the "
                                         "prompt text's choice")
    beyond: str = Field(description="what continues past the detailed area, one clause — the rest of the street, open "
                                    "water to the horizon, more forest, a skyline, farmland, a wall; the place does not "
                                    "stop where its zones stop")
    asset_types: list[AssetCount]
    lighting: Lighting
    features: list[Extra] = Field(default_factory=list)
    mood: str = Field(description="the lighting mood in one clause a viewer can verify in a render")
    luminance_low: float = Field(ge=0.0, le=1.0, description="mean frame luminance band, low edge")
    luminance_high: float = Field(ge=0.0, le=1.0, description="mean frame luminance band, high edge")
    effect: str = Field(description="ONE water / weather / particle / shader effect, concrete and visible")
    animation: str = Field(description="ONE thing that visibly moves between the sampled times")
    camera: str = Field(description="ONE fixed viewpoint — where it stands, what it looks along and its height above "
                                    "the surface it stands on (eye level, 1.6 m, is the usual choice; a balcony, tower, "
                                    "ridge or quay crane is fine when the place has one), e.g. 'at the quay's landward "
                                    "end looking along it at 1.6 m'; a still camera, never a path")
    hero: str = Field(default="", description="rungs 4-5: the one hero asset")
    hero_motion: str = Field(default="", description="rungs 4-5: how the hero moves")
    scale_constraints: list[str] = Field(default_factory=list, description="rung 5: 'the lighthouse is 18 m tall'")

    @property
    def feature_set(self) -> frozenset[str]:
        return frozenset([self.lighting, *self.features])


class GeneratedBattery(BaseModel):
    prompts: list[GeneratedPrompt]


#: one acceptance line per extra feature, fixed wording so every battery states them alike
FEATURE_LINES: dict[str, str] = {
    "interior": "interior: the authored cameras are inside the built space — floor, walls and ceiling enclose the view",
    "water": "water: a water surface with reflections and visible ripples is in frame from an authored camera",
    "particles": "particles: a soft particle effect (dust, steam, sparks, snow, rain) is visible, never hard-edged quads",
}


def check_prompt(gp: GeneratedPrompt, rung: Rung) -> list[str]:
    """Every way ``gp`` falls short of ``rung`` — empty when it fits.  The loop re-asks with this list."""
    problems: list[str] = []
    if len(gp.zones) < rung.zones:
        problems.append(f"{gp.title!r}: {len(gp.zones)} zones, rung {rung.level} needs at least {rung.zones}")
    if len({z.strip().lower() for z in gp.zones}) != len(gp.zones):
        problems.append(f"{gp.title!r}: duplicate zone names")
    if len(gp.asset_types) < rung.asset_types:
        problems.append(f"{gp.title!r}: {len(gp.asset_types)} asset types, rung {rung.level} needs at least {rung.asset_types}")
    total = sum(a.count for a in gp.asset_types)
    if total > rung.instances:
        problems.append(f"{gp.title!r}: {total} instances in total, rung {rung.level} allows at most {rung.instances}")
    if not gp.luminance_low < gp.luminance_high:
        problems.append(f"{gp.title!r}: luminance band {gp.luminance_low}-{gp.luminance_high} is not low < high")
    if gp.luminance_low < LUMINANCE_FLOOR or gp.luminance_high > LUMINANCE_CEILING:
        problems.append(f"{gp.title!r}: luminance band must sit inside {LUMINANCE_FLOOR}-{LUMINANCE_CEILING} "
                        "(the frame gate errors on darker / blown frames)")
    have = gp.feature_set
    for f in rung.require_all:
        if f not in have:
            problems.append(f"{gp.title!r}: rung {rung.level} requires {f!r} (has {sorted(have)})")
    if rung.require_any and not have & set(rung.require_any):
        problems.append(f"{gp.title!r}: rung {rung.level} requires one of {list(rung.require_any)} (has {sorted(have)})")
    if rung.require_count and len(have & set(FEATURE_POOL)) < rung.require_count:
        problems.append(f"{gp.title!r}: rung {rung.level} requires {rung.require_count} of {list(FEATURE_POOL)} (has {sorted(have)})")
    for f in rung.forbid:
        if f in have:
            problems.append(f"{gp.title!r}: rung {rung.level} forbids {f!r}")
    if rung.hero and not (gp.hero.strip() and gp.hero_motion.strip()):
        problems.append(f"{gp.title!r}: rung {rung.level} needs a hero and its motion")
    if rung.scale and len(gp.scale_constraints) < 2:
        problems.append(f"{gp.title!r}: rung {rung.level} needs at least 2 scale constraints")
    for field in ("effect", "animation", "camera", "mood", "prompt", "beyond"):
        if not getattr(gp, field).strip():
            problems.append(f"{gp.title!r}: empty {field}")
    return problems


#: The contract's camera line (2026-10-15).  Both scene languages render the AUTHORED cameras as stills
#: at t = 0 and t = 1.5 s (``scene_blender.TIMES``, ``scene_threejs`` ``times``) — nothing dollies — so a
#: brief that asked for a tracking camera was a must-have no run could satisfy: the harness planner turns
#: every must_have line into a ``must`` acceptance item, and the judge marked it unverified in 5 of
#: iteration 0's 60 runs ("remains stationary between t=0s and t=1.5s"), which caps the whole score
#: (``missing_must_acceptance``).  Every camera line starts with a fixed-form prefix — :func:`migrate_camera_lines`
#: recognises the form by it — and ends with the suffix.  ``CAMERA_PREFIX`` is the form written since
#: 2026-10-15: FIXED is interface (stills only), the height is the brief's own (a tower, a balcony, eye level —
#: the harness measures the eye >= 0.3 m above what it stands on and >= 0.5 m from every surface), so the
#: "eye-level" of the first fixed form is no longer baked into an acceptance item the judge verifies.
#: ``LEGACY_CAMERA_PREFIX`` is that first form: the migrated lines of the cached rungs keep it verbatim (they
#: pair iterations), and the migration recognises both forms as already fixed.
CAMERA_PREFIX = "camera: a FIXED camera"
LEGACY_CAMERA_PREFIX = "camera: a FIXED eye-level camera"
CAMERA_SUFFIX = ("; the harness renders still cameras only (nothing dollies or tracks); it never intersects any "
                 "geometry (no clipping, nothing cut by the near plane)")
FIXED_CAMERA_PREFIXES: tuple[str, ...] = (CAMERA_PREFIX, LEGACY_CAMERA_PREFIX)


def camera_line(camera: str) -> str:
    """The must-have line for a viewpoint brief (where it stands, what it looks along, its height)."""
    return f"{CAMERA_PREFIX}, {camera.strip()}{CAMERA_SUFFIX}"


def migrate_camera_lines(prompts: Sequence[BenchPrompt]) -> int:
    """Rewrite, in place, every ``must_have`` camera line still in the old moving-camera form
    (``camera: <route>; the camera never intersects …``) into the fixed form, keeping the route the model
    wrote as where the viewpoint stands and looks; returns how many lines changed.  Idempotent — a line
    already in the fixed form is left alone, as is every non-camera line.  The cached rung prompts keep
    their ids (they pair iterations), so the line moves and nothing else does."""
    n = 0
    for p in prompts:
        for i, line in enumerate(p.must_have):
            if not line.startswith("camera: ") or line.startswith(FIXED_CAMERA_PREFIXES):
                continue
            route = line.removeprefix("camera: ").split(";", 1)[0].strip()
            p.must_have[i] = (f'{LEGACY_CAMERA_PREFIX} standing where this route begins and looking along it — "{route}"'
                              f"{CAMERA_SUFFIX}")
            n += 1
    return n


def contract_lines(gp: GeneratedPrompt) -> list[str]:
    """The scenes_v2 composition contract rendered from the typed fields (the ``must_have`` list)."""
    lines = [f"{len(gp.zones)} zones: {', '.join(z.strip() for z in gp.zones)}",
             f"beyond: {gp.beyond.strip()} — the place continues past the detailed zones, visibly, from the authored camera"]
    for a in gp.asset_types:
        line = f"at least {a.count} {a.name.strip()}"
        if a.designs > 1:
            line += f" using at least {a.designs} distinct designs"
        lines.append(line)
    lines.append(f"{gp.lighting} mood: {gp.mood.strip()} (mean luminance roughly "
                 f"{gp.luminance_low:.2f}-{gp.luminance_high:.2f}, no black or blown-out frames)")
    lines.extend(FEATURE_LINES[f] for f in FEATURE_LINES if f in gp.features)
    lines.append(f"effect: {gp.effect.strip()}")
    lines.append(f"animation: {gp.animation.strip()}")
    lines.append(camera_line(gp.camera))
    if gp.hero.strip():
        lines.append(f"hero: {gp.hero.strip()} — {gp.hero_motion.strip()}")
    lines.extend(f"scale: {s.strip()}" for s in gp.scale_constraints if s.strip())
    return lines


def instance_total(gp: GeneratedPrompt) -> int:
    return sum(a.count for a in gp.asset_types)


# --------------------------------------------------------------------------- the request
SYSTEM = (
    "You write evaluation prompts for a procedural 3D scene generator: each prompt describes ONE place "
    "that a coding agent builds as a procedural 3D scene (named zones, repeated assets with counts, lighting, "
    "one effect, one animation, authored cameras).  Prompts are concrete, varied across categories and "
    "settings, and buildable procedurally in code (no external models or textures) — no licensed characters, "
    "no text-heavy signage, no real people.  Describe the place, not a layout: where its zones sit relative to "
    "each other, how high its viewpoint stands and what continues past the detailed area are each prompt's own "
    "choices, and they should differ from prompt to prompt.  Answer ONLY with the JSON the schema describes."
)


def rung_text(rung: Rung) -> str:
    lines = [
        f"Rung {rung.level} ({rung.tier}): {rung.brief}.",
        f"- at least {rung.zones} named zones, each a distinct part of the place the prompt describes; the prompt text "
        "says how they sit relative to each other (side by side, nested, stacked, across water, along a road that "
        "leaves the frame — the place's own arrangement)",
        "- beyond: what continues past the detailed zones (the rest of the street, open water, hills, a skyline, "
        "farmland, a wall) — the place does not end where its zones end",
        f"- at least {rung.asset_types} distinct asset types, each with an explicit instance count; the counts "
        f"must sum to at most {rung.instances} instances in the whole scene",
        "- lighting is one of day / dusk / night; extra features are any of interior / water / particles",
        f"- a mean-luminance band inside {LUMINANCE_FLOOR:.2f}-{LUMINANCE_CEILING:.2f} that fits the mood "
        f"(day ≈ 0.35-0.65, dusk ≈ 0.20-0.45, night ≈ {LUMINANCE_FLOOR:.2f}-0.35)",
        "- exactly one effect (water surface, rain, snow, steam, dust, sparks, fog volume, a custom shader material)",
        "- exactly one animation (a thing that visibly moves between two sampled times)",
        "- a camera brief: ONE fixed viewpoint — where it stands, what it looks along and how high it is (eye level "
        "is one option; a tower, balcony, ridge or deck is another when the place has it); the scenes are rendered "
        "from still cameras only (nothing dollies or tracks), and the camera never intersects geometry",
    ]
    if rung.require_all:
        lines.append(f"- REQUIRED features: {', '.join(rung.require_all)}")
    if rung.require_any:
        lines.append(f"- at least one of: {', '.join(rung.require_any)}")
    if rung.require_count:
        lines.append(f"- at least {rung.require_count} of: {', '.join(FEATURE_POOL)}")
    if rung.forbid:
        lines.append(f"- FORBIDDEN: {', '.join(rung.forbid)}")
    if rung.hero:
        lines.append("- name one HERO asset (the thing the scene is about) and how it moves")
    if rung.scale:
        lines.append("- state at least two PRECISE scale constraints in metres (a named asset's height or span)")
    return "\n".join(lines)


def build_request(rung: Rung, n: int, language: Language, *, label: str = "loop:generate",
                  previous_text: str = "", problems: Sequence[str] = ()) -> ChatRequest:
    """The structured request for ``n`` prompts of ``rung``.  A re-ask carries the previous answer
    (``previous_text``, as the model wrote it) and the typed problems with it, so the model repairs
    rather than reinvents."""
    user = (
        f"Write {n} distinct scene prompts for rung {rung.level}.  The scenes will be built in "
        f"{'Blender (bpy, Cycles)' if language is Language.SCENE_BLENDER else 'three.js (WebGL)'}; the prompt text "
        "itself must stay engine-neutral.\n\n" + rung_text(rung) + "\n\n"
        "Spread the prompts over the categories the schema lists (no category more than a quarter of them), "
        "vary era, climate and scale, and keep every title unique.  Zones are short lowercase phrases; asset "
        "type names are plural nouns.  A zone named in `zones` must be mentioned in the prompt text."
    )
    messages = [ChatMessage.user(user)]
    if problems:
        messages.append(ChatMessage.assistant(previous_text[:60_000] or "(no answer)"))
        messages.append(ChatMessage.user(
            "Your answer failed these checks — return the FULL corrected list (every prompt, same count), fixing "
            "only what the checks name:\n- " + "\n- ".join(problems)))
    return ChatRequest(messages=messages, system=SYSTEM, response_schema=GeneratedBattery.model_json_schema(),
                       temperature=0.9, thinking="medium", max_output_tokens=65_536, max_wait_s=900.0, label=label)


def parse_battery(text: str, parsed: Any = None) -> tuple[GeneratedBattery | None, list[str]]:
    """``(battery, problems)``: the model's answer validated against the schema; a parse or validation
    failure is a problem the re-ask states, not an exception."""
    try:
        payload = parsed if parsed is not None else parse_json_lenient(text)
        return GeneratedBattery.model_validate(payload), []
    except (JsonParseError, ValidationError, TypeError) as e:
        return None, [f"the answer did not match the schema: {str(e)[:600]}"]


def check_battery(battery: GeneratedBattery, rung: Rung, n: int) -> list[str]:
    """Every problem across the battery: the rung rules per prompt, the count, duplicate titles."""
    problems: list[str] = []
    if len(battery.prompts) < n:
        problems.append(f"{len(battery.prompts)} prompts returned, {n} needed")
    titles = [p.title.strip().lower() for p in battery.prompts]
    if len(set(titles)) != len(titles):
        problems.append("duplicate titles")
    for gp in battery.prompts:
        problems.extend(check_prompt(gp, rung))
    return problems


# --------------------------------------------------------------------------- the mix
def mix_counts(top: int, n: int) -> dict[int, int]:
    """How many prompts of each rung 1..``top`` an ``n``-prompt battery takes: an even split, the
    remainder to the HARDEST rungs — so a battery at rung 2 keeps half of rung 1's ids (the pairs
    ``compare`` needs against the previous iteration) and the other half moves up."""
    if top < 1 or top > max(RUNGS):
        raise ValueError(f"rung must be 1..{max(RUNGS)}, got {top}")
    base, rem = divmod(n, top)
    return {r: base + (1 if r > top - rem else 0) for r in range(1, top + 1)}


__all__ = ["CAMERA_PREFIX", "FEATURE_POOL", "LEGACY_CAMERA_PREFIX", "RUNGS", "AssetCount", "GeneratedBattery",
           "GeneratedPrompt", "Rung", "build_request", "camera_line", "check_battery", "check_prompt", "contract_lines",
           "instance_total", "migrate_camera_lines", "mix_counts", "parse_battery", "rung_text"]
