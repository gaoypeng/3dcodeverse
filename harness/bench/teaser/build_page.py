"""Render docs/teaser.html from content.py.  Run: python bench/teaser/build_page.py"""
from __future__ import annotations

import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from content import ARTIC, GFX, LEAD, ML_ASSETS, SCENES, STATIC, M  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "docs" / "teaser.html"

def esc(s: str) -> str:
    return html.escape(s, quote=False)

def chips(langs):
    return "".join(f'<span class="chip">{esc(x)}</span>' for x in langs)

def verdict_badge(v, score):
    cls = "ok" if v == "reads" else "mid"
    word = "reads" if v == "reads" else "usable"
    return (f'<span class="verdict {cls}"><b>{word}</b>'
            f'<span class="score">judge {score:.3f}</span></span>')

def viewer(card_id, media):
    tabs, panes = [], []
    for i, (kind, name, label) in enumerate(media):
        act = " on" if i == 0 else ""
        tabs.append(f'<button class="tab{act}" data-t="{card_id}-{i}">{esc(label)}</button>')
        ext = "gif" if kind == "gif" else "jpg"
        src = f"{M}{name}.{ext}"
        attr = "data-src" if kind == "gif" else "src"   # GIFs are heavy: fetch on demand
        img = f'<img {attr}="{src}" alt="{esc(label)}" loading="lazy">'
        panes.append(f'<figure class="pane{act}" id="{card_id}-{i}">{img}</figure>')
    return (f'<div class="viewer" data-card="{card_id}">'
            f'<div class="tabs">{"".join(tabs)}</div>'
            f'<div class="stage">{"".join(panes)}</div></div>')

def card(c, kind="object"):
    rerun = f'<span class="chip warn">{esc(c["rerun"])}</span>' if c.get("rerun") else ""
    if kind == "scene":
        shots = "".join(
            f'<figure class="shot"><img src="{M}{n}.jpg" alt="{esc(cap)}" loading="lazy">'
            f'<figcaption>{esc(cap)}</figcaption></figure>' for n, cap in c["shots"])
        sn, sl = c["sheet"]
        media_block = (
            f'<div class="shots">{shots}</div>'
            f'<details class="sheetwrap"><summary>Contact sheet — {esc(sl)}</summary>'
            f'<img data-src="{M}{sn}.jpg" alt="contact sheet" loading="lazy"></details>')
    else:
        media_block = viewer(c["id"], c["media"])
    return f'''
<article class="card" id="c-{c['id']}">
  <div class="card-media">{media_block}</div>
  <div class="card-body">
    <h3>{esc(c['title'])}</h3>
    <div class="chips">{chips(c['langs'])}{rerun}</div>
    {verdict_badge(c['verdict'], c['score'])}
    <details class="prompt"><summary>prompt</summary><p>{esc(c['prompt'])}</p></details>
    <p class="note">{c['note']}</p>
    <dl class="meta">
      <div><dt>generator</dt><dd>{esc(c['gen'])}</dd></div>
      <div><dt>run</dt><dd>{esc(c['rounds'])} · {esc(c['mins'])}</dd></div>
      <div><dt>slug</dt><dd><code>{esc(c['slug'])}</code></dd></div>
    </dl>
  </div>
</article>'''

def asset_tile(a):
    flag = ('<span class="pill ok">in scene from the GLB</span>' if a["verified"]
            else '<span class="pill warn">GLB not used in scene</span>')
    return f'''
<div class="asset{'' if a['verified'] else ' caveat'}">
  <div class="asset-pics">
    <img src="{M}asset_{a['key']}.jpg" alt="{esc(a['name'])}" loading="lazy" class="still">
    <img data-src="{M}tt_asset_{a['key']}.gif" alt="{esc(a['name'])} turntable" loading="lazy" class="turn">
  </div>
  <h4>{esc(a['name'])} {flag}</h4>
  <p>{esc(a['desc'])}</p>
  <ul class="files">
    <li><span>bpy</span><code>{esc(a['src'])}</code></li>
    <li><span>glb</span><code>{esc(a['glb'])}</code>&nbsp;<span class="kb">{a['kb']}&nbsp;KB</span></li>
    <li><span>js</span><code>{esc(a['use'])}</code></li>
  </ul>
  <details class="sheetwrap"><summary>the object on its own · 8 views</summary>
    <img data-src="{M}asset_{a['key']}_sheet.jpg" alt="{esc(a['name'])} contact sheet" loading="lazy">
  </details>
</div>'''

HEAD = '''<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>3dcodeverse — teaser</title>
<style>
:root{
  --bg:#08090c; --bg2:#0d0f14; --surface:#11141a; --line:#1e222c; --line2:#2a3040;
  --fg:#e7e9ef; --dim:#98a0b0; --dim2:#6b7385;
  --amber:#f2a94b; --cyan:#5ed2e4; --green:#6fd08c; --pink:#ff5fa8;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,"Helvetica Neue",Arial,sans-serif;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 var(--sans);
  -webkit-font-smoothing:antialiased;overflow-x:hidden}
img{max-width:100%;display:block}
code{font-family:var(--mono);font-size:.85em;color:var(--cyan)}
a{color:var(--amber)}
.wrap{max-width:1240px;margin:0 auto;padding:0 28px}

/* ---- nav ---- */
nav{position:sticky;top:0;z-index:40;background:rgba(8,9,12,.86);
  backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}
nav .wrap{display:flex;align-items:center;gap:26px;height:54px}
nav .mark{font-weight:800;letter-spacing:-.03em;font-size:15px}
nav .mark i{color:var(--amber);font-style:normal}
nav a{color:var(--dim);text-decoration:none;font:500 12.5px/1 var(--mono);
  letter-spacing:.06em;text-transform:uppercase}
nav a:hover{color:var(--fg)}
nav .sp{flex:1}

/* ---- masthead ---- */
header{padding:96px 0 56px;position:relative;overflow:hidden}
header::before{content:"";position:absolute;inset:-40% -20% auto -20%;height:120%;
  background:radial-gradient(60% 60% at 30% 0%,rgba(242,169,75,.13),transparent 70%),
             radial-gradient(50% 50% at 80% 20%,rgba(94,210,228,.10),transparent 70%);
  pointer-events:none}
header .wrap{position:relative}
h1{margin:0;font-size:clamp(44px,7.4vw,96px);line-height:.94;letter-spacing:-.045em;
  font-weight:820}
h1 em{font-style:normal;color:var(--amber)}
.kicker{font:600 12px/1 var(--mono);letter-spacing:.22em;text-transform:uppercase;
  color:var(--dim2);margin:0 0 22px}
.lede{max-width:66ch;margin:26px 0 0;font-size:19px;color:var(--dim)}
.lede b{color:var(--fg);font-weight:600}
.stats{display:flex;flex-wrap:wrap;gap:0;margin:44px 0 0;border-top:1px solid var(--line);
  border-bottom:1px solid var(--line)}
.stats div{flex:1 1 130px;padding:18px 22px 16px;border-right:1px solid var(--line)}
.stats div:last-child{border-right:0}
.stats b{display:block;font-size:30px;font-weight:750;letter-spacing:-.03em}
.stats span{font:500 11px/1.4 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--dim2)}

/* ---- sections ---- */
section{padding:76px 0 8px}
.shead{display:flex;align-items:baseline;gap:18px;flex-wrap:wrap;
  border-top:1px solid var(--line2);padding-top:20px;margin-bottom:34px}
.shead h2{margin:0;font-size:30px;letter-spacing:-.03em;font-weight:760}
.shead p{margin:0;color:var(--dim2);font:500 12px/1.5 var(--mono);letter-spacing:.06em;
  text-transform:uppercase}
.shead .num{font:700 12px/1 var(--mono);color:var(--amber);letter-spacing:.14em}

/* ---- cards ---- */
.card{display:grid;grid-template-columns:minmax(0,1.28fr) minmax(0,1fr);gap:36px;
  padding:34px 0 40px;border-bottom:1px solid var(--line)}
.card:last-child{border-bottom:0}
.card-body h3{margin:0 0 12px;font-size:25px;letter-spacing:-.025em;font-weight:720}
.chips{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:14px}
.chip{font:600 11px/1 var(--mono);letter-spacing:.05em;padding:6px 9px;border-radius:3px;
  background:#161a22;border:1px solid var(--line2);color:var(--cyan)}
.chip.warn{color:var(--amber);border-color:#3a2f1c;background:#1a1610}
.verdict{display:inline-flex;align-items:center;gap:10px;margin-bottom:16px;
  font:700 11px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;
  padding:7px 11px;border-radius:3px}
.verdict.ok{background:rgba(111,208,140,.10);color:var(--green);border:1px solid rgba(111,208,140,.3)}
.verdict.mid{background:rgba(242,169,75,.10);color:var(--amber);border:1px solid rgba(242,169,75,.32)}
.verdict .score{color:var(--dim2);letter-spacing:.08em;font-weight:500}
.note{color:var(--dim);font-size:15px;margin:0 0 18px}
.note b{color:var(--fg)}
details.prompt{margin:0 0 18px;border-left:2px solid var(--line2);padding-left:14px}
details.prompt summary{cursor:pointer;font:600 11px/1 var(--mono);letter-spacing:.14em;
  text-transform:uppercase;color:var(--dim2);list-style:none}
details.prompt summary::-webkit-details-marker{display:none}
details.prompt summary::before{content:"▸ ";color:var(--amber)}
details.prompt[open] summary::before{content:"▾ "}
details.prompt p{margin:12px 0 2px;font-size:14px;color:var(--dim);font-style:italic}
dl.meta{margin:0;display:grid;grid-template-columns:1fr 1fr;gap:13px 26px;
  border-top:1px solid var(--line);padding-top:14px}
dl.meta div{min-width:0}
dl.meta dt{font:600 10px/1.6 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--dim2)}
dl.meta dd{margin:0;font:500 11.8px/1.6 var(--mono);color:var(--dim);
  word-break:normal;overflow-wrap:anywhere}

/* ---- viewer ---- */
.viewer{position:sticky;top:74px}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.tab{font:600 10.5px/1 var(--mono);letter-spacing:.09em;text-transform:uppercase;
  padding:8px 11px;border-radius:3px;border:1px solid var(--line2);background:transparent;
  color:var(--dim2);cursor:pointer;transition:.14s}
.tab:hover{color:var(--fg);border-color:#3a4356}
.tab.on{background:var(--fg);color:#0a0b0e;border-color:var(--fg)}
.stage{background:var(--bg2);border:1px solid var(--line);border-radius:5px;overflow:hidden}
.pane{margin:0;display:none}
.pane.on{display:block}
.pane img{width:100%;cursor:zoom-in}

/* ---- scene shots ---- */
.shots{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.shots .shot:first-child,.shots .shot:nth-child(2){grid-column:span 1}
.shot{margin:0;background:var(--bg2);border:1px solid var(--line);border-radius:5px;overflow:hidden}
.shot img{width:100%;cursor:zoom-in}
.shot figcaption{font:600 10.5px/1.5 var(--mono);letter-spacing:.06em;color:var(--dim2);
  padding:8px 10px;border-top:1px solid var(--line)}
details.sheetwrap{margin-top:12px}
details.sheetwrap summary{cursor:pointer;font:600 10.5px/1 var(--mono);letter-spacing:.12em;
  text-transform:uppercase;color:var(--dim2);padding:9px 0}
details.sheetwrap img{border:1px solid var(--line);border-radius:5px;margin-top:8px}

/* ---- lead ---- */
.lead{border:1px solid var(--line2);border-radius:8px;background:
  linear-gradient(180deg,rgba(242,169,75,.045),transparent 40%);padding:0 30px}
.lead .card{border-bottom:0}
.lead-tag{font:700 11px/1 var(--mono);letter-spacing:.22em;text-transform:uppercase;
  color:var(--amber);padding:22px 0 0}

/* ---- multi-language panel ---- */
#multilang{background:
  radial-gradient(70% 100% at 50% 0%,rgba(94,210,228,.07),transparent 70%),var(--bg2);
  border-top:1px solid var(--line2);border-bottom:1px solid var(--line2);
  margin-top:70px;padding:70px 0 78px}
.pipe{display:flex;align-items:stretch;gap:0;flex-wrap:wrap;margin:30px 0 46px;
  border:1px solid var(--line2);border-radius:6px;overflow:hidden}
.pipe .step{flex:1 1 200px;padding:20px 22px;border-right:1px solid var(--line2);
  background:var(--surface)}
.pipe .step:last-child{border-right:0}
.pipe .n{font:700 10px/1 var(--mono);letter-spacing:.2em;color:var(--amber);display:block;margin-bottom:9px}
.pipe .step h5{margin:0 0 6px;font-size:15px;font-weight:680;letter-spacing:-.01em}
.pipe .step p{margin:0;font-size:13px;color:var(--dim2);line-height:1.55}
.pipe .step code{font-size:11.5px}
.assets{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}
.asset{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:14px}
.asset.caveat{border-color:#3a2f1c;background:#14110d}
.asset-pics{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:12px}
.asset-pics img{background:#0a0b0e;border-radius:4px;cursor:zoom-in}
.asset h4{margin:0 0 8px;font-size:15px;font-weight:700;letter-spacing:-.01em;
  display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.pill{font:600 9.5px/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;
  padding:4px 7px;border-radius:3px}
.pill.ok{background:rgba(111,208,140,.12);color:var(--green)}
.pill.warn{background:rgba(242,169,75,.14);color:var(--amber)}
.asset p{margin:0 0 12px;font-size:13.5px;color:var(--dim);line-height:1.55}
ul.files{list-style:none;margin:0;padding:10px 0 0;border-top:1px solid var(--line)}
ul.files li{display:flex;gap:9px;font-size:11.5px;line-height:1.7;align-items:baseline}
ul.files span{font:700 9.5px/1.7 var(--mono);letter-spacing:.1em;text-transform:uppercase;
  color:var(--dim2);min-width:26px}
ul.files .kb{min-width:0;white-space:nowrap;text-transform:none;letter-spacing:0;
  font-weight:500;color:var(--dim2)}
ul.files code{color:var(--dim);word-break:normal;overflow-wrap:anywhere;line-height:1.65}
.inscene{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin-top:34px}
.inscene figure{margin:0;background:var(--surface);border:1px solid var(--line);
  border-radius:6px;overflow:hidden}
.inscene img{cursor:zoom-in}
.inscene figcaption{padding:12px 14px;font-size:13px;color:var(--dim);line-height:1.5}
.inscene figcaption b{display:block;font:700 10px/1.6 var(--mono);letter-spacing:.12em;
  text-transform:uppercase;color:var(--cyan);margin-bottom:4px}
.caveatbox{margin-top:30px;border-left:2px solid var(--amber);padding:4px 0 4px 18px;
  color:var(--dim);font-size:14.5px;max-width:82ch}
.caveatbox b{color:var(--amber)}

/* ---- footer ---- */
footer{border-top:1px solid var(--line2);margin-top:78px;padding:56px 0 90px;color:var(--dim2)}
footer h4{margin:0 0 12px;font-size:16px;color:var(--fg);font-weight:700}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:38px}
footer ul{margin:0;padding-left:18px;font-size:14px;line-height:1.75}
footer li b{color:var(--dim)}
footer p{font-size:14px;line-height:1.7;margin:0 0 12px}
footer pre{background:var(--surface);border:1px solid var(--line);border-radius:5px;
  padding:13px 15px;overflow-x:auto;font-family:var(--mono);font-size:12px;color:var(--dim)}

/* ---- lightbox ---- */
#lb{position:fixed;inset:0;z-index:100;background:rgba(5,6,8,.96);display:none;
  align-items:center;justify-content:center;padding:26px;cursor:zoom-out}
#lb.on{display:flex}
#lb img{max-width:100%;max-height:100%;object-fit:contain;border-radius:4px}

@media(max-width:920px){
  .card{grid-template-columns:1fr;gap:22px}
  .viewer{position:static}
  .wrap{padding:0 18px}
  .lead{padding:0 16px}
  header{padding:60px 0 40px}
}
</style>
'''

def build() -> str:
    p = [HEAD]
    a = p.append

    # nav
    a('''<nav><div class="wrap">
  <span class="mark">3dcodeverse<i>.</i></span>
  <a href="#multilang">multi-language</a>
  <a href="#static">objects</a>
  <a href="#artic">articulated</a>
  <a href="#scenes">scenes</a>
  <a href="#gfx">graphics</a>
  <span class="sp"></span>
  <a href="#notes">what is not here</a>
</div></nav>''')

    # masthead
    a('''<header><div class="wrap">
  <p class="kicker">a language model writes the code · the harness builds, measures and renders it</p>
  <h1>Fifteen things<br>made out of <em>raw 3D code</em>.</h1>
  <p class="lede">No mesh generator, no asset store, no SDK. Every artefact on this page is a model writing
    <b>ordinary source</b> — Blender <code>bpy</code>, CadQuery, three.js, URDF, GLSL, Python OpenGL — into a
    workspace with spatial tools, then looking at its own renders and fixing what it sees. The harness owns the
    build, the gates, the eight canonical cameras and the judge. <b>Every caption below is honest about what is
    still wrong.</b></p>
  <div class="stats">
    <div><b>15</b><span>artefacts shown</span></div>
    <div><b>6</b><span>languages</span></div>
    <div><b>4</b><span>tracks</span></div>
    <div><b>2</b><span>generators</span></div>
    <div><b>1</b><span>scene proving bpy → GLB → three.js</span></div>
  </div>
</div></header>''')

    # lead
    a('<div class="wrap"><div class="lead"><p class="lead-tag">the lead</p>')
    a(card(LEAD))
    a('</div></div>')

    # ---- multi-language panel ----
    a('''<section id="multilang"><div class="wrap">
  <div class="shead"><span class="num">01</span><h2>One scene, three languages</h2>
    <p>bpy → glb → three.js + glsl</p></div>
  <p class="lede" style="margin-top:-14px">This is the thing a single-language pipeline cannot show. Two hero props
    per scene are authored as <b>real Blender Python</b> in their own sub-workspace, with their own plan, their own
    agent session and their own eight-view review. The harness compiles each to <b>GLB</b>. A separate three.js
    session then loads those GLBs and composes them into a scene whose water, sky, sunbeams and particles are
    <b>hand-written GLSL</b>. Below: the assets alone, then the same assets inside the assembled frame.</p>

  <div class="pipe">
    <div class="step"><span class="n">01 · PYTHON</span><h5>Blender bpy</h5>
      <p>A per-asset agent session writes <code>bmesh</code> geometry and Principled BSDF materials in
        <code>_assets/&lt;name&gt;/src/model.py</code>, and reviews its own renders before finishing.</p></div>
    <div class="step"><span class="n">02 · BINARY</span><h5>GLB</h5>
      <p>The harness runs Blender headless, takes a census and exports
        <code>artifacts/object.glb</code>, then copies it to <code>public/assets/&lt;name&gt;.glb</code>.</p></div>
    <div class="step"><span class="n">03 · JAVASCRIPT</span><h5>three.js</h5>
      <p><code>src/scene.js</code> loads each GLB with <code>loaders.gltf.loadAsync</code> and the zone modules
        clone it into place beside procedural trees, bridges, benches and racks.</p></div>
    <div class="step"><span class="n">04 · GLSL</span><h5>Shaders</h5>
      <p>Raw vertex/fragment pairs in <code>src/shaders/</code> drive the pond, the sky and the volumetric
        sunbeams, each with a live <code>uTime</code> uniform the render rig samples at two times.</p></div>
  </div>

  <div class="assets">''')
    for asset in ML_ASSETS:
        a(asset_tile(asset))
    a('''</div>

  <div class="inscene">
    <figure><img src="''' + M + '''temple_censer.jpg" alt="censer in scene" loading="lazy">
      <figcaption><b>Blender in the frame</b>The BronzeCenser GLB, lit by its own charcoal glow inside the
        three.js temple courtyard. Pierced dome, ring handles and relief band all survive the trip through
        glTF — the best single detail frame in the wave.</figcaption></figure>
    <figure><img src="''' + M + '''temple_bridge.jpg" alt="pond shader" loading="lazy">
      <figcaption><b>GLSL in the frame</b>The custom pond <code>ShaderMaterial</code>
        (<code>src/shaders/water.js</code>, uniforms <code>uTime/uDeep/uShallow/uSunDir</code>) carrying the
        lantern flames as long warm reflection streaks, with three StoneLantern GLB instances along the path.
      </figcaption></figure>
    <figure><img src="''' + M + '''boat_stove.jpg" alt="stove in scene" loading="lazy">
      <figcaption><b>Both at once</b>The PotbellyStove GLB in the workshop corner — bellied cast iron from bpy,
        the flue and the hot door glow from three.js, and the hazy shafts from
        <code>VolumetricSunbeamShader</code>.</figcaption></figure>
  </div>

  <p class="caveatbox"><b>Verified, and one caveat.</b> Nothing in the harness enforces asset kind, so this was
    checked by hand rather than trusted. In the temple, both heroes are genuine end to end:
    <code>plan.json → assets[].kind = blender_glb</code>, the GLBs exist, and
    <code>src/assets/stone_lantern.js</code> is a loader that <i>throws</i> if the GLB is missing. In the boat
    workshop, PotbellyStove is genuine — but <b>ClinkerSkiff is not</b>: the GLB is authored, compiled and still
    loaded at <code>src/scene.js:12</code>, yet <code>src/zones/central_bay.js:59</code> calls a
    <i>procedural</i> three.js rebuild that a refine round wrote over the asset slot, so the hull you see in the
    workshop frames is JavaScript, not Blender. The standalone skiff card above is the real bpy asset. Filed as a
    defect; the temple is the clean demonstration.</p>
</div></section>''')

    def section(anchor, num, title, sub, cards, kind="object"):
        a(f'<section id="{anchor}"><div class="wrap"><div class="shead">'
          f'<span class="num">{num}</span><h2>{title}</h2><p>{sub}</p></div>')
        for c in cards:
            a(card(c, kind))
        a('</div></section>')

    section("static", "02", "Static objects", "blender · cadquery · three.js", STATIC)
    section("artic", "03", "Articulated", "urdf + blender · joints that move", ARTIC)
    section("scenes", "04", "Scenes", "three.js + glsl · authored cameras at two times", SCENES, "scene")
    section("gfx", "05", "Graphics", "glsl fragment shaders · python opengl", GFX)

    # footer
    a('''<footer id="notes"><div class="wrap">
  <div class="cols">
    <div>
      <h4>What is not on this page</h4>
      <ul>
        <li><b>Espresso portafilter (CadQuery)</b> — rated <i>no</i> and left out. Two attempts; the second is
          better geometry (the perforated basket resolves, the lugs became tabs) but the body is still flat
          white on stubby legs. The cause is structural: the CadQuery track's only material channel is
          <code>cq.Color(r,g,b)</code>, so "polished stainless" is unreachable by construction, and its texture
          pass produced zero textures. A third attempt would fail the same way.</li>
        <li><b>Ten superseded attempts</b> — the first radial engine, the first chandelier, the first clock, the
          first temple / workshop / alley, two extra rain windows, two extra auroras and a second accretion disc.
          Each is on disk; each lost to the version shown here, and the caption of the winner says why.</li>
        <li><b>mp4 turntables</b> — ffmpeg is not installed on this machine, so every motion clip here is an
          animated GIF via the PIL fallback. Nothing about the harness requires that.</li>
        <li><b>Scene textures</b> — tileable texture packs were generated for two scenes and could not be wired
          in without re-planning the passing run.</li>
      </ul>
    </div>
    <div>
      <h4>How to read the score</h4>
      <p>The number on each card is the harness judge (<code>gemini-3.1-pro-preview</code>, falling back to
        flash × 3), and on this page it is a <b>filter, not the point</b> — it decided whether a piece was worth
        looking at, and then a human looked. Two cards ship at a <i>lower</i> judged score than the run they
        replace, because the higher-scoring run did not contain the thing the prompt was chosen for. The temple
        courtyard is the sharpest case: the washed-out first attempt scored 0.729 and the correct-looking night
        scored 0.286.</p>
      <h4 style="margin-top:26px">Regenerate</h4>
      <pre>pip install -e harness
cd harness
3dcv bench run bench/prompts/teaser_v1_static.yaml \\
  --generator api-agent:gemini:gemini-3.7-flash
3dcv render &lt;slug&gt;          # 8 canonical views
3dcv texture pass &lt;slug&gt;     # text-to-image material pass
python -m http.server -d . 8931   # this page: /docs/teaser.html</pre>
      <p>Prompt sheets: <code>bench/prompts/teaser_v1_{static,articulated,scene,graphics}.yaml</code> —
        the sharpened re-runs are in the same files, suffixed <code>_v2</code> / <code>_v3</code>.
        Full method notes: <code>docs/TEASER.md</code>.</p>
    </div>
  </div>
</div></footer>

<div id="lb"><img alt=""></div>
<script>
// lazy panes: only fetch a tab's image when it is first shown (the GIFs are heavy)
function show(el){ if(el && el.dataset.src){ el.src = el.dataset.src; delete el.dataset.src; } }
document.querySelectorAll('.viewer').forEach(v=>{
  v.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>{
    v.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
    v.querySelectorAll('.pane').forEach(x=>x.classList.remove('on'));
    t.classList.add('on');
    const pane = document.getElementById(t.dataset.t);
    pane.classList.add('on'); show(pane.querySelector('img'));
  }));
});
// everything else: load when it scrolls near
const io = new IntersectionObserver((es)=>es.forEach(e=>{
  if(e.isIntersecting){ show(e.target); io.unobserve(e.target); }
}),{rootMargin:'400px'});
document.querySelectorAll('img[data-src]').forEach(i=>{
  if(!i.closest('.pane')) io.observe(i);
});
document.querySelectorAll('details').forEach(d=>d.addEventListener('toggle',()=>{
  if(d.open) d.querySelectorAll('img[data-src]').forEach(show);
}));
// lightbox
const lb = document.getElementById('lb'), lbi = lb.querySelector('img');
document.addEventListener('click',e=>{
  const t = e.target;
  if(t.tagName==='IMG' && t.closest('.pane,.shot,.asset-pics,.inscene,.sheetwrap')){
    lbi.src = t.currentSrc || t.src; lb.classList.add('on');
  } else if(t.closest('#lb')){ lb.classList.remove('on'); lbi.removeAttribute('src'); }
});
addEventListener('keydown',e=>{ if(e.key==='Escape'){ lb.classList.remove('on'); lbi.removeAttribute('src'); }});
</script>''')
    return "\n".join(p)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(build(), encoding="utf-8")
print(f"{OUT}  {OUT.stat().st_size/1024:.0f} KB")
