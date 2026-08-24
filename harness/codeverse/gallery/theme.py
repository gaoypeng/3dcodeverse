"""The gallery's design system: one token set, one page shell, no external assets.

Everything is inlined (offline is a hard requirement — no CDN, no Google Fonts).
Colour, spacing, radius and type scale live as custom properties on ``:root``;
dark mode is expressed **three** times on purpose so both the OS preference and
the manual toggle win in both directions:

* ``:root`` — the complete light palette (the only place a colour is born)
* ``@media (prefers-color-scheme: dark)`` guarded by ``:root:not([data-theme="light"])``
* ``:root[data-theme="dark"]`` — the manual toggle

``page_shell`` is the only place that emits ``<html>``; every page in this
package returns a body fragment and hands it here.
"""

from __future__ import annotations

import html

TOKENS = """
:root{
  --bg:#f7f7f8; --surface:#ffffff; --sunken:#eeeef1; --line:#dcdce2; --line-strong:#c3c3cd;
  --fg:#16161a; --fg-2:#4b4b57; --fg-3:#75757f;
  --accent:#2f5bd0; --accent-fg:#ffffff; --accent-soft:#e6ecfb;
  --ok:#1f7a3d; --ok-soft:#e2f3e7; --bad:#b3261e; --bad-soft:#fbe6e4; --warn:#8a5a00; --warn-soft:#fbf0d9;
  --tier-a:#1f7a3d; --tier-b:#5c7a1f; --tier-c:#8a5a00; --tier-d:#b3261e;
  --shadow:0 1px 2px rgba(16,16,24,.06), 0 4px 12px rgba(16,16,24,.05);
  --r-1:4px; --r-2:8px; --r-3:14px;
  --s-1:4px; --s-2:8px; --s-3:12px; --s-4:16px; --s-5:24px; --s-6:36px;
  --fs-xs:11.5px; --fs-sm:12.5px; --fs-md:14px; --fs-lg:17px; --fs-xl:22px;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  --maxw:1680px;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#0f1013; --surface:#17181c; --sunken:#111216; --line:#2a2c33; --line-strong:#3b3e47;
    --fg:#e8e8ec; --fg-2:#b1b2bd; --fg-3:#83848f;
    --accent:#7aa2f7; --accent-fg:#0f1013; --accent-soft:#1b2438;
    --ok:#5fd18b; --ok-soft:#14291d; --bad:#ff8a80; --bad-soft:#2e1614; --warn:#e3b341; --warn-soft:#2b2210;
    --tier-a:#5fd18b; --tier-b:#a8cf5c; --tier-c:#e3b341; --tier-d:#ff8a80;
    --shadow:0 1px 2px rgba(0,0,0,.5), 0 6px 18px rgba(0,0,0,.35);
  }
}
:root[data-theme="dark"]{
  --bg:#0f1013; --surface:#17181c; --sunken:#111216; --line:#2a2c33; --line-strong:#3b3e47;
  --fg:#e8e8ec; --fg-2:#b1b2bd; --fg-3:#83848f;
  --accent:#7aa2f7; --accent-fg:#0f1013; --accent-soft:#1b2438;
  --ok:#5fd18b; --ok-soft:#14291d; --bad:#ff8a80; --bad-soft:#2e1614; --warn:#e3b341; --warn-soft:#2b2210;
  --tier-a:#5fd18b; --tier-b:#a8cf5c; --tier-c:#e3b341; --tier-d:#ff8a80;
  --shadow:0 1px 2px rgba(0,0,0,.5), 0 6px 18px rgba(0,0,0,.35);
}
"""

BASE_CSS = """
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:var(--fs-md)/1.5 var(--sans);
  overflow-x:hidden;font-variant-numeric:tabular-nums}
.wrap{max-width:var(--maxw);margin:0 auto;padding:var(--s-5) var(--s-4) var(--s-6)}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
a:focus-visible,button:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible{
  outline:2px solid var(--accent);outline-offset:2px;border-radius:var(--r-1)}
h1,h2,h3{margin:0;font-weight:650;letter-spacing:-.01em;line-height:1.25}
h1{font-size:var(--fs-xl)} h2{font-size:var(--fs-lg)} h3{font-size:var(--fs-md)}
p{margin:0}
code,pre,.mono{font-family:var(--mono)}
.num{font-variant-numeric:tabular-nums}
.muted{color:var(--fg-2)} .faint{color:var(--fg-3)}
.small{font-size:var(--fs-sm)} .xs{font-size:var(--fs-xs)}

header.top{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--bg) 88%,transparent);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
header.top .bar{max-width:var(--maxw);margin:0 auto;padding:var(--s-3) var(--s-4);
  display:flex;gap:var(--s-3) var(--s-4);align-items:center;flex-wrap:wrap;overflow-wrap:anywhere}
.brand{display:flex;gap:var(--s-2);align-items:baseline}
.brand b{font-size:var(--fs-lg);letter-spacing:-.02em}
.spacer{margin-left:auto}
.btn{font:inherit;font-size:var(--fs-sm);color:var(--fg);background:var(--surface);border:1px solid var(--line);
  border-radius:var(--r-2);padding:5px 10px;cursor:pointer}
.btn:hover{border-color:var(--line-strong)}
.btn[aria-pressed="true"]{background:var(--accent-soft);border-color:var(--accent);color:var(--fg)}

.crumbs{display:flex;gap:var(--s-2);align-items:center;font-size:var(--fs-sm);color:var(--fg-2);flex-wrap:wrap;
  overflow-wrap:anywhere;min-width:0}
.crumbs a{color:var(--fg-2)}

.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:var(--s-2);
  margin:var(--s-4) 0}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-2);padding:var(--s-3)}
.stat .k{font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:.06em;color:var(--fg-3)}
.stat .v{font-size:var(--fs-lg);font-weight:650;margin-top:2px}

.controls{display:flex;gap:var(--s-2);align-items:center;flex-wrap:wrap;margin:var(--s-4) 0 var(--s-3);
  padding:var(--s-3);background:var(--surface);border:1px solid var(--line);border-radius:var(--r-2)}
.controls label{display:flex;gap:6px;align-items:center;font-size:var(--fs-sm);color:var(--fg-2)}
select,input[type=search]{font:inherit;font-size:var(--fs-sm);color:var(--fg);background:var(--bg);
  border:1px solid var(--line);border-radius:var(--r-1);padding:4px 6px;max-width:100%}
input[type=search]{min-width:min(260px,60vw)}

section.battery{margin:var(--s-5) 0}
section.battery > h2{display:flex;gap:var(--s-2);align-items:baseline}
.count{font-size:var(--fs-sm);color:var(--fg-3);font-weight:500}
.rootpath{font-size:var(--fs-xs);color:var(--fg-3);font-family:var(--mono);margin-top:2px;
  overflow-wrap:anywhere}

.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(320px,100%),1fr));gap:var(--s-4);
  margin-top:var(--s-3)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-3);overflow:hidden;
  display:flex;flex-direction:column;box-shadow:var(--shadow)}
.card.pass{border-top:3px solid var(--ok)} .card.fail{border-top:3px solid var(--bad)}
.card.na{border-top:3px solid var(--line-strong)}
.card .shot{display:block;background:var(--sunken);aspect-ratio:16/10;overflow:hidden}
.card .shot img{width:100%;height:100%;object-fit:contain;display:block}
.card .noshot{width:100%;height:100%;display:flex;align-items:center;justify-content:center;
  color:var(--fg-3);font-size:var(--fs-sm)}
.card .body{padding:var(--s-3);display:flex;flex-direction:column;gap:6px;flex:1}
.titlerow{display:flex;gap:var(--s-2);align-items:center}
.titlerow .name{font-weight:650;overflow-wrap:anywhere;min-width:0}
.titlerow .score{margin-left:auto;font-size:var(--fs-lg);font-weight:650}
.prompt{color:var(--fg-2);font-size:var(--fs-sm);overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:3;
  -webkit-box-orient:vertical;overflow:hidden}
.facts{font-size:var(--fs-sm);color:var(--fg-2);display:flex;flex-wrap:wrap;gap:2px 8px}
.links{display:flex;flex-wrap:wrap;gap:2px 8px;font-size:var(--fs-sm);padding-top:2px;
  border-top:1px solid var(--line);margin-top:auto}

.tag{display:inline-flex;align-items:center;gap:4px;font-size:var(--fs-xs);padding:1px 7px;border-radius:999px;
  border:1px solid var(--line);color:var(--fg-2);background:var(--sunken);white-space:nowrap}
.tier{font-weight:700;color:var(--accent-fg)}
.tier.A{background:var(--tier-a);border-color:var(--tier-a)}
.tier.B{background:var(--tier-b);border-color:var(--tier-b)}
.tier.C{background:var(--tier-c);border-color:var(--tier-c)}
.tier.D{background:var(--tier-d);border-color:var(--tier-d)}
.pill-pass{color:var(--ok);background:var(--ok-soft);border-color:var(--ok)}
.pill-fail{color:var(--bad);background:var(--bad-soft);border-color:var(--bad)}
.pill-warn{color:var(--warn);background:var(--warn-soft);border-color:var(--warn)}

.tablewrap{overflow-x:auto;margin-top:var(--s-3);border:1px solid var(--line);border-radius:var(--r-2);
  background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:var(--fs-sm)}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:.05em;color:var(--fg-3);
  position:sticky;top:0;background:var(--surface)}
tbody tr:last-child td{border-bottom:0}
td.wide{white-space:normal;min-width:220px;max-width:520px}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}

.panel{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-2);
  padding:var(--s-4);margin:var(--s-4) 0}
.panel > h2{margin-bottom:var(--s-3)}
.kvs{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:var(--s-2) var(--s-4)}
.kv{display:flex;gap:var(--s-2);font-size:var(--fs-sm);border-bottom:1px dotted var(--line);padding:3px 0}
.kv .k{color:var(--fg-3);min-width:110px}
.kv .v{overflow-wrap:anywhere}
ul.plain{margin:0;padding-left:18px;font-size:var(--fs-sm);color:var(--fg-2)}
ul.plain li{margin:2px 0}

.shots{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:var(--s-3)}
.shots figure{margin:0;background:var(--sunken);border:1px solid var(--line);border-radius:var(--r-2);
  overflow:hidden}
.shots img{width:100%;display:block;background:var(--sunken)}
.shots figcaption{font-size:var(--fs-xs);color:var(--fg-3);padding:4px 6px;overflow-wrap:anywhere}

pre.code{margin:0;padding:var(--s-3);background:var(--sunken);border:1px solid var(--line);
  border-radius:var(--r-2);overflow-x:auto;font-size:var(--fs-sm);line-height:1.55;tab-size:4}
pre.code .ln{display:inline-block;width:3.5em;color:var(--fg-3);user-select:none;text-align:right;
  padding-right:1em}
.err{color:var(--bad);background:var(--bad-soft);border:1px solid var(--bad);border-radius:var(--r-1);
  padding:var(--s-2);font-size:var(--fs-sm);overflow-wrap:anywhere;white-space:pre-wrap}
.is-hidden{display:none !important}
.empty{padding:var(--s-5);text-align:center;color:var(--fg-3)}
footer.foot{max-width:var(--maxw);margin:0 auto;padding:var(--s-4);color:var(--fg-3);font-size:var(--fs-xs);
  border-top:1px solid var(--line);overflow-wrap:anywhere}
"""

#: sets the stored theme before first paint so a manual choice never flashes
THEME_BOOT_JS = """
(function(){try{var t=localStorage.getItem('3dcv-gallery-theme');
if(t==='dark'||t==='light')document.documentElement.setAttribute('data-theme',t);}catch(e){}})();
"""

THEME_TOGGLE_JS = """
(function(){var b=document.getElementById('theme-btn');if(!b)return;
function cur(){var a=document.documentElement.getAttribute('data-theme');if(a)return a;
return matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}
function paint(){b.textContent=cur()==='dark'?'\\u25D1 dark':'\\u25D0 light';}
b.addEventListener('click',function(){var n=cur()==='dark'?'light':'dark';
document.documentElement.setAttribute('data-theme',n);
try{localStorage.setItem('3dcv-gallery-theme',n);}catch(e){}paint();});paint();})();
"""

#: inline favicon so a browser never issues a /favicon.ico request the server would 404
FAVICON = ("<link rel='icon' href=\"data:image/svg+xml,"
           "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E"
           "%3Crect width='16' height='16' rx='3' fill='%232f5bd0'/%3E"
           "%3Cpath d='M4 11V5l4 2.4L12 5v6' stroke='white' stroke-width='1.6' fill='none'/%3E"
           "%3C/svg%3E\">")

THEME_BUTTON = '<button class="btn" id="theme-btn" type="button" title="light / dark">theme</button>'


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def page_shell(title: str, body: str, *, scripts: str = "", extra_css: str = "",
               head_extra: str = "", body_class: str = "", modules: str = "") -> str:
    """A complete, self-contained HTML document.  ``body`` is a fragment.

    ``scripts`` runs as a classic script after the body; ``modules`` as an ES
    module (the GLB viewer needs one)."""
    cls = f" class='{esc(body_class)}'" if body_class else ""
    mod = f"<script type='module'>{modules}</script>\n" if modules else ""
    return (
        "<!doctype html>\n<html lang='en'>\n<head>\n<meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>\n"
        f"<title>{esc(title)}</title>\n{FAVICON}\n"
        f"<script>{THEME_BOOT_JS}</script>\n"
        f"<style>{TOKENS}{BASE_CSS}{extra_css}</style>\n{head_extra}</head>\n<body{cls}>\n"
        f"{body}\n"
        f"<script>{THEME_TOGGLE_JS}{scripts}</script>\n{mod}</body>\n</html>\n"
    )


def top_bar(title: str, subtitle: str = "", *, crumbs: str = "", right: str = "") -> str:
    """Sticky header: brand + optional breadcrumb trail + the theme toggle."""
    sub = f"<span class='muted small'>{esc(subtitle)}</span>" if subtitle else ""
    crumb = f"<nav class='crumbs'>{crumbs}</nav>" if crumbs else ""
    return (f"<header class='top'><div class='bar'><div class='brand'><b>{esc(title)}</b>{sub}</div>"
            f"{crumb}<div class='spacer'></div>{right}{THEME_BUTTON}</div></header>")


def footer(text: str) -> str:
    return f"<footer class='foot'>{esc(text)}</footer>"
