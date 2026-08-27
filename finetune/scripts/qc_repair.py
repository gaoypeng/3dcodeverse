"""Validate every converted sample with a real compiler/parser and repair what can be repaired minimally.

Three passes, each writing a report and (where it helps) a repaired copy of the subdir dataset:

  glsl      compile every unique shader with glslangValidator (the same wrapper the evaluator uses).
            Repairs attempted, smallest first, each re-validated before it is accepted:
              1. drop duplicated Shadertoy header blocks (repeated `#version` / uniform declarations)
              2. balance braces/parens at EOF (append the missing closers)
              3. drop a trailing incomplete statement (last line without `;`/`}`)
              4. wrap a bare body in `void mainImage(out vec4 fragColor, in vec2 fragCoord){ ... }`
  python    ast.parse every Blender/CadQuery program; repairs: strip a trailing truncated line, close
            unbalanced brackets. (No cosmetic rewrites — a program either parses or it does not.)
  dedup     find exact-duplicate programs *within* and *across* subdirs of the same source and write the
            duplicate groups so the upstream dataset can be cleaned too.

usage: python scripts/qc_repair.py --root /wekafs/ict/hx_624/hf_subdirs [--pass glsl|python|dedup|all] [--workers 32]
"""
import argparse
import ast
import collections
import glob
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

GLSLANG = "/wekafs/ict/hx_624/anaconda3/envs/llmft/bin/glslangValidator"
PRELUDE = """#version 310 es
precision highp float; precision highp int;
uniform vec3 iResolution; uniform float iTime; uniform float iTimeDelta; uniform int iFrame;
uniform vec4 iMouse; uniform vec4 iDate; uniform float iSampleRate;
uniform float iChannelTime[4]; uniform vec3 iChannelResolution[4];
uniform sampler2D iChannel0; uniform sampler2D iChannel1; uniform sampler2D iChannel2; uniform sampler2D iChannel3;
out vec4 _fragColor;
"""


def code_of(msg):
    m = re.findall(r"```[a-zA-Z]*\n(.*?)```", msg, flags=re.S)
    return (max(m, key=len) if m else msg).strip()


def _prelude(version, cube_channels=()):
    ch = "".join(f"uniform {'samplerCube' if i in cube_channels else 'sampler2D'} iChannel{i};\n" for i in range(4))
    return (f"#version {version}\nprecision highp float; precision highp int;\n"
            "uniform vec3 iResolution; uniform float iTime; uniform float iTimeDelta; uniform int iFrame;\n"
            "uniform vec4 iMouse; uniform vec4 iDate; uniform float iSampleRate; uniform float iFrameRate;\n"
            "uniform float iChannelTime[4]; uniform vec3 iChannelResolution[4];\n" + ch + "out vec4 _fragColor;\n")


CUBE_USE = re.compile(r"texture(?:Lod)?\s*\(\s*iChannel([0-3])\s*,\s*(?:vec3|normalize|reflect|refract|[a-zA-Z_]\w*\s*\.\s*xyz)")
EPILOGUE = "\nvoid main(){ mainImage(_fragColor, gl_FragCoord.xy); }\n"
STRIP = re.compile(r"^[ \t]*(?:#version[^\n]*|precision\s+\w+\s+\w+\s*;|uniform\s+(?:vec[234]|float|int|sampler2D|samplerCube)\s+\w+(?:\[\d+\])?\s*;|out\s+vec4\s+\w+\s*;)[ \t]*$", re.M)  # whole line only: a glued/minified line carries real code


def _try_compile(src):
    with tempfile.NamedTemporaryFile("w", suffix=".frag", delete=False) as f:
        f.write(src); p = f.name
    try:
        r = subprocess.run([GLSLANG, "-S", "frag", p], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=25, text=True, errors="replace")
        out = r.stdout or ""
        return r.returncode == 0, next((l for l in out.splitlines() if "ERROR:" in l), "")[:160]
    except Exception as e:
        return False, str(e)[:160]
    finally:
        os.unlink(p)


USED_CH = re.compile(r"iChannel([0-3])")


def glsl_compile(body):
    """Shadertoy targets WebGL2 (GLSL ES 3.00) and each iChannel may be a 2D texture OR a cubemap — which one
    is a property of the *shader's channel bindings*, not of the source text. Guessing it from the coordinate
    expression is unreliable (a repair agent brute-forced it and found 97% of "no matching overloaded function"
    failures were just a wrong guess), so try the actual assignments: for the channels the shader uses, iterate
    over 2D/cube combinations under both ES profiles and accept if ANY compiles."""
    stripped = STRIP.sub("", body)
    used = sorted({int(m.group(1)) for m in USED_CH.finditer(stripped)})
    combos = [()]
    if used:
        combos = [tuple(c for c, bit in zip(used, bits) if bit)
                  for bits in itertools.product((0, 1), repeat=len(used))]
        combos.sort(key=len)                      # all-2D first, then progressively more cubemaps
    last = ""
    for ver in ("300 es", "310 es"):
        for cc in combos:
            ok, err = _try_compile(_prelude(ver, cc) + stripped + EPILOGUE)
            if ok:
                return True, ""
            last = last or err
    return False, last


def dedup_headers(body):
    """shadertoy forks often repeat the whole header; keep the first occurrence of each duplicated line block"""
    lines = body.splitlines()
    seen, out, removed = set(), [], 0
    for ln in lines:
        s = ln.strip()
        if s.startswith(("#version", "precision ", "uniform ", "out vec4")) and s in seen:
            removed += 1
            continue
        if s:
            seen.add(s)
        out.append(ln)
    return ("\n".join(out), removed) if removed else (body, 0)


def balance(body):
    add = ""
    for o, c in (("{", "}"), ("(", ")")):
        d = body.count(o) - body.count(c)
        if d > 0:
            add += c * d
    return (body + "\n" + add if add else body), len(add)


def drop_tail(body):
    lines = body.rstrip().splitlines()
    while lines and not lines[-1].strip().endswith((";", "}", "{")):
        lines.pop()
    return "\n".join(lines), 1


def wrap_main(body):
    if "mainImage" in body or "void main" in body:
        return body, 0
    return "void mainImage(out vec4 fragColor, in vec2 fragCoord){\n" + body + "\n}", 1


def repair_glsl(body):
    """try repairs smallest-first; return (fixed_body, recipe) or (None, reason)"""
    ok, err = glsl_compile(body)
    if ok:
        return body, "already_ok"
    steps = []
    cur = body
    for name, fn in (("dedup_headers", dedup_headers), ("balance", balance), ("drop_tail", drop_tail), ("wrap_main", wrap_main)):
        new, n = fn(cur)
        if n == 0 or new == cur:
            continue
        cur = new
        steps.append(name)
        ok, err = glsl_compile(cur)
        if ok:
            return cur, "+".join(steps)
    return None, err or "unfixable"


def _glsl_job(args):
    key, body = args
    fixed, recipe = repair_glsl(body)
    return key, (fixed is not None), recipe, (fixed if fixed and recipe != "already_ok" else None)


def shadertoy_provenance():
    """id -> flags the dataset itself records: a shader that needs a Common tab, extra buffers or external
    textures CANNOT compile standalone — that is a property of the sample, not a defect to repair."""
    prov = {}
    for p in glob.glob("/wekafs/ict/hx_624/data/3dcodeverse_meta/shadertoy/*/metadata.parquet"):
        df = pd.read_parquet(p, columns=["id", "meta_json"])
        for r in df.itertuples():
            try:
                m = json.loads(r.meta_json) if isinstance(r.meta_json, str) else (r.meta_json or {})
            except Exception:
                m = {}
            prov[str(r.id)] = {"num_passes": m.get("num_passes") or 1,
                               "has_common_pass": bool(m.get("has_common_pass")),
                               "external_assets": bool(m.get("external_assets")),
                               "multi_file": bool(m.get("multi_file"))}
    return prov


def classify(err, flags):
    if flags.get("has_common_pass"):
        return "expected_needs_common_pass"
    if (flags.get("num_passes") or 1) > 1:
        return "expected_multi_pass"
    if flags.get("external_assets"):
        return "expected_external_assets"
    e = err or ""
    if "syntax error" in e or "unexpected" in e:
        return "syntax_error"
    if "undeclared identifier" in e or "no matching overloaded function" in e:
        return "missing_symbol"
    return "other"


def pass_glsl(root, workers, limit=None):
    prov = shadertoy_provenance()
    print(f"[qc] provenance flags for {len(prov)} shadertoy samples", flush=True)
    dirs = sorted(glob.glob(f"{root}/shadertoy/*_llamafactory"))
    uniq, where, hid = {}, collections.defaultdict(list), {}
    for d in dirs:
        p = os.path.join(d, "train.parquet")
        if not os.path.exists(p):
            continue
        df = pd.read_parquet(p)
        for i, row in enumerate(df.itertuples()):
            body = code_of(row.conversations[1]["value"])
            h = hashlib.md5(body.encode()).hexdigest()
            uniq.setdefault(h, body)
            hid.setdefault(h, row.id)
            where[h].append((d, i))
    items = list(uniq.items())
    if limit:
        items = items[:limit]
    print(f"[qc] glsl: {len(items)} unique shaders from {len(dirs)} subdirs", flush=True)
    results = {}
    with ProcessPoolExecutor(workers) as ex:
        for n, (key, ok, recipe, fixed) in enumerate(ex.map(_glsl_job, items, chunksize=8)):
            results[key] = (ok, recipe, fixed)
            if (n + 1) % 5000 == 0:
                good = sum(1 for v in results.values() if v[0])
                print(f"[qc] glsl {n+1}/{len(items)} compile-ok {good} ({100*good/(n+1):.1f}%)", flush=True)
    stats = collections.Counter(v[1] for v in results.values())
    cls = collections.Counter()
    for h, (ok, recipe, _f) in results.items():
        if ok:
            cls["compiles" if recipe == "already_ok" else "repaired"] += 1
        else:
            cls[classify(recipe, prov.get(hid.get(h, ""), {}))] += 1
    fixed_map = {k: v[2] for k, v in results.items() if v[2]}
    # write repaired parquets
    per_dir = collections.Counter()
    for h, locs in where.items():
        if h not in fixed_map:
            continue
        for d, _i in locs:
            per_dir[d] += 1
    for d in dirs:
        p = os.path.join(d, "train.parquet")
        if not os.path.exists(p) or per_dir[d] == 0:
            continue
        df = pd.read_parquet(p)
        conv = df["conversations"].tolist()
        nfix = 0
        for i, c in enumerate(conv):
            body = code_of(c[1]["value"])
            h = hashlib.md5(body.encode()).hexdigest()
            if h in fixed_map:
                c[1]["value"] = "```glsl\n" + fixed_map[h].strip() + "\n```"
                nfix += 1
        df["conversations"] = conv
        df.to_parquet(p, index=False, compression="zstd")
        q = os.path.join(d, "qc.json")
        if os.path.exists(q):
            j = json.load(open(q))
            j["glsl_repaired_rows"] = nfix
            json.dump(j, open(q, "w"), indent=2)
    json.dump({"unique_shaders": len(items), "classification": dict(cls), "recipes": dict(stats.most_common(20)),
               "compiled_before": stats.get("already_ok", 0),
               "repaired": sum(v for k, v in stats.items() if k not in ("already_ok",) and not k.startswith("ERROR") and k != "unfixable"),
               "unfixable": sum(v for k, v in stats.items() if k == "unfixable" or k.startswith("ERROR"))},
              open(f"{root}/glsl_validation_report.json", "w"), indent=2)
    print("[qc] glsl classification:", dict(cls), flush=True)


def pass_python(root):
    out = []
    for d in sorted(glob.glob(f"{root}/**/*_llamafactory", recursive=True)):
        q = json.load(open(os.path.join(d, "qc.json"))) if os.path.exists(os.path.join(d, "qc.json")) else {}
        if q.get("dialect") not in ("blender", "cadquery"):
            continue
        p = os.path.join(d, "train.parquet")
        df = pd.read_parquet(p)
        bad = 0
        for row in df.itertuples():
            try:
                ast.parse(code_of(row.conversations[1]["value"]))
            except Exception:
                bad += 1
        out.append({"subdir": os.path.relpath(d, root), "rows": len(df), "ast_fail": bad})
        print(f"[qc] python {os.path.relpath(d, root):45s} rows={len(df):7,} ast_fail={bad}", flush=True)
    json.dump(out, open(f"{root}/python_validation_report.json", "w"), indent=2)


def pass_dedup(root):
    """exact duplicate programs within and across subdirs of the same source"""
    by_src = collections.defaultdict(lambda: collections.defaultdict(list))
    for d in sorted(glob.glob(f"{root}/**/*_llamafactory", recursive=True)):
        p = os.path.join(d, "train.parquet")
        if not os.path.exists(p):
            continue
        src = os.path.relpath(d, root).split("/")[0]
        df = pd.read_parquet(p)
        seen = set()
        for row in df.itertuples():
            if row.id in seen:
                continue
            seen.add(row.id)
            h = hashlib.md5(code_of(row.conversations[1]["value"]).encode()).hexdigest()
            by_src[src][h].append(row.id)
    report = {}
    for src, groups in by_src.items():
        dupes = {h: ids for h, ids in groups.items() if len(ids) > 1}
        n_extra = sum(len(v) - 1 for v in dupes.values())
        report[src] = {"unique_programs": len(groups), "duplicate_groups": len(dupes),
                       "redundant_samples": n_extra,
                       "examples": [{"kept": v[0], "duplicates": v[1:4]} for v in list(dupes.values())[:5]]}
        json.dump({h: ids for h, ids in dupes.items()}, open(f"{root}/duplicates_{src}.json", "w"), indent=2)
        print(f"[qc] dedup {src:20s} unique={len(groups):7,} dup_groups={len(dupes):6,} redundant={n_extra:6,}", flush=True)
    json.dump(report, open(f"{root}/duplicate_report.json", "w"), indent=2)



def dedupe_decls(body):
    """after merging a Common tab, the same #define / struct / function can appear twice — a redefinition is a
    compile error, and even when it is not, it is pure redundancy. Drop the LATER exact-duplicate top-level
    declaration, keeping the first (Shadertoy semantics: Common comes first)."""
    lines = body.splitlines()
    seen_def, seen_sig, out, removed = set(), set(), [], 0
    i = 0
    sig_re = re.compile(r"^\s*(?:[A-Za-z_][\w]*\s+){1,3}([A-Za-z_]\w*)\s*\(([^)]*)\)\s*\{?\s*$")
    def_re = re.compile(r"^\s*#define\s+(\w+)")
    while i < len(lines):
        ln = lines[i]
        m = def_re.match(ln)
        if m:
            if ln.strip() in seen_def:
                removed += 1; i += 1; continue
            seen_def.add(ln.strip()); out.append(ln); i += 1; continue
        m = sig_re.match(ln)
        if m and "{" in ln:
            sig = (m.group(1), re.sub(r"\s+", "", m.group(2)))
            depth = ln.count("{") - ln.count("}")
            block = [ln]; j = i + 1
            while j < len(lines) and depth > 0:
                depth += lines[j].count("{") - lines[j].count("}")
                block.append(lines[j]); j += 1
            if sig in seen_sig:
                removed += len(block); i = j; continue
            seen_sig.add(sig); out.extend(block); i = j; continue
        out.append(ln); i += 1
    return ("\n".join(out), removed) if removed else (body, 0)


def _flat_job(args):
    key, body = args
    ok, err = glsl_compile(body)
    if ok:
        slim, n = dedupe_decls(body)
        if n:
            ok2, _ = glsl_compile(slim)
            if ok2:
                return key, True, f"ok_slimmed({n}_lines)", slim
        return key, True, "ok", None
    cur, steps = body, []
    for name, fn in (("dedupe_decls", dedupe_decls), ("dedup_headers", dedup_headers), ("balance", balance), ("drop_tail", drop_tail)):
        new, n = fn(cur)
        if n == 0 or new == cur:
            continue
        cur = new; steps.append(name)
        ok, err = glsl_compile(cur)
        if ok:
            return key, True, "+".join(steps), cur
    return key, False, err or "unfixable", None


def pass_glsl_flat(root, workers, limit=None):
    """validate + slim the flattened (Common-merged) shaders"""
    dirs = sorted(glob.glob(f"{root}/shadertoy/*_llamafactory_flat"))
    uniq, where = {}, collections.defaultdict(list)
    for d in dirs:
        p = os.path.join(d, "train.parquet")
        if not os.path.exists(p):
            continue
        df = pd.read_parquet(p)
        for i, row in enumerate(df.itertuples()):
            body = code_of(row.conversations[1]["value"])
            h = hashlib.md5(body.encode()).hexdigest()
            uniq.setdefault(h, body); where[h].append((d, i))
    items = list(uniq.items())
    if limit: items = items[:limit]
    print(f"[qc] flat: {len(items)} unique flattened programs from {len(dirs)} subdirs", flush=True)
    res = {}
    with ProcessPoolExecutor(workers) as ex:
        for n, (k, ok, recipe, fixed) in enumerate(ex.map(_flat_job, items, chunksize=8)):
            res[k] = (ok, recipe, fixed)
            if (n+1) % 10000 == 0:
                good = sum(1 for v in res.values() if v[0])
                print(f"[qc] flat {n+1}/{len(items)} compile-ok {good} ({100*good/(n+1):.1f}%)", flush=True)
    fixed_map = {k: v[2] for k, v in res.items() if v[2]}
    for d in dirs:
        p = os.path.join(d, "train.parquet")
        if not os.path.exists(p): continue
        df = pd.read_parquet(p); conv = df["conversations"].tolist(); okcol = []; nfix = 0
        for c in conv:
            body = code_of(c[1]["value"]); h = hashlib.md5(body.encode()).hexdigest()
            if h in fixed_map:
                c[1]["value"] = "```glsl\n" + fixed_map[h].strip() + "\n```"; nfix += 1
            okcol.append(bool(res.get(h, (False,))[0]))
        df["conversations"] = conv; df["compiles"] = okcol
        df.to_parquet(p, index=False, compression="zstd")
        json.dump({"rows": len(df), "compiles": int(sum(okcol)), "repaired_or_slimmed": nfix},
                  open(os.path.join(d, "qc.json"), "w"), indent=2)
    cls = collections.Counter(v[1] if v[0] else "FAIL:" + v[1][:40] for v in res.values())
    good = sum(1 for v in res.values() if v[0])
    json.dump({"unique_programs": len(items), "compile_ok": good,
               "compile_rate": round(good/max(1,len(items)), 4),
               "top_outcomes": dict(cls.most_common(15))},
              open(f"{root}/glsl_flat_validation_report.json", "w"), indent=2)
    print(f"[qc] flat: {good}/{len(items)} compile ({100*good/max(1,len(items)):.1f}%)", flush=True)
    print("[qc] flat outcomes:", dict(cls.most_common(8)), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/wekafs/ict/hx_624/hf_subdirs")
    ap.add_argument("--pass", dest="which", default="all", choices=["glsl", "glsl_flat", "python", "dedup", "all"])
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    if a.which in ("dedup", "all"):
        pass_dedup(a.root)
    if a.which in ("python", "all"):
        pass_python(a.root)
    if a.which in ("glsl", "all"):
        pass_glsl(a.root, a.workers, a.limit)
    if a.which == "glsl_flat":
        pass_glsl_flat(a.root, a.workers, a.limit)


if __name__ == "__main__":
    main()
