"""The fixed evaluator builds each cell with ITS language's runtime, not Blender's."""

from __future__ import annotations

from codeverse.contracts.common import Language


def test_the_evaluator_asks_for_the_cells_own_runtime(monkeypatch):
    """Measured 2026-08-26 on fancy_v1/tj: a three.js cell judged 0.589 in its run was then
    built by the Blender runtime at eval time — `MissingEntryFile: src/model.py` — and recorded
    build_failed 0.0.  A false zero, the b4ea4cc bug class one layer down."""
    import codeverse.languages as langs
    from bench._fixed_eval import FixedEvaluator

    asked: list[Language] = []

    class _RT:
        def __init__(self, lang): self.lang = lang

    monkeypatch.setattr(langs, "get_runtime", lambda lang: asked.append(Language(lang)) or _RT(Language(lang)))
    ev = FixedEvaluator("gemini:fake", n_samples=1)
    assert ev.runtime(Language.THREEJS).lang is Language.THREEJS
    assert ev.runtime("blender").lang is Language.BLENDER
    assert ev.runtime(Language.THREEJS).lang is Language.THREEJS, "cached per language, not one global"
    assert asked == [Language.THREEJS, Language.BLENDER], "each language's runtime is constructed once"
