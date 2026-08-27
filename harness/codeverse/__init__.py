"""codeverse — the 3dcodeverse harness.

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
- ``agents``        CodingAgent backends: gemini-cli / claude-code / codex /
                    antigravity / api-agent (tool loop on any ChatModel).
- ``languages``     LanguageRuntime per raw language: lint, build, export.
- ``spatial``       language-agnostic 3D tools on GLB/scene: measure, render,
                    slice, isolate, silhouette, joints, probes — also served as
                    an MCP server to agentic harnesses.
- ``judges``        VLM judges + rubrics + deterministic metrics.
- ``tracks``        static_object / articulated_object / scene / graphics pipelines.
- ``orchestrator``  stage runner, round loop, fan-out, budget, events.
- ``flywheel``      run records → dataset samples / preference pairs.
- ``cli``           ``3dcv`` command line.
"""

__version__ = "0.1.0"
