"""codeverse3d — the 3dcodeverse harness.

A backend harness in which LLMs write RAW, executable 3D code (Blender ``bpy``, CadQuery,
Three.js, URDF, GLSL) for four tracks (static objects, articulated objects, scenes, graphics).

Package map:

- ``conventions``   frames, units, naming.      - ``config``   typed layered settings.
- ``contracts``     shared pydantic models.     - ``models``   ChatModel backends + key pool.
- ``agents``        vendor CLI coding agents.   - ``languages``  per-language lint / build / export.
- ``spatial``       3D tools on GLB / scene, also an MCP server.
- ``judges``        VLM judges + rubrics.       - ``tracks``   the four pipelines.
- ``orchestrator``  stage runner, round loop.   - ``record``   record.json, deliverable/, telemetry.
- ``addons``        tools that read finished runs.   - ``cli``   the ``3dcodeverse`` command line.
"""

__version__ = "0.1.0"
