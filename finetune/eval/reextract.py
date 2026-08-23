"""Re-extract code.py from raw.txt in a generation dir (after extractor fixes)."""
import sys, os, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate import extract_code
for d in sys.argv[1:]:
    n=0
    for raw in glob.glob(os.path.join(d, "*", "raw.txt")):
        code = extract_code(open(raw).read()); open(os.path.join(os.path.dirname(raw), "code.py"), "w").write(code + "\n"); n+=1
    print(d, "re-extracted", n)
