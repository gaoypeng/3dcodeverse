"""Dialect-aware code extraction from free-form model output.

Copied from finetune/eval/extract.py (2026-09-08) with one fix: the shell-prompt penalty no longer
fires on a leading `# comment` line.  Behaviour is otherwise identical, so numbers stay comparable.

Why this exists: instruct/reasoning models do not answer with bare code. A single response may contain
a <think> block (often with *draft* code), prose, several fenced blocks (setup shell commands, a JSON
snippet, the real program, then a "here's a variant" block), or no fence at all. Taking "the longest
fenced block" (the old rule) picks the draft or the variant often enough to matter.

extract(text, dialect) -> (code, meta)
  * strips reasoning blocks (<think>…</think>, <thinking>…</thinking>, <reasoning>…</reasoning>);
    if the model never closed the block the answer is considered truncated-in-thought and we fall back
    to mining the thought itself (meta["from_think"]=True) so we can still score something.
  * collects candidates: every fenced block (closed or trailing-unterminated), plus the whole visible
    text as a last resort.
  * scores each candidate by: dialect markers (import bpy / cq. / mainImage / THREE. / module …),
    language tag on the fence, syntax validity (ast.parse for python dialects, structural checks
    otherwise), and penalises obvious non-answers (shell commands, pip installs, json, error logs).
  * ties are broken toward the LAST high-scoring block (models put the final program last), then length.

meta keys: n_blocks, chosen, score, lang, had_think, think_chars, from_think, no_fence, valid, markers.
"""
import ast
import json
import re

THINK_RE = re.compile(r"<(think|thinking|reasoning)>(.*?)</\1>", re.S | re.I)
THINK_OPEN_RE = re.compile(r"<(think|thinking|reasoning)>(.*)$", re.S | re.I)
FENCE_RE = re.compile(r"```([A-Za-z0-9_+.#-]*)[ \t]*\r?\n(.*?)```", re.S)
FENCE_OPEN_RE = re.compile(r"```([A-Za-z0-9_+.#-]*)[ \t]*\r?\n(.*)$", re.S)

# per-dialect: fence language tags, regexes that prove the block is that dialect, and how to syntax-check
DIALECTS = {
    "blender": {
        "langs": {"python", "py", "python3", "blender", "bpy"},
        "markers": [r"^\s*import\s+bpy\b", r"\bbpy\.(ops|data|context)\b"],
        "check": "python",
    },
    "cadquery": {
        "langs": {"python", "py", "python3", "cadquery", "cq"},
        "markers": [r"^\s*import\s+cadquery\b", r"^\s*from\s+cadquery\b", r"\bcq\.Workplane\b", r"\bcadquery\.Workplane\b"],
        "check": "python",
    },
    "openscad": {
        "langs": {"openscad", "scad"},
        "markers": [r"^\s*module\s+\w+\s*\(", r"\b(cube|cylinder|sphere|polyhedron|linear_extrude|rotate_extrude|hull|minkowski)\s*\("],
        "check": "scad",
    },
    "glsl": {
        "langs": {"glsl", "frag", "fragment", "fs", "c", "cpp"},
        "markers": [r"void\s+mainImage\s*\(", r"\bgl_FragColor\b", r"^\s*#version\b", r"\bfragColor\b"],
        "check": "glsl",
    },
    "threejs": {
        "langs": {"html", "htm", "javascript", "js", "jsx"},
        "markers": [r"<!DOCTYPE\s+html", r"<html", r"\bnew\s+THREE\.", r"\bTHREE\.[A-Z]\w+"],
        "check": "html",
    },
    # generic python (used by "auto" when nothing dialect-specific matches)
    "python": {"langs": {"python", "py", "python3"}, "markers": [r"^\s*(import|from|def|class)\s+\w"], "check": "python"},
}

# blocks that are never the answer
NEGATIVE = [
    (re.compile(r"^\s*(pip|conda|apt|sudo|blender|python)\s+(install|-m|-b)\b", re.M), 4),
    (re.compile(r"^\s*[$>]\s+\w"), 3),                       # shell prompt ("#" excluded: it is a comment in every dialect we score)
    (re.compile(r"^\s*\{[\s\S]*\}\s*$"), 2),                  # bare JSON
    (re.compile(r"^\s*(Traceback|Error:|error:)", re.M), 3),  # pasted error log
]


def _syntax_ok(code, kind):
    if kind == "python":
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False
        except Exception:
            return False
    if kind == "scad":
        return code.count("{") == code.count("}") and code.count("(") == code.count(")") and bool(re.search(r"[;{]", code))
    if kind == "glsl":
        return code.count("{") == code.count("}") and "void" in code
    if kind == "html":
        return ("<" in code and ">" in code) or "THREE" in code
    return True


def _candidates(text):
    """[(lang, code, start_index)] for every fenced block, plus a trailing unterminated one."""
    out, last_end = [], 0
    for m in FENCE_RE.finditer(text):
        out.append((m.group(1).lower(), m.group(2), m.start()))
        last_end = m.end()
    tail = text[last_end:]
    m = FENCE_OPEN_RE.search(tail)
    if m and m.group(2).strip():
        out.append((m.group(1).lower(), m.group(2), last_end + m.start()))
    return out


def _first_code_line(text):
    """No fence anywhere: drop the prose preamble, keep from the first code-looking line on."""
    pat = re.compile(r"^\s*(import\s+\w|from\s+\w|def\s+\w|class\s+\w|#\s*!|#version|module\s+\w|void\s+\w|<!DOCTYPE|<html|const\s+\w|let\s+\w|var\s+\w|function\s+\w|\$fn\s*=)")
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if pat.match(ln):
            return "\n".join(lines[i:])
    return text


def _score(lang, code, spec):
    s, markers = 0.0, 0
    for pat in spec["markers"]:
        if re.search(pat, code, re.M):
            markers += 1
    s += 3.0 * min(markers, 2)
    if lang in spec["langs"]:
        s += 2.0
    elif lang == "":
        s += 0.5
    valid = _syntax_ok(code, spec["check"])
    if valid:
        s += 2.0
    for pat, pen in NEGATIVE:
        if pat.search(code):
            s -= pen
    if len(code.strip()) < 40:
        s -= 2.0
    return s, valid, markers


def extract(text, dialect="auto"):
    meta = {"n_blocks": 0, "chosen": None, "score": None, "lang": None, "had_think": False,
            "think_chars": 0, "from_think": False, "no_fence": False, "valid": False, "markers": 0,
            "dialect": dialect}
    if not text:
        return "", meta

    # 1) reasoning blocks
    thinks = THINK_RE.findall(text)
    visible = THINK_RE.sub("", text)
    if thinks:
        meta["had_think"] = True
        meta["think_chars"] = sum(len(t[1]) for t in thinks)
    m = THINK_OPEN_RE.search(visible)
    if m:  # unterminated <think>: everything after it is thought
        meta["had_think"] = True
        meta["think_chars"] += len(m.group(2))
        visible = visible[: m.start()]

    def _pick(src):
        cands = _candidates(src)
        if not cands:
            return None, None
        specs = [DIALECTS[dialect]] if dialect in DIALECTS else list(DIALECTS.values())
        best = None
        for i, (lang, code, _pos) in enumerate(cands):
            sc = max((_score(lang, code, sp) for sp in specs), key=lambda x: x[0])
            key = (round(sc[0], 3), i, len(code))  # score, then LAST block, then length
            if best is None or key > best[0]:
                best = (key, i, lang, code, sc)
        return cands, best

    cands, best = _pick(visible)
    if best is None and visible.strip():          # no fence in the visible answer
        code = _first_code_line(visible)
        specs = [DIALECTS[dialect]] if dialect in DIALECTS else list(DIALECTS.values())
        sc = max((_score("", code, sp) for sp in specs), key=lambda x: x[0])
        meta.update(n_blocks=0, no_fence=True, chosen=-1, score=round(sc[0], 2), lang="", valid=sc[1], markers=sc[2])
        return code.strip("\n"), meta
    if best is None:                               # nothing visible: mine the thought
        cands, best = _pick(text)
        meta["from_think"] = True
        if best is None:
            return "", meta

    meta.update(n_blocks=len(cands), chosen=best[1], score=round(best[4][0], 2), lang=best[2],
                valid=best[4][1], markers=best[4][2])
    return best[3].strip("\n"), meta


def extract_code(text, dialect="auto"):
    """Backwards-compatible single-value API (old callers import this name from generate.py)."""
    return extract(text, dialect)[0]


def summarize(metas):
    """Aggregate extraction metadata over a run -> dict for extract_stats.json."""
    n = max(1, len(metas))
    return {
        "n": len(metas),
        "with_think": sum(m.get("had_think", False) for m in metas),
        "mean_think_chars": round(sum(m.get("think_chars", 0) for m in metas) / n, 1),
        "no_fence": sum(m.get("no_fence", False) for m in metas),
        "from_think_fallback": sum(m.get("from_think", False) for m in metas),
        "multi_block": sum((m.get("n_blocks") or 0) > 1 for m in metas),
        "mean_blocks": round(sum(m.get("n_blocks") or 0 for m in metas) / n, 2),
        "chosen_not_last": sum(m.get("chosen") is not None and m.get("n_blocks") and m["chosen"] != m["n_blocks"] - 1 for m in metas),
        "syntax_valid": sum(m.get("valid", False) for m in metas),
        "no_dialect_marker": sum((m.get("markers") or 0) == 0 for m in metas),
        "empty": sum((m.get("score") is None) for m in metas),
    }


if __name__ == "__main__":  # tiny self-test: python eval/extract.py
    T = [
        ("<think>let me draft\n```python\nimport bpy\nbpy.ops.mesh.primitive_cube_add()  # draft\n```\nno, better</think>\n"
         "Here is the script:\n```python\nimport bpy\nbpy.ops.mesh.primitive_uv_sphere_add(radius=2)\n```\nThat creates a sphere.",
         "blender", "uv_sphere"),
        ("First install:\n```bash\npip install cadquery\n```\nThen:\n```python\nimport cadquery as cq\nresult = cq.Workplane('XY').box(1,2,3)\n```",
         "cadquery", "Workplane"),
        ("Sure!\nimport bpy\nbpy.ops.mesh.primitive_cone_add()\n", "blender", "cone"),
        ("```\n$fn = 64;\nmodule part(){ cube([10,10,2]); }\npart();\n```", "openscad", "module part"),
        ("```glsl\nvoid mainImage(out vec4 O, in vec2 U){ O = vec4(1.0); }\n```", "glsl", "mainImage"),
        ("<think>thinking forever without closing\n```python\nimport bpy\nbpy.ops.mesh.primitive_torus_add()\n```",
         "blender", "torus"),
    ]
    for text, d, needle in T:
        code, meta = extract(text, d)
        print(("OK  " if needle in code else "FAIL"), d, json.dumps({k: meta[k] for k in ("n_blocks", "chosen", "score", "had_think", "from_think", "no_fence", "valid")}))
