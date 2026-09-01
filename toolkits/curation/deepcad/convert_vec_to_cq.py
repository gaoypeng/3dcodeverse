"""
convert_vec_to_cq.py
--------------------
Convert a DeepCAD vectorized command sequence (the .h5 `cad_vec` form) into a
standalone, executable CadQuery Python script.

This reuses the *validated* numeric conversion logic from GenCAD-Code
(scripts/h5tocadquery.py + geom_utils.py + deepcad_constants.py), which correctly
handles two-sided / symmetric extrudes, arc sweep geometry, and the DeepCAD
normalization scale. We only re-wrap it so that:
  * it returns the generated code as a string (no global side effects),
  * the heavy matplotlib/pandas imports of the original __main__ are avoided,
  * the produced script exports a STEP file to sys.argv[1].

The pure geometry helpers (CoordSystem, get_arc, polar_*) are imported unchanged
from the cloned GenCAD-Code repo.
"""
import os
import sys
import numpy as np

# --- locate the cloned GenCAD-Code scripts (pure helpers only) ---
GENCAD_SCRIPTS = os.environ.get(
    "GENCAD_SCRIPTS", "/data/ziyao/deepcad2cq/GenCAD-Code/scripts"
)
if GENCAD_SCRIPTS not in sys.path:
    sys.path.insert(0, GENCAD_SCRIPTS)

# These two modules are pure (numpy/math only) -- safe to import directly.
from deepcad_constants import (  # noqa: E402
    LINE, ARC, CIRCLE, EOS, SOL, EXTRUDE,
    EXTRUDE_OPERATIONS, EXTENT_TYPE, NORM_FACTOR,
)
from geom_utils import CoordSystem, get_arc  # noqa: E402

import h5py  # noqa: E402

UNQUANTIZE = True


# ---------------------------------------------------------------------------
# The conversion logic below is adapted faithfully from GenCAD-Code's
# convert_h5_to_cadquery(); it builds the body of the CadQuery program as a
# string instead of writing it to disk. Variable `solid` holds the final result.
# ---------------------------------------------------------------------------
def vec_to_cadquery_code(vecs, truncate=6, use_fixed_decimal=True):
    """Return a CadQuery python program (string) for the given vec array.

    Raises TypeError / NotImplementedError on sequences DeepCAD/GenCAD cannot
    represent (zero extrude, malformed two-sided, unsupported curve combos).
    """

    def split_by_sketches(arr):
        split_indices = np.where(arr[:, 0] == EXTRUDE)[0] + 1
        split_arrays = np.split(arr, split_indices)[:-1]
        sketches = [a[:-1] for a in split_arrays]
        extrudes = [a[-1:] for a in split_arrays]
        return sketches, extrudes

    def split_by_loops(arr):
        split_indices = np.where(arr[:, 0] == SOL)[0]
        split_arrays = np.split(arr, split_indices)[1:]
        loops = [a[1:] for a in split_arrays]
        return loops

    def extract_extrude_params(extrusion_command, unquantize):
        c = extrusion_command[0]
        theta, phi, gamma = c[6], c[7], c[8]
        px, py, pz = c[9], c[10], c[11]
        scale = c[12]
        dir1, dir2 = c[13], c[14]
        op = int(c[15])
        typ = int(c[16])
        if unquantize:
            dir1 = dir1 / 256 * 2 - 1.0
            dir2 = dir2 / 256 * 2 - 1.0
            scale = scale / 256 * 2
        return theta, phi, gamma, px, py, pz, scale, dir1, dir2, op, typ

    def fmt(v):
        return f"{v:.{truncate}f}" if use_fixed_decimal else f"{v}"

    def cadquery_workplane(sp, sketch_num):
        cmt = f"# workplane for sketch {sketch_num}\n"
        cmd = (
            f"wp_sketch{sketch_num} = cq.Workplane(cq.Plane("
            f"cq.Vector({fmt(sp.origin[0])}, {fmt(sp.origin[1])}, {fmt(sp.origin[2])}), "
            f"cq.Vector({fmt(sp.x_axis[0])}, {fmt(sp.x_axis[1])}, {fmt(sp.x_axis[2])}), "
            f"cq.Vector({fmt(sp.normal[0])}, {fmt(sp.normal[1])}, {fmt(sp.normal[2])})))\n"
        )
        return cmt + cmd

    def sk_scale(extrude_scale):
        return extrude_scale / (256 / 2 * NORM_FACTOR - 1)

    def cadquery_line(x, y, cx, cy, loop_list, scale):
        if (x == cx) and (y == cy):
            return ""
        s = sk_scale(scale)
        t = -(256 / 2)
        x = (x + t) * s
        y = (y + t) * s
        if len(loop_list) == 0:
            return f".moveTo({fmt(x)}, {fmt(y)})"
        return f".lineTo({fmt(x)}, {fmt(y)})"

    def cadquery_arc(x, y, cx, cy, sweep, dir_flag, loop_list, scale):
        arc_out = get_arc(x, y, cx, cy, sweep, dir_flag, is_numerical=True)
        if arc_out is None:
            return ""
        start_point, mid_point, end_point = arc_out
        s = sk_scale(scale)
        t = -(256 / 2)
        mx = (mid_point[0] + t) * s
        my = (mid_point[1] + t) * s
        ex = (end_point[0] + t) * s
        ey = (end_point[1] + t) * s
        ccx = (cx + t) * s
        ccy = (cy + t) * s
        if len(loop_list) == 0:
            return (f".moveTo({fmt(ccx)}, {fmt(ccy)})"
                    f".threePointArc(({fmt(mx)}, {fmt(my)}), ({fmt(ex)}, {fmt(ey)}))")
        return f".threePointArc(({fmt(mx)}, {fmt(my)}), ({fmt(ex)}, {fmt(ey)}))"

    def cadquery_circle(x, y, r, loop_list, scale):
        s = sk_scale(scale)
        t = -(256 / 2)
        x = (x + t) * s
        y = (y + t) * s
        r = r * s
        if len(loop_list) == 0:
            return f".moveTo({fmt(x)}, {fmt(y)}).circle({fmt(r)})"
        raise NotImplementedError("Circle with other things in loop")

    def cadquery_close_loop(loop_ops, loop_num, sketch_num):
        if len(loop_ops) > 1:
            return f"loop{loop_num}=wp_sketch{sketch_num}" + "".join(loop_ops) + ".close()\n"
        if "circle" in loop_ops[0]:
            return f"loop{loop_num}=wp_sketch{sketch_num}" + loop_ops[0] + "\n"
        if "Arc" in loop_ops[0]:
            return f"loop{loop_num}=wp_sketch{sketch_num}" + "".join(loop_ops) + ".close()\n"
        if len(loop_ops) == 1 and loop_ops[0] == "":
            raise TypeError("Empty Loop")
        raise NotImplementedError(f"1 loop something else:{loop_ops}")

    def join_loops(loops):
        return "".join(f".add(loop{l})" for l in loops)

    def cadquery_extrude(loops_joined, op, typ, dir1, dir2, sketch_num,
                         repeat_sketch, repeat_loops):
        OS = EXTENT_TYPE.index("OneSideFeatureExtentType")
        SYM = EXTENT_TYPE.index("SymmetricFeatureExtentType")
        NEW = EXTRUDE_OPERATIONS.index("NewBodyFeatureOperation")
        JOIN = EXTRUDE_OPERATIONS.index("JoinFeatureOperation")
        CUT = EXTRUDE_OPERATIONS.index("CutFeatureOperation")

        def two_sided_dirs():
            if dir2 == 0:
                return None
            return dir1, -dir2

        if sketch_num == 0:
            if op in (NEW, JOIN, CUT, EXTRUDE_OPERATIONS.index("IntersectFeatureOperation")):
                if typ == OS:
                    return (f"solid{sketch_num}=wp_sketch{sketch_num}{loops_joined}"
                            f".extrude({fmt(dir1)})\nsolid=solid{sketch_num}\n")
                if typ == SYM:
                    return (f"solid{sketch_num}=wp_sketch{sketch_num}{loops_joined}"
                            f".extrude({fmt(dir1)}, both=True)\nsolid=solid{sketch_num}\n")
                td = two_sided_dirs()
                if td is None:
                    return cadquery_extrude(loops_joined, op, OS, dir1, dir2,
                                            sketch_num, repeat_sketch, repeat_loops)
                dwn, dan = td
                return (
                    f"solid{sketch_num}=wp_sketch{sketch_num}{loops_joined}.extrude({fmt(dwn)})\n"
                    f"solid=solid{sketch_num}\n"
                    + repeat_sketch.split("\n")[1] + "\n" + repeat_loops
                    + f"solid{sketch_num}=wp_sketch{sketch_num}{loops_joined}.extrude({fmt(dan)})\n"
                    f"solid=solid.union(solid{sketch_num})\n"
                )
            raise NotImplementedError("first body not newbody or join")

        # non-first bodies
        if op in (NEW, JOIN):
            combiner = "solid=solid.union(solid{n})\n"
        elif op == CUT:
            combiner = "solid=solid.cut(solid{n})\n"
        else:
            combiner = "solid=solid.intersect(solid{n})\n"

        if typ == OS:
            return (f"solid{sketch_num}=wp_sketch{sketch_num}{loops_joined}.extrude({fmt(dir1)})\n"
                    + combiner.format(n=sketch_num))
        if typ == SYM:
            return (f"solid{sketch_num}=wp_sketch{sketch_num}{loops_joined}.extrude({fmt(dir1)}, both=True)\n"
                    + combiner.format(n=sketch_num))
        td = two_sided_dirs()
        if td is None:
            return cadquery_extrude(loops_joined, op, OS, dir1, dir2,
                                    sketch_num, repeat_sketch, repeat_loops)
        dwn, dan = td
        join_or = "union" if op in (NEW, JOIN) else ("cut" if op == CUT else "intersect")
        return (
            f"solid{sketch_num}=wp_sketch{sketch_num}{loops_joined}.extrude({fmt(dwn)})\n"
            f"solid_temp=solid{sketch_num}\n"
            + repeat_sketch.split("\n")[1] + "\n" + repeat_loops
            + f"solid{sketch_num}=wp_sketch{sketch_num}{loops_joined}.extrude({fmt(dan)})\n"
            f"solid_temp=solid_temp.union(solid{sketch_num})\n"
            f"solid=solid.{join_or}(solid_temp)\n"
        )

    # ---- main assembly ----
    code = "import cadquery as cq\n"
    sketches, extrudes = split_by_sketches(vecs)

    total_loop_count = 0
    curr_x, curr_y = 128, 128

    for i, sketch in enumerate(sketches):
        extrude = extrudes[i]
        theta, phi, gamma, px, py, pz, scale, dir1, dir2, op, typ = \
            extract_extrude_params(extrude, UNQUANTIZE)

        if dir1 == 0:
            raise TypeError("Extrude is zero for dir1")
        if dir2 == 0 and typ == EXTENT_TYPE.index("TwoSidesFeatureExtentType"):
            raise TypeError("Two sided extrude with no second extrude value")

        sketch_plane = CoordSystem(np.array([px, py, pz]), theta, phi, gamma)
        sketch_plane.denumericalize(256)

        wp_code = cadquery_workplane(sketch_plane, i)
        code += wp_code

        loops = split_by_loops(sketch)
        loops_in_sketch = []
        loop_chunk = ""

        for loop in loops:
            loop_ops = []
            for sketch_op in loop:
                if sketch_op[0] == LINE:
                    px2, py2 = sketch_op[1], sketch_op[2]
                    loop_ops.append(cadquery_line(px2, py2, curr_x, curr_y, loop_ops, scale))
                    curr_x, curr_y = px2, py2
                elif sketch_op[0] == ARC:
                    ex, ey = sketch_op[1], sketch_op[2]
                    loop_ops.append(cadquery_arc(ex, ey, curr_x, curr_y,
                                                 sketch_op[3], sketch_op[4], loop_ops, scale))
                    curr_x, curr_y = ex, ey
                elif sketch_op[0] == CIRCLE:
                    cxx, cyy, r = sketch_op[1], sketch_op[2], sketch_op[5]
                    loop_ops.append(cadquery_circle(cxx, cyy, r, loop_ops, scale))
                else:
                    raise NotImplementedError("Command not implemented")

            close_cmd = cadquery_close_loop(loop_ops, total_loop_count, i)
            code += close_cmd
            loop_chunk += close_cmd
            loops_in_sketch.append(total_loop_count)
            total_loop_count += 1

        loops_joined = join_loops(loops_in_sketch)
        code += cadquery_extrude(loops_joined, op, typ, dir1, dir2, i, wp_code, loop_chunk)

    code += (
        "\nif __name__ == '__main__':\n"
        "    import sys\n"
        "    out = sys.argv[1] if len(sys.argv) > 1 else '/tmp/out.step'\n"
        "    cq.exporters.export(solid, out)\n"
    )
    return code


def extract_h5(path):
    with h5py.File(path, "r") as f:
        key = list(f.keys())[0]
        return f[key][()]


if __name__ == "__main__":
    src = sys.argv[1]        # path to .h5
    dest = sys.argv[2]       # path to output .py
    vec = extract_h5(src)
    code = vec_to_cadquery_code(vec)
    with open(dest, "w") as f:
        f.write(code)
    print(f"wrote {dest}")
