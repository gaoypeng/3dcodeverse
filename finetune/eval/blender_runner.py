"""Run ONE generated Blender-Python script headlessly inside Blender and export a GLB + JSON report.

Usage (inside blender):
  blender -b --factory-startup --python blender_runner.py -- --script gen.py --out out.glb --report out.json
"""
import sys, json, time, traceback, argparse, runpy, os, io, contextlib

def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)

def main():
    args = parse()
    import bpy
    t0 = time.time()
    rep = {"script": args.script, "out_glb": args.out, "status": "FAIL", "error": None,
           "n_meshes": 0, "n_verts": 0, "n_faces": 0, "size_kb": 0.0, "latency_s": 0.0}
    # factory startup scene (keeps the default 'Collection' in the view layer, like the official export),
    # but remove the default Cube/Light/Camera so an empty generation cannot pass as "OK"
    # NOTE: do not call read_factory_settings() here (leaves bpy.context.view_layer stale); rely on --factory-startup
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll_name in ("meshes", "materials", "lights", "cameras", "curves"):
        coll = getattr(bpy.data, coll_name)
        for blk in list(coll):
            if blk.users == 0: coll.remove(blk)
    bpy.context.view_layer.update()
    log = io.StringIO()
    try:
        # Make the script dir importable & chdir so relative asset paths resolve
        sdir = os.path.dirname(os.path.abspath(args.script))
        sys.path.insert(0, sdir); os.chdir(sdir)
        with contextlib.redirect_stdout(log):
            runpy.run_path(args.script, run_name="__main__")
        # collect meshes (evaluate modifiers)
        bpy.context.view_layer.update()
        deps = bpy.context.evaluated_depsgraph_get()
        vl_objs = list(bpy.context.view_layer.objects)   # only objects actually in the scene/view layer count
        nm = nv = nf = 0
        for ob in vl_objs:
            if ob.type == "MESH":
                ev = ob.evaluated_get(deps)
                me = ev.to_mesh()
                if me is not None:
                    nm += 1; nv += len(me.vertices); nf += len(me.polygons)
                    ev.to_mesh_clear()
        rep.update(n_meshes=nm, n_verts=nv, n_faces=nf)
        if nm == 0 or nv == 0:
            rep["status"] = "EMPTY"; rep["error"] = "no mesh geometry produced"
        else:
            for ob in vl_objs:
                try: ob.select_set(ob.type == "MESH" and not ob.hide_viewport and not ob.hide_render)
                except Exception: pass
            bpy.ops.export_scene.gltf(filepath=args.out, export_format="GLB", use_selection=True,
                                      export_apply=True, export_materials="EXPORT", export_yup=True)
            rep["size_kb"] = round(os.path.getsize(args.out) / 1024, 1)
            rep["status"] = "OK"
    except BaseException as e:  # noqa  (catch SystemExit too)
        tb = traceback.format_exc()
        rep["error"] = (type(e).__name__ + ": " + str(e))[:2000]
        rep["traceback"] = tb[-3000:]
    rep["latency_s"] = round(time.time() - t0, 2)
    rep["stdout_tail"] = log.getvalue()[-1500:]
    with open(args.report, "w") as f:
        json.dump(rep, f, indent=2)
    print("RUNNER_STATUS", rep["status"], rep["error"] or "")

main()
