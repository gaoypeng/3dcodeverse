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
  --shot-bg:#e8e8ec; --shot-check:#eeeef1; --shot-ring:rgba(16,16,24,.14);
  --overlay:rgba(12,12,16,.62);
  --chip:#ebebf0; --chip-fg:#4b4b57;
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
    --shot-bg:#1a1b20; --shot-check:#1f2027; --shot-ring:rgba(255,255,255,.14);
    --overlay:rgba(0,0,0,.66);
    --chip:#24262c; --chip-fg:#b1b2bd;
  }
}
:root[data-theme="dark"]{
  --bg:#0f1013; --surface:#17181c; --sunken:#111216; --line:#2a2c33; --line-strong:#3b3e47;
  --fg:#e8e8ec; --fg-2:#b1b2bd; --fg-3:#83848f;
  --accent:#7aa2f7; --accent-fg:#0f1013; --accent-soft:#1b2438;
  --ok:#5fd18b; --ok-soft:#14291d; --bad:#ff8a80; --bad-soft:#2e1614; --warn:#e3b341; --warn-soft:#2b2210;
  --tier-a:#5fd18b; --tier-b:#a8cf5c; --tier-c:#e3b341; --tier-d:#ff8a80;
  --shadow:0 1px 2px rgba(0,0,0,.5), 0 6px 18px rgba(0,0,0,.35);
  --shot-bg:#1a1b20; --shot-check:#1f2027; --shot-ring:rgba(255,255,255,.14);
  --overlay:rgba(0,0,0,.66);
  --chip:#24262c; --chip-fg:#b1b2bd;
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

select,input[type=search]{font:inherit;font-size:var(--fs-sm);color:var(--fg);background:var(--bg);
  border:1px solid var(--line);border-radius:var(--r-1);padding:5px 7px;max-width:100%;width:100%}
.checker{background-color:var(--shot-bg);
  background-image:repeating-conic-gradient(var(--shot-check) 0% 25%,transparent 0% 50%);
  background-size:22px 22px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.up{color:var(--ok)} .down{color:var(--bad)}
.clamp1{display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden;
  text-overflow:ellipsis}
.clamp2{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
  text-overflow:ellipsis}

section.battery{margin:var(--s-5) 0}
section.battery > h2{display:flex;gap:var(--s-2) var(--s-3);align-items:baseline;flex-wrap:wrap}
.count{font-size:var(--fs-sm);color:var(--fg-3);font-weight:500}
.rootpath{font-size:var(--fs-xs);color:var(--fg-3);font-family:var(--mono);font-weight:400;
  overflow-wrap:anywhere;margin-left:auto}

/* --- badges: solid muted fills, no hairline outlines ------------------------ */
.tag{display:inline-flex;align-items:center;gap:4px;font-size:var(--fs-xs);padding:2px 8px;
  border-radius:999px;border:0;color:var(--chip-fg);background:var(--chip);white-space:nowrap;
  font-weight:550;line-height:1.5}
.tier{font-weight:750;color:var(--accent-fg);min-width:20px;justify-content:center}
.tier.A{background:var(--tier-a)} .tier.B{background:var(--tier-b)}
.tier.C{background:var(--tier-c)} .tier.D{background:var(--tier-d)}
.pill-pass,.v-pass.tag,.pill-ok{color:var(--ok);background:var(--ok-soft)}
.pill-fail,.v-fail.tag{color:var(--bad);background:var(--bad-soft)}
.pill-warn,.v-err.tag{color:var(--warn);background:var(--warn-soft)}
.v-none.tag{color:var(--fg-3);background:var(--chip)}
.pill-accent{color:var(--accent);background:var(--accent-soft)}

.tablewrap{overflow-x:auto;margin-top:var(--s-3);border:1px solid var(--line);border-radius:var(--r-2);
  background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:var(--fs-sm)}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
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

/* --- render tiles: the label floats on a gradient, never a black bar -------- */
.shots{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(210px,100%),1fr));gap:var(--s-3)}
.shots figure{margin:0;background-color:var(--shot-bg);
  background-image:repeating-conic-gradient(var(--shot-check) 0% 25%,transparent 0% 50%);
  background-size:22px 22px;border-radius:var(--r-2);overflow:hidden;
  box-shadow:inset 0 0 0 1px var(--shot-ring)}
.shots figure a{display:block;position:relative;color:inherit}
.shots img{width:100%;display:block;aspect-ratio:1;object-fit:contain}
.shots figcaption{position:absolute;inset:auto 0 0 0;font-size:var(--fs-sm);font-weight:550;
  color:#fff;padding:22px 10px 7px;overflow-wrap:anywhere;letter-spacing:.01em;
  background:linear-gradient(to top,var(--overlay),transparent);text-shadow:0 1px 2px rgba(0,0,0,.55)}

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

INDEX_CSS = """
/* --- status rail: four disjoint buckets that add up to the runs on screen --- */
.summary{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s-2) var(--s-3);
  margin:var(--s-3) 0 var(--s-2);padding:8px var(--s-3);background:var(--surface);
  border:1px solid var(--line);border-radius:var(--r-2)}
.summary .sumn{font-size:var(--fs-sm);color:var(--fg-2);display:flex;align-items:baseline;gap:5px}
.summary .sumn b{font-size:var(--fs-xl);font-weight:700;color:var(--fg)}
.summary .eq{font-size:var(--fs-lg);padding:0 2px}
.vchips{display:flex;flex-wrap:wrap;gap:5px}
.vchip{font:inherit;font-size:var(--fs-sm);display:inline-flex;align-items:baseline;gap:5px;
  padding:3px 9px;border-radius:999px;border:0;cursor:pointer;background:var(--chip);
  color:var(--chip-fg);font-weight:550}
.vchip b{font-size:var(--fs-md);font-weight:700}
.vchip .pct{font-size:var(--fs-xs);opacity:.7}
.vchip:hover{filter:brightness(.97)}
:root[data-theme="dark"] .vchip:hover{filter:brightness(1.2)}
.vchip.v-pass{color:var(--ok);background:var(--ok-soft)}
.vchip.v-fail{color:var(--bad);background:var(--bad-soft)}
.vchip.v-err{color:var(--warn);background:var(--warn-soft)}
.vchip[aria-pressed="true"]{outline:2px solid currentColor;outline-offset:1px}
.vbar{display:flex;height:8px;flex:1 1 160px;min-width:120px;border-radius:999px;overflow:hidden;
  background:var(--sunken)}
.vbar .seg{display:block;min-width:0;transition:flex-grow .2s}
.vbar .seg.v-pass{background:var(--ok)} .vbar .seg.v-fail{background:var(--bad)}
.vbar .seg.v-none{background:var(--line-strong)} .vbar .seg.v-err{background:var(--warn)}
.sumnums{display:flex;flex-wrap:wrap;gap:2px var(--s-3);font-size:var(--fs-sm);color:var(--fg-3);
  margin-left:auto}
.sumnums b{color:var(--fg-2);font-weight:650}

/* --- filters: one aligned grid, collapsible on a phone --------------------- */
details.filters{margin:0 0 var(--s-3);background:var(--surface);border:1px solid var(--line);
  border-radius:var(--r-2)}
details.filters > summary{cursor:pointer;padding:7px var(--s-3);font-size:var(--fs-sm);
  color:var(--fg-2);list-style:none;display:flex;align-items:center;gap:6px}
details.filters > summary::-webkit-details-marker{display:none}
details.filters > summary::before{content:"▸";display:inline-block;transition:transform .15s;
  color:var(--fg-3)}
details.filters[open] > summary::before{transform:rotate(90deg)}
details.filters[open] > summary{border-bottom:1px solid var(--line)}
.controls{display:grid;grid-template-columns:repeat(auto-fit,minmax(126px,1fr));gap:var(--s-2) var(--s-3);
  align-items:end;padding:var(--s-3)}
.controls .fld{display:flex;flex-direction:column;gap:3px;min-width:0}
.controls .fld.grow{grid-column:span 2}
.controls .flab{font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:.06em;color:var(--fg-3)}
.controls .btn{width:100%;padding:6px 10px}

/* --- cards: one hero view, a verdict rail, two links and a menu ------------- */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(272px,100%),1fr));gap:var(--s-3);
  margin-top:var(--s-3)}
.card{position:relative;background:var(--surface);border:1px solid var(--line);
  border-left:6px solid var(--line-strong);border-radius:var(--r-2);overflow:hidden;
  display:flex;flex-direction:column;box-shadow:var(--shadow)}
.card.v-pass{border-left-color:var(--ok)}
.card.v-fail{border-left-color:var(--bad)}
.card.v-err{border-left-color:var(--warn)}
.card.v-none{border-left-color:var(--line-strong)}
.card.v-fail,.card.v-err{background:color-mix(in srgb,var(--surface) 94%,var(--bad-soft))}
.card .shot{position:relative;display:block;background-color:var(--shot-bg);
  background-image:repeating-conic-gradient(var(--shot-check) 0% 25%,transparent 0% 50%);
  background-size:22px 22px;aspect-ratio:4/3;overflow:hidden;
  box-shadow:inset 0 0 0 1px var(--shot-ring)}
.card .shot > a{display:block;height:100%}
.card .shot > a > img{width:100%;height:100%;object-fit:contain;display:block}
.card .noshot{width:100%;height:100%;display:flex;align-items:center;justify-content:center;
  color:var(--fg-3);font-size:var(--fs-sm)}
.viewtag{position:absolute;left:0;right:0;bottom:0;padding:16px 8px 5px;color:#fff;
  font-size:var(--fs-xs);font-weight:550;pointer-events:none;
  background:linear-gradient(to top,var(--overlay),transparent);text-shadow:0 1px 2px rgba(0,0,0,.5)}
details.views{position:absolute;top:6px;right:6px}
details.views > summary{list-style:none;cursor:pointer;font-size:var(--fs-xs);font-weight:650;
  padding:2px 7px;border-radius:999px;background:var(--overlay);color:#fff;opacity:.55}
details.views > summary::-webkit-details-marker{display:none}
.card:hover details.views > summary,details.views[open] > summary{opacity:1}
details.views .sheetover{position:fixed;display:none}
details.views[open] .sheetover{display:block;position:absolute;top:0;right:0;
  width:min(420px,72vw);background:var(--surface);border:1px solid var(--line-strong);
  border-radius:var(--r-2);box-shadow:var(--shadow);z-index:6;padding:3px}
.card .pick{position:absolute;top:5px;left:5px;z-index:5;padding:6px;border-radius:var(--r-2);
  background:color-mix(in srgb,var(--surface) 86%,transparent);opacity:.45;transition:opacity .12s;
  cursor:pointer;box-shadow:0 1px 3px rgba(0,0,0,.18)}
.card:hover .pick,.card .pick:focus-within,.card.picked .pick{opacity:1}
.card.picked{outline:2px solid var(--accent);outline-offset:-2px}
.pick input{width:18px;height:18px;accent-color:var(--accent);cursor:pointer;display:block;margin:0}
.card .body{padding:var(--s-3);display:flex;flex-direction:column;gap:5px;flex:1}
.titlerow{display:flex;gap:var(--s-2);align-items:baseline}
.titlerow{align-items:start}
.titlerow .name{font-weight:650;min-width:0;color:var(--fg);flex:1;display:-webkit-box;
  -webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;text-overflow:ellipsis;
  overflow-wrap:break-word}
.titlerow .score{margin-left:auto;font-size:var(--fs-lg);font-weight:700}
.prompt{color:var(--fg-2);font-size:var(--fs-sm);overflow-wrap:anywhere;display:-webkit-box;
  -webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;text-overflow:ellipsis}
.facts{font-size:var(--fs-sm);color:var(--fg-2);display:flex;flex-wrap:wrap;gap:4px 8px;
  align-items:center}
.facts.tags{gap:3px}
.actions{display:flex;align-items:center;gap:var(--s-3);font-size:var(--fs-sm);padding-top:6px;
  border-top:1px solid var(--line);margin-top:auto;position:relative}
.actions .go{font-weight:650}
details.menu{margin-left:auto;position:relative}
details.menu > summary{list-style:none;cursor:pointer;color:var(--fg-3);padding:0 6px;
  border-radius:var(--r-1);font-weight:700;letter-spacing:.08em}
details.menu > summary::-webkit-details-marker{display:none}
details.menu > summary:hover{color:var(--fg);background:var(--sunken)}
details.menu .pop{position:absolute;right:0;bottom:calc(100% + 6px);z-index:8;min-width:150px;
  display:flex;flex-direction:column;gap:1px;padding:5px;background:var(--surface);
  border:1px solid var(--line-strong);border-radius:var(--r-2);box-shadow:var(--shadow)}
details.menu .pop a{padding:3px 7px;border-radius:var(--r-1);white-space:nowrap}
details.menu .pop a:hover{background:var(--sunken);text-decoration:none}

/* --- table mode ------------------------------------------------------------ */
.tablewrap{display:none}
body.view-table .grid{display:none}
body.view-table .tablewrap{display:block}
body.view-table table{width:max-content;min-width:100%}

td.pickcol,th.pickcol{width:30px;padding-right:0;position:sticky;left:0;z-index:2;
  background:var(--surface)}
th.pickcol{z-index:4}
td.wide,th.runcol{position:sticky;left:30px;z-index:2;background:var(--surface)}
th.runcol{z-index:4}
tr.picked td.pickcol,tr.picked td.wide{background:var(--accent-soft)}
td.pickcol .pick{opacity:1;position:static;background:none;padding:0}
td.thumbcol,th.thumbcol{width:66px;padding:3px 6px}
img.rowthumb{width:52px;height:40px;object-fit:contain;background:var(--shot-bg);border-radius:var(--r-1);
  display:block;box-shadow:inset 0 0 0 1px var(--shot-ring)}
tr.v-fail td:first-child{box-shadow:inset 3px 0 0 var(--bad)}
tr.v-pass td:first-child{box-shadow:inset 3px 0 0 var(--ok)}
tr.v-err td:first-child{box-shadow:inset 3px 0 0 var(--warn)}
tr.picked{background:var(--accent-soft)}
td.rowlinks a{margin-right:8px}
td.rowlinks{white-space:nowrap}
td.wide{min-width:180px;max-width:230px}
td.backend,th.backend{max-width:190px;overflow:hidden;text-overflow:ellipsis}
td.track,td.lang{max-width:140px;overflow:hidden;text-overflow:ellipsis}
@media (max-width:1100px){td.track,th.track,td.lang,th.lang{display:none}}
@media (max-width:640px){
  td.backend,th.backend,td.base,th.base,td.delta,th.delta,td.min,th.min,
  td.rowlinks,th.rowlinks,td.tier,th.tier{display:none}
  td.wide,th.runcol{position:static;min-width:0}
  td.pickcol,th.pickcol{position:static}
  td.thumbcol,th.thumbcol{display:none}
  body.view-table table{width:100%}
  .narrowhint{display:block}
}
.narrowhint{display:none;padding:5px 10px;border-bottom:1px solid var(--line)}

/* --- bulk bar -------------------------------------------------------------- */
.selbar{position:sticky;bottom:0;z-index:30;display:flex;flex-wrap:wrap;align-items:center;
  gap:var(--s-2) var(--s-3);padding:var(--s-2) var(--s-4);
  background:color-mix(in srgb,var(--surface) 94%,transparent);backdrop-filter:blur(8px);
  border-top:1px solid var(--line-strong);box-shadow:0 -4px 14px rgba(0,0,0,.10)}
.selbar.empty{color:var(--fg-3)}
.selbar.empty .btn{opacity:.45;pointer-events:none}
.selbar .selcount b{font-size:var(--fs-lg)}
.selbar .btn.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-fg)}
.selbar .selnote{margin-left:auto}
@media (max-width:520px){.selbar .selnote{display:none}}
@media (max-width:760px){.selbar.empty{display:none}}
.factive{display:flex;gap:4px;flex-wrap:wrap;margin-left:6px}
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
