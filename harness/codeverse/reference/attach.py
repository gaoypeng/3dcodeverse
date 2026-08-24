"""Attaching a synthesized reference to a Spec — with the honesty guards.

Three rules, enforced here so no caller can forget them:

1. **The user's own references always win.**  A spec that already carries
   ``--image`` references is returned untouched; synthesis is skipped entirely.
2. **A synthesized reference is marked as such everywhere.**  Every attached
   ``ReferenceImage`` carries :data:`~codeverse.reference.types.SYNTH_NOTE`
   (which the planner label, the generator's reference note and the judge's
   image label all print verbatim) and the spec gains the
   :data:`~codeverse.reference.types.SYNTH_TAG` tag, which flows into
   ``record.json`` (``spec.tags``) and the dataset meta (``flywheel/sample.py``).
   The local gallery does not render tags yet — one badge there would close the
   loop visually.
3. **It never overrides explicit dimensions.**  ``spec.constraints`` is copied
   through unchanged — the picture is a shape target, the brief is the contract.
"""

from __future__ import annotations

from pathlib import Path

from codeverse.contracts.spec import ReferenceImage, Spec
from codeverse.reference.types import SYNTH_NOTE, SYNTH_TAG, ReferenceSet


def has_user_references(spec: Spec) -> bool:
    """True when the user supplied their own reference image(s) (``--image``)."""
    return any(not is_synthetic(r) for r in spec.references)


def is_synthetic(ref: ReferenceImage) -> bool:
    return ref.note.startswith("SYNTHESIZED")


#: view kinds that can serve as the silhouette ``target``, best first — the harness
#: measures IoU against a straight-on render, so a straight-on photo is the right
#: target and the three-quarter shot is only a detail/part-inventory image.
TARGET_PREFERENCE: tuple[str, ...] = ("front", "side", "back", "three_quarter")


def reference_images(refset: ReferenceSet) -> list[ReferenceImage]:
    """Accepted views as spec references, ``target`` first.

    Exactly one view is the ``target`` (the only one the silhouette gate and the
    measured ``silhouette_match`` criterion compare against); the rest are
    ``detail`` images the planner, generator and judge still see.
    """
    usable = [v for v in refset.accepted if v.path and Path(v.path).is_file()]
    if not usable:
        return []
    target = min(usable, key=lambda v: (TARGET_PREFERENCE.index(v.view)
                                        if v.view in TARGET_PREFERENCE else len(TARGET_PREFERENCE)))
    ordered = [target] + [v for v in usable if v is not target]
    return [ReferenceImage(path=str(Path(v.path).resolve()), role="target" if v is target else "detail",
                           note=f"{SYNTH_NOTE} [{v.view} view, {refset.image_model or 'image model'}]")
            for v in ordered]


def attach(spec: Spec, refset: ReferenceSet) -> tuple[Spec, str]:
    """``(spec, why)`` — the spec to run with, and one line saying what happened.

    The returned spec is a copy; ``spec`` itself is never mutated.
    """
    if has_user_references(spec):
        return spec, "user --image references win; synthesized reference not attached"
    refs = reference_images(refset)
    if not refs:
        return spec, f"no usable synthesized reference ({refset.summary()}) — running without one"
    tags = list(spec.tags) + ([SYNTH_TAG] if SYNTH_TAG not in spec.tags else [])
    return spec.model_copy(update={"references": refs, "tags": tags}), (
        f"attached {len(refs)} SYNTHESIZED reference image(s) ({refset.summary()})"
    )


__all__ = ["TARGET_PREFERENCE", "attach", "has_user_references", "is_synthetic", "reference_images"]
