"""codeverse3d — the 3dcodeverse plugin's instruments.

Deterministic tools for a host coding agent writing RAW 3D code (Blender ``bpy``, CadQuery, Three.js,
URDF, GLSL / OpenGL) across four tracks (static objects, articulated objects, scenes, graphics),
driven from the ``3dcode`` command line.

Package map:

- ``conventions``   frames, units, naming.      - ``config``     typed layered settings.
- ``contracts``     shared pydantic models.     - ``languages``  per-language lint / build / export.
- ``spatial``       measurement, gates and renders on GLB / scene / frames; the tool registry.
- ``gates``         per-track gate assembly and render rig.   - ``plan_checks``  plan validation.
- ``judges``        rubrics, score arithmetic, the judge brief.   - ``workflow``  init / validate-plan / check / judge-pack / score.
- ``cli``           the ``3dcode`` command line.   - ``doctor``   environment checks.
"""

__version__ = "0.0.10"
