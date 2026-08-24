"""Prompt text for reference grounding, kept as module constants.

Deliberately NOT under ``codeverse/prompts/``: this package must be able to
change its wording without touching a directory other waves author in.  The
hashes of these constants are recorded on every ``ReferenceSet`` cache key, so
a wording change invalidates the cache the same way a template change would.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- image prompt writer
IMAGE_PROMPT_SYSTEM = (
    "You write prompts for a text-to-image model.  You are given a 3D modelling brief.  Your job is to "
    "describe THE SAME OBJECT as a neutral studio product photograph, so a 3D modeller can use the picture "
    "as a shape target.  Describe only what a camera would see: the object's parts, their proportions and "
    "their materials.  Never invent brand names, never add props, people, hands, scale figures or scenery. "
    "Answer with JSON only."
)

IMAGE_PROMPT_USER = """\
3D MODELLING BRIEF
------------------
{brief}

Write ONE `subject` sentence (25-60 words) describing this object as it would look in a product catalogue:
its overall form, its named parts, their relative sizes, and the material of each part.  Keep every explicit
requirement of the brief (part counts, features, materials).  Do not mention the camera, the background or
the lighting — the harness adds those.

Then write `object_name`: 1-4 words naming the object (e.g. "hand-crank coffee grinder").

Then, for each of the {n_views} requested views {views}, write a `view_note`: at most 12 words saying what
that camera angle must make readable (e.g. "front face, crank handle silhouette, drawer front").
"""

#: Harness-owned camera/background clause appended to every image prompt — this is
#: what makes the picture usable as a silhouette target (plain backdrop, whole object
#: in frame, no crop, no styling).
STUDIO_SUFFIX = (
    "neutral studio product photograph of a single object, isolated on a plain seamless light grey "
    "background, the complete object fully inside the frame with a small even margin on all sides, "
    "nothing cropped, standing upright in its natural resting orientation, soft even studio lighting, "
    "no cast shadow on the backdrop, sharp focus throughout, no depth of field, no props, no hands, "
    "no people, no scale figures, no scenery, no text, no labels, no logos, no watermark, no border, "
    "no collage, no multiple views in one image, single frame only, photorealistic, colour photograph"
)

#: view name → the camera clause the harness owns for it
VIEW_CLAUSE: dict[str, str] = {
    "three_quarter": "three-quarter view from slightly above, front and one side both visible",
    "front": "straight-on front elevation, camera at object height, no perspective distortion",
    "side": "straight-on side elevation, camera at object height, no perspective distortion",
    "back": "straight-on rear elevation, camera at object height",
    "detail": "close view of the most characteristic feature, whole feature in frame",
}

#: order views are requested in as ``n_views`` grows.  The straight-on ``front``
#: elevation comes second on purpose: it is the SILHOUETTE TARGET, and the harness
#: measures IoU against the run's ``front`` render (``tracks.reference.silhouette_gate``),
#: so the two cameras must agree.  The 3/4 shot carries the part inventory.
VIEW_ORDER: tuple[str, ...] = ("three_quarter", "front", "side", "back")


# --------------------------------------------------------------------------- plausibility gate
GATE_SYSTEM = (
    "You verify whether a generated image can be used as a modelling reference.  You are strict and "
    "literal: you report what the image actually shows, not what it was meant to show.  Answer with JSON only."
)

GATE_USER = """\
The image was generated as a reference photo for this 3D modelling brief.

BRIEF
-----
{brief}

EXPLICIT CONSTRAINTS THE IMAGE MUST NOT CONTRADICT
--------------------------------------------------
{constraints}

Answer these questions about the IMAGE:
- `depicted_object`: 1-6 words naming what the image actually shows.
- `shows_requested_object`: true only if the depicted object IS the object the brief asks for
  (a different object, or an unrecognisable blob, is false).
- `single_object`: true only if exactly ONE object is shown (a base/stand/tray that the brief itself
  asks for counts as part of the object; a second copy of the object, extra props or accessories do not).
- `plain_background`: true only if the background is a plain, empty, untextured backdrop.
- `no_text_or_watermark`: true only if there is NO text, caption, label, dimension line, logo or watermark.
- `is_photo_collage`: true if the frame contains multiple panels, multiple views, a grid or a turnaround sheet.
- `contradictions`: for EACH constraint above that the image visibly contradicts, one short sentence naming
  the constraint and what the image shows instead.  Empty list if none.  Only list what you can SEE — do not
  guess at absolute dimensions from a photo with no scale reference.
- `reason`: one sentence summarising your verdict.
"""


# --------------------------------------------------------------------------- reference diff
DIFF_SYSTEM = (
    "You compare a 3D render against a reference image and name concrete, fixable differences. "
    "You never comment on background, lighting, camera framing or image style — only on the object's "
    "shape, its parts, their counts, their proportions, their placement and their materials. "
    "Answer with JSON only."
)

DIFF_USER = """\
The images labelled REFERENCE are the shape target.  The images labelled RENDER are the current 3D model.
{synth_note}

BRIEF
-----
{brief}
{measured}
List the differences that make the RENDER read as less like the real object than the REFERENCE does.
For each, give:
- `kind`: missing_feature | extra_feature | wrong_part_count | wrong_proportion | wrong_shape |
  wrong_material | wrong_placement
- `target`: the part name it concerns (use a name from this list when one fits: {parts}), else "overall"
- `detail`: what the REFERENCE shows and what the RENDER shows instead — one sentence, concrete and
  checkable (name the count, the ratio or the shape, e.g. "reference has 8 flutes around the body,
  render has a smooth cylinder")
- `severity`: critical (the object no longer reads as the right thing) | major | minor

Rules:
- At most 6 mismatches; order them so the most identity-destroying comes first.
- Ignore anything the brief overrides: where the brief states a dimension, a count or a material, the BRIEF
  wins and the reference does not.
- Do not report background, lighting, shadow, reflection, resolution or camera-angle differences.
- `matches`: up to 4 short phrases naming what the render already gets right (so the builder does not undo it).
"""
