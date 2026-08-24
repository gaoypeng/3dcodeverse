"""Reference grounding: give the pipeline a picture of what it is building.

Today a prompt goes to a planner that has never SEEN the object.  This package
synthesizes a neutral studio product shot of the brief with the image model,
**validates** it (:mod:`.gate`), and hands it to the rest of the harness as the
fidelity anchor: the planner sees it, the generator's ``compare_reference`` tool
sees it, the silhouette gate measures against it and the judge scores against it
(:class:`codeverse.judges.reference.ReferenceJudge`).

A synthesized reference is never ground truth and is marked as such everywhere
(:mod:`.attach`): the user's own ``--image`` wins, the brief's dimensions win,
and a picture that fails the gate is discarded rather than chased.

Entry points::

    from codeverse.reference import synth_reference, attach, compare

    refset = synth_reference(spec, model=chat, image_model=img, n_views=2)
    spec, why = attach(spec, refset)
"""

from __future__ import annotations

from codeverse.reference.attach import attach, has_user_references, is_synthetic, reference_images
from codeverse.reference.gate import check_plausible, decide
from codeverse.reference.mismatch import compare, refine_tasks
from codeverse.reference.proportions import conflict_note, dimension_conflict, expected_aspect_range
from codeverse.reference.run import ground_spec
from codeverse.reference.synth import compose_image_prompt, synth_reference, views_for
from codeverse.reference.types import (
    SYNTH_NOTE,
    SYNTH_TAG,
    Mismatch,
    PlausibilityVerdict,
    ReferenceDiff,
    ReferenceSet,
    ReferenceView,
)

__all__ = [
    "SYNTH_NOTE", "SYNTH_TAG", "Mismatch", "PlausibilityVerdict", "ReferenceDiff", "ReferenceSet",
    "ReferenceView", "attach", "check_plausible", "compare", "compose_image_prompt", "conflict_note",
    "decide", "dimension_conflict", "expected_aspect_range",
    "ground_spec", "has_user_references", "is_synthetic", "reference_images", "refine_tasks",
    "synth_reference", "views_for",
]
