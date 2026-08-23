Model this object in raw bpy: a desk lamp with a heavy round base, a single angled arm and a conical metal shade
MUST HAVE: round base on the ground
MUST HAVE: one straight arm leaning forward
MUST HAVE: conical shade at the top of the arm
MUST HAVE: shade opens downward

You write ONE file, `src/model.py`, in raw Blender Python (bpy, Blender 5.x API).
The harness runs it headless (`blender -b --factory-startup --python <wrapper> -- --script src/model.py`)
inside an EMPTIED scene (no default cube / camera / light) and then exports the result itself.

Frame, units and placement: Coordinate frame: Z is UP, -Y is the FRONT of the object (the face a viewer sees from the default front view), +X is the object's right. Units are meters. The object stands on the ground plane z=0 and its footprint is centred on the Z axis.  Build at real-world size.

Naming: one mesh object per part, `obj.name` set to a unique PascalCase part name (e.g. `Seat`,
`LegFrontLeft`); no auto-suffixed duplicates like `Leg.001`.  Link every object to
`bpy.context.scene.collection`.  Every visible mesh gets a material (Principled BSDF base colour /
roughness / metallic).

Do NOT: create cameras or lights, touch render/world settings, call `bpy.ops.render.*`,
`bpy.ops.export_*`, `bpy.ops.import_*`, `bpy.ops.wm.*`, read or write files, use the network, or
import anything other than `bpy`, `bmesh`, `mathutils`, `math`, `random`.  Keep the total under
500k triangles and finish in under 120 s.

The script must build the whole object when executed top-to-bottom (call your `main()` at module
level).  Make sure the code runs without errors in headless Blender — there is no second chance.

You have NO tools in this session: you cannot write files, run Blender or inspect anything — the code must appear in your reply.  Reply with the COMPLETE contents of `src/model.py` as ONE ```python fenced code block and nothing else (no prose before or after, no partial snippets).