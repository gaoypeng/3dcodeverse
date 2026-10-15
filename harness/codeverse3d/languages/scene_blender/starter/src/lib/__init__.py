"""src/lib — the harness-owned bpy starter library (D51 for scene_blender).

Shipped into every scene_blender workspace; an agent CALLS it (``from lib.mesh import Batch``)
and never edits it (writes under src/lib/ are reverted).  The index is the effects catalog the
prompts carry (``prompts/scene_blender/effects_catalog.md``); the lint reads these files for
syntax only.  Blender 5.2 LTS API only.
"""
