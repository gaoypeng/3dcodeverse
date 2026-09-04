"""Aggregate every suite of one eval_all run (or compare several runs) into a table.

  python eval/results_all.py <name> [<name2> ...]        -> markdown table + eval/out/<name>_report.json
"""
import json
import os
import sys

SUITES = [("bench", "3DCodeBench (212)"), ("blender", "Blender held-out (103)"), ("cadquery", "CadQuery (200)"),
          ("openscad", "OpenSCAD (50)"), ("glsl", "GLSL (200)"), ("threejs", "three.js (40)")]
ROOT = os.environ.get("EVAL_OUT", "/wekafs/ict/hx_624/llm-ft/eval/out")


def suite_row(name, suite):
    d = os.path.join(ROOT, f"{name}_{suite}")
    s_path, x_path = os.path.join(d, "summary.json"), os.path.join(d, "extract_stats.json")
    if not os.path.exists(s_path):
        return None
    s = json.load(open(s_path))
    x = json.load(open(x_path)) if os.path.exists(x_path) else {}
    if suite == "bench":
        rate, f05, f01 = s.get("exec_ok_rate"), s.get("f@0.05_mean_all(fail=0)"), s.get("f@0.1_mean_scored")
        n_ok, n = s.get("exec_ok"), s.get("n_tasks")
    elif suite == "threejs":
        rate, f05, f01 = s.get("exec_rate"), None, None
        n_ok, n = s.get("exec_ok"), s.get("n")
    else:
        rate, f05, f01 = s.get("gen_exec_rate"), s.get("f@0.05_mean_all"), s.get("f@0.1_mean_scored")
        n_ok, n = s.get("gen_exec_ok"), s.get("n")
    return {"suite": suite, "n": n, "exec_ok": n_ok, "exec_rate": rate, "f@0.05_all": f05, "f@0.1_scored": f01,
            "mean_new_tokens": x.get("mean_new_tokens"), "with_think": x.get("with_think"), "no_fence": x.get("no_fence"),
            "multi_block": x.get("multi_block"), "chosen_not_last": x.get("chosen_not_last"),
            "syntax_valid": x.get("syntax_valid"), "no_dialect_marker": x.get("no_dialect_marker"),
            "from_think_fallback": x.get("from_think_fallback")}


def main(names):
    print("| model | suite | exec | rate | F@0.05(all) | F@0.1(ok) | out tok | think | no-fence | multi-blk | not-last | no-marker |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name in names:
        rows = []
        for suite, _label in SUITES:
            r = suite_row(name, suite)
            if not r:
                continue
            rows.append(r)
            pct = f"{100*r['exec_rate']:.1f}%" if r["exec_rate"] is not None else "–"
            f05 = f"{r['f@0.05_all']:.3f}" if r["f@0.05_all"] is not None else "–"
            f01 = f"{r['f@0.1_scored']:.3f}" if r["f@0.1_scored"] is not None else "–"
            print(f"| {name} | {suite} | {r['exec_ok']}/{r['n']} | {pct} | {f05} | {f01} | "
                  f"{r['mean_new_tokens'] or '–'} | {r['with_think'] if r['with_think'] is not None else '–'} | "
                  f"{r['no_fence'] if r['no_fence'] is not None else '–'} | {r['multi_block'] if r['multi_block'] is not None else '–'} | "
                  f"{r['chosen_not_last'] if r['chosen_not_last'] is not None else '–'} | {r['no_dialect_marker'] if r['no_dialect_marker'] is not None else '–'} |")
        if rows:
            json.dump({"name": name, "suites": rows}, open(os.path.join(ROOT, f"{name}_report.json"), "w"), indent=2)


if __name__ == "__main__":
    main(sys.argv[1:] or ["md_xl"])
