"""codeverse3d — the 3dcodeverse harness.

A backend harness in which LLMs (API models or agentic CLIs) write RAW,
executable 3D code — Blender ``bpy``, CadQuery, Three.js, URDF, GLSL — for
four tracks (static objects, articulated objects, scenes, graphics).  Every run is
planned, generated, linted, executed, measured, rendered, judged, refined and
finally emitted as a data-flywheel record.

Package map (each sub-package has its own docstring):

- ``conventions``   single source of truth for frames, units, naming.
- ``config``        settings (keys, binaries, defaults) — layered, typed.
- ``contracts``     pydantic models shared by everything (Spec, Plan, Gate,
                    Judgment, Usage, RunRecord ...).  No logic lives here.
- ``models``        ChatModel backends: Gemini / Anthropic / OpenAI (+ key pool).
- ``agents``        CodingAgent backends: the vendor CLIs — gemini-cli /
                    claude-code / codex / antigravity.
- ``languages``     LanguageRuntime per raw language: lint, build, export.
- ``spatial``       language-agnostic 3D tools on GLB/scene: measure, render,
                    slice, isolate, silhouette, joints, probes — also served as
                    an MCP server to agentic harnesses.
- ``judges``        VLM judges + rubrics + deterministic metrics.
- ``tracks``        static_object / articulated_object / scene / graphics pipelines.
- ``orchestrator``  stage runner, round loop, budget, candidate selection.
- ``record``        what every run writes: record.json, deliverable/, telemetry.
- ``addons``        optional tools that READ finished runs (gallery, dataset, cost report, …).
- ``cli``           ``3dcode`` command line.
"""

import os as _os

__version__ = "0.1.0"


def _adopt_legacy_env() -> None:
    """``CV3D_`` was the settings prefix until D78 (2026-09-22).  A shell or a script that still
    sets ``CV3D_X`` keeps working: each one with no ``C3D_X`` beside it is copied over once, at
    first import — before anything reads a switch — and named in one warning."""
    old = sorted(k for k in _os.environ if k.startswith("CV3D_") and "C3D_" + k[5:] not in _os.environ)
    for k in old:
        _os.environ["C3D_" + k[5:]] = _os.environ[k]
    if old:
        import logging

        logging.getLogger(__name__).warning("the CV3D_ prefix is now C3D_; read as C3D_: %s", ", ".join(old))


_adopt_legacy_env()
