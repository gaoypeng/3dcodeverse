"""Strip the embedded reference-mesh fallback from thingiverse-openscad-blender-5-0 'native_csg_generated' scripts.
Writes <out>/<sample>/code.py (+ copies captions.json) for samples whose stripped script still parses."""
import ast, glob, json, os, re, shutil, sys
SRC = "/wekafs/ict/hx_624/data/thingiverse_5_0"; OUT = "/wekafs/ict/hx_624/data/thingiverse_5_0_native"
os.makedirs(OUT, exist_ok=True); kept = dropped = 0
for cp in sorted(glob.glob(f"{SRC}/*/code.py")):
    d = os.path.dirname(cp); rep = os.path.join(d, "reports/translation_report.json")
    st = json.load(open(rep)).get("status", "") if os.path.exists(rep) else ""
    if st != "native_csg_generated": continue
    code = open(cp).read()
    # 1) remove the fallback function definition
    code2 = re.sub(r"\ndef load_mesh_fallback\(\):.*?(?=\n(?:def |if __name__|[A-Za-z_])|\Z)", "\n", code, flags=re.S)
    # 2) rewrite 'try: <native> except ...: load_mesh_fallback()' -> just the native call (common pattern)
    code2 = re.sub(r"try:\n(\s+)(.*?)\n\s*except [^\n]*:\n\s+(?:[^\n]*\n)*?\s*(?:obj\s*=\s*)?load_mesh_fallback\(\)[^\n]*\n", lambda m: m.group(1) + m.group(2) + "\n", code2, flags=re.S)
    code2 = re.sub(r"^.*load_mesh_fallback\(\).*$", "", code2, flags=re.M)
    code2 = re.sub(r"^MESH_REFERENCE_ASSET\s*=.*$", "", code2, flags=re.M)
    if "load_mesh_fallback" in code2 or "reference_mesh" in code2:
        dropped += 1; continue
    try: ast.parse(code2)
    except SyntaxError: dropped += 1; continue
    od = os.path.join(OUT, os.path.basename(d)); os.makedirs(od, exist_ok=True)
    open(os.path.join(od, "code.py"), "w").write(code2)
    for f in ["captions.json", "meta.json"]:
        if os.path.exists(os.path.join(d, f)): shutil.copy(os.path.join(d, f), od)
    kept += 1
print(f"kept {kept} dropped {dropped} -> {OUT}")
