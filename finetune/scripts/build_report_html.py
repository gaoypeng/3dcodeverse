"""Render REPORT.md into a self-contained, themed HTML page (for publishing as an Artifact)."""
import markdown, re, datetime, sys
SRC = "/wekafs/ict/hx_624/llm-ft/REPORT.md"; DST = "/wekafs/ict/hx_624/llm-ft/REPORT.html"
md = open(SRC).read()
body = markdown.markdown(md, extensions=["tables", "fenced_code", "toc"], extension_configs={"toc": {"toc_depth": "2-3"}})
toc = re.search(r'<div class="toc">(.*?)</div>', body, re.S)
toc_html = toc.group(1) if toc else ""
body = body.replace(toc.group(0), "") if toc else body
css = """
:root{--bg:#f6f4ef;--paper:#fffdf8;--ink:#1f2a2e;--muted:#5b6a70;--line:#d9d4c7;--accent:#0e7c7b;--accent-2:#b5541c;--code:#eef2f1;--head:#13343b}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#15191b;--paper:#1c2124;--ink:#e6e2d8;--muted:#a3ada9;--line:#2f373a;--accent:#5ec8c5;--accent-2:#e59a62;--code:#242b2e;--head:#dfe9e7}}
:root[data-theme="dark"]{--bg:#15191b;--paper:#1c2124;--ink:#e6e2d8;--muted:#a3ada9;--line:#2f373a;--accent:#5ec8c5;--accent-2:#e59a62;--code:#242b2e;--head:#dfe9e7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"IBM Plex Sans","Noto Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif;font-size:16px;line-height:1.7}
.wrap{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:40px;max-width:1180px;margin:0 auto;padding:40px 28px 80px}
@media (max-width:900px){.wrap{grid-template-columns:1fr}.toc{display:none}}
main{min-width:0;background:var(--paper);border:1px solid var(--line);border-radius:6px;padding:40px 44px}
@media (max-width:600px){main{padding:22px 18px}}
header.hero{margin-bottom:28px;border-bottom:2px solid var(--accent);padding-bottom:18px}
header.hero .eyebrow{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent-2)}
h1,h2,h3,h4{color:var(--head);text-wrap:balance;line-height:1.25;font-family:"IBM Plex Sans","Noto Sans SC","PingFang SC",sans-serif}
h1{font-size:30px;margin:6px 0 8px;font-weight:600}
h2{font-size:22px;margin:44px 0 12px;padding-top:14px;border-top:1px solid var(--line);font-weight:600}
h3{font-size:17px;margin:28px 0 8px;font-weight:600}
p,li{max-width:78ch}
blockquote{margin:14px 0;padding:10px 16px;border-left:3px solid var(--accent);background:var(--code);color:var(--muted);border-radius:0 4px 4px 0}
code,pre{font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.88em}
code{background:var(--code);padding:1px 5px;border-radius:3px}
pre{background:var(--code);padding:12px 14px;border-radius:4px;overflow-x:auto}
pre code{background:none;padding:0}
.tbl{overflow-x:auto;margin:14px 0}
table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12.5px;letter-spacing:.04em;text-transform:uppercase;background:var(--code)}
tr:hover td{background:color-mix(in srgb,var(--accent) 6%,transparent)}
strong{color:var(--head)}
a{color:var(--accent)}
.toc{position:sticky;top:24px;align-self:start;font-size:13.5px;line-height:1.5}
.toc ul{list-style:none;padding-left:0;margin:0}
.toc ul ul{padding-left:12px;margin-top:2px}
.toc li{margin:4px 0}
.toc a{color:var(--muted);text-decoration:none}
.toc a:hover{color:var(--accent)}
.toc .label{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent-2);margin-bottom:8px}
footer{margin-top:40px;color:var(--muted);font-size:13px}
"""
body = re.sub(r"<table>", '<div class="tbl"><table>', body); body = re.sub(r"</table>", "</table></div>", body)
body = re.sub(r"<h1[^>]*>.*?</h1>", "", body, count=1)
html = f"""<title>3D-code LLM Finetune Lab Notes</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&family=Noto+Sans+SC:wght@400;500;700&display=swap">
<style>{css}</style>
<div class="wrap">
<main>
<header class="hero"><div class="eyebrow">dgx03 · 4×H100 · {datetime.date.today().isoformat()}</div>
<h1>3D-code LLM finetune 调研报告</h1>
<p style="color:var(--muted);margin:0">Qwen3.5-9B × LLaMA-Factory × 3DCodeVerse → 3DCodeBench（Blender 5.0 执行评测）。工程目录 <code>/wekafs/ict/hx_624/llm-ft</code>。</p></header>
{body}
<footer>Generated from <code>llm-ft/REPORT.md</code> · all numbers reproducible via <code>eval/compare.py</code> / <code>eval/results_table.py</code>.</footer>
</main>
<nav class="toc"><div class="label">Contents</div>{toc_html}</nav>
</div>"""
open(DST, "w").write(html); print("wrote", DST, len(html), "bytes")
