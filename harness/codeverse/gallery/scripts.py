"""The gallery's client-side behaviour — plain ES5-ish JS, no dependencies.

The page ships its run table as JSON in a ``<script type="application/json">``
block; filtering, sorting, the per-section counts, the status breakdown and the
bulk-selection bar are all recomputed from that array on every input event.
Nothing here fetches: a filter change never reloads the page, and the URL is kept
in sync with ``history.replaceState`` so a filtered view can be copied,
bookmarked or curled.

The bulk bar is deliberately read-only.  ``compare`` and ``export csv`` are links
to real server routes (so they work in a new tab, and without JS if you build the
query by hand); ``copy paths`` writes to the clipboard.  There is no "re-run"
because there is no route that would run anything — see ``gallery/compare.py``.
"""

from __future__ import annotations

INDEX_JS = r"""
(function(){
var node=document.getElementById('gallery-data');
if(!node) return;
var DATA=JSON.parse(node.textContent);
var KEYS=['q','track','lang','tier','backend','pass','verdict','battery'];
var BUCKETS=['passed','failed','unjudged','error'];
var els={};
KEYS.concat(['sort']).forEach(function(k){els[k]=document.getElementById('f-'+k);});
var F={};
var picked={};
var filters=document.getElementById('filters');

function readControls(){
  KEYS.forEach(function(k){F[k]=els[k]?String(els[k].value||'').trim():'';});
}
function match(r){
  if(F.q && r.text.indexOf(F.q.toLowerCase())<0) return false;
  if(F.track && r.track!==F.track) return false;
  if(F.lang && r.lang!==F.lang) return false;
  if(F.tier && r.tier!==F.tier) return false;
  if(F.backend && r.backend!==F.backend) return false;
  if(F.pass && r.pass!==F.pass) return false;
  if(F.verdict && r.verdict!==F.verdict) return false;
  if(F.battery && r.battery!==F.battery) return false;
  return true;
}
function num(v){return (v===null||v===undefined)?-1:Number(v);}
function cmp(a,b){
  var s=els.sort?els.sort.value:'score';
  if(s==='name') return a.slug<b.slug?-1:(a.slug>b.slug?1:0);
  var k=(s==='cost')?'cost':(s==='time'?'minutes':'score');
  var d=num(b[k])-num(a[k]);
  if(d) return d;
  return a.slug<b.slug?-1:1;
}
function fmt(v,d){return (v===null||v===undefined)?'—':Number(v).toFixed(d);}
function set(id,txt){var e=document.getElementById(id); if(e) e.textContent=txt;}

/* the strip's promise: the four buckets always add up to the runs on screen */
function summarize(rows){
  var scored=[],total=0,mins=0,passed=0;
  var b={passed:0,failed:0,unjudged:0,error:0};
  rows.forEach(function(r){
    if(typeof r.score==='number') scored.push(r.score);
    total+=r.cost||0; mins+=r.minutes||0;
    if(b[r.verdict]===undefined) b[r.verdict]=0;
    b[r.verdict]++;
    if(r.verdict==='passed') passed++;
  });
  var mean=scored.length?scored.reduce(function(a,c){return a+c;},0)/scored.length:null;
  var srt=scored.slice().sort(function(a,c){return a-c;});
  var med=null;
  if(srt.length) med=srt.length%2?srt[(srt.length-1)/2]:(srt[srt.length/2-1]+srt[srt.length/2])/2;
  var n=document.getElementById('s-n');
  if(n) n.innerHTML=rows.length===DATA.length?String(rows.length)
    :(rows.length+" <span class='faint'>of "+DATA.length+"</span>");
  BUCKETS.forEach(function(k){
    set('vc-'+k,String(b[k]));
    set('vp-'+k,(rows.length?Math.round(100*b[k]/rows.length):0)+'%');
    var seg=document.getElementById('vs-'+k);
    if(seg){ seg.style.flex=String(b[k]); seg.title=b[k]+' '+k; }
  });
  set('s-score', fmt(mean,3)+' / '+fmt(med,3));
  set('s-cost', '$'+total.toFixed(2));
  set('s-perpass', passed?'$'+(total/passed).toFixed(2):'—');
  set('s-time', mins>=90?(mins/60).toFixed(1)+' h':Math.round(mins)+' min');
}

function paintChips(){
  var chips=document.querySelectorAll('.vchip');
  for(var i=0;i<chips.length;i++)
    chips[i].setAttribute('aria-pressed', chips[i].getAttribute('data-verdict')===F.verdict?'true':'false');
  var badge=document.getElementById('f-count'), active=document.getElementById('f-active');
  var n=0;
  KEYS.forEach(function(k){ if(F[k]) n++; });
  if(badge){ badge.textContent=String(n); badge.classList.toggle('is-hidden',n===0); }
  /* the collapsed accordion still has to say what is being hidden */
  if(active){
    active.textContent='';
    var closed=filters && !filters.hasAttribute('open');
    KEYS.forEach(function(k){
      if(!F[k] || !closed) return;
      var chip=document.createElement('span');
      chip.className='tag';
      chip.textContent=(k==='q'?'search':k)+': '+F[k];
      active.appendChild(chip);
    });
  }
}

function apply(){
  readControls();
  var keep={},rows=[];
  DATA.forEach(function(r){ if(match(r)){keep[r.key]=1; rows.push(r);} });
  var all=document.querySelectorAll('[data-run]');
  for(var i=0;i<all.length;i++)
    all[i].classList.toggle('is-hidden',!keep[all[i].getAttribute('data-run')]);
  rows.sort(cmp);
  var order={};
  rows.forEach(function(r,i){order[r.key]=i;});
  var boxes=document.querySelectorAll('[data-items]');
  for(var b=0;b<boxes.length;b++){
    var kids=[].slice.call(boxes[b].children).filter(function(k){return k.getAttribute('data-run');});
    kids.sort(function(x,y){
      var a=order[x.getAttribute('data-run')], c=order[y.getAttribute('data-run')];
      return (a===undefined?1e9:a)-(c===undefined?1e9:c);
    });
    for(var k=0;k<kids.length;k++) boxes[b].appendChild(kids[k]);
  }
  var secs=document.querySelectorAll('section.battery');
  for(var s=0;s<secs.length;s++){
    var vis=[].slice.call(secs[s].querySelectorAll('.grid > [data-run]'))
      .filter(function(e){return !e.classList.contains('is-hidden');}).length;
    var c=secs[s].querySelector('[data-count]');
    if(c) c.textContent=vis+(vis===1?' run':' runs');
    secs[s].classList.toggle('is-hidden',vis===0);
  }
  var empty=document.getElementById('empty');
  if(empty) empty.classList.toggle('is-hidden',rows.length>0);
  summarize(rows);
  paintChips();
  syncUrl();
}

function syncUrl(){
  var p=new URLSearchParams();
  KEYS.forEach(function(k){ if(F[k]) p.set(k,F[k]); });
  if(els.sort && els.sort.value && els.sort.value!=='score') p.set('sort',els.sort.value);
  if(document.body.classList.contains('view-table')) p.set('view','table');
  var qs=p.toString();
  try{ history.replaceState(null,'',qs?('?'+qs):location.pathname); }catch(e){}
}

/* ------------------------------------------------------------------ selection */
function selKeys(){
  return DATA.filter(function(r){return picked[r.key];}).map(function(r){return r.key;});
}
function paintSelection(){
  var keys=selKeys(), bar=document.getElementById('selbar');
  set('sel-n',String(keys.length));
  if(bar) bar.classList.toggle('empty',keys.length===0);
  var hint=document.getElementById('sel-hint');
  if(hint) hint.classList.toggle('is-hidden',keys.length>0);
  var boxes=document.querySelectorAll('input.sel');
  for(var i=0;i<boxes.length;i++){
    var k=boxes[i].getAttribute('data-key'), on=!!picked[k];
    boxes[i].checked=on;
    var host=boxes[i].closest('[data-run]');
    if(host) host.classList.toggle('picked',on);
  }
  var qs='?runs='+encodeURIComponent(keys.join(','));
  var c=document.getElementById('sel-compare'); if(c) c.href='/compare'+qs;
  var x=document.getElementById('sel-csv'); if(x) x.href='/export.csv'+qs;
}
document.addEventListener('change',function(e){
  var t=e.target;
  if(!t || !t.classList || !t.classList.contains('sel')) return;
  var k=t.getAttribute('data-key');
  if(t.checked) picked[k]=1; else delete picked[k];
  paintSelection();
});
function on(id,fn){var e=document.getElementById(id); if(e) e.addEventListener('click',fn);}
on('sel-clear',function(){picked={};paintSelection();});
on('sel-all',function(){
  DATA.forEach(function(r){ if(match(r)) picked[r.key]=1; });
  paintSelection();
});
on('sel-paths',function(e){
  var btn=e.currentTarget;
  var paths=DATA.filter(function(r){return picked[r.key];}).map(function(r){return r.path;}).join('\n');
  var done=function(ok){ btn.textContent=ok?'copied':'copy failed';
    setTimeout(function(){btn.textContent='copy paths';},1400); };
  try{
    navigator.clipboard.writeText(paths).then(function(){done(true);},function(){done(false);});
  }catch(err){ done(false); }
});

/* ------------------------------------------------------------------ controls */
KEYS.concat(['sort']).forEach(function(k){
  if(!els[k]) return;
  els[k].addEventListener('input',apply);
  els[k].addEventListener('change',apply);
});
var chips=document.querySelectorAll('.vchip');
for(var i=0;i<chips.length;i++) chips[i].addEventListener('click',function(e){
  var want=e.currentTarget.getAttribute('data-verdict');
  if(els.verdict){ els.verdict.value=(els.verdict.value===want)?'':want; apply(); }
});
var reset=document.getElementById('f-reset');
if(reset) reset.addEventListener('click',function(){
  KEYS.forEach(function(k){ if(els[k]) els[k].value=''; });
  if(els.sort) els.sort.value='score';
  apply();
});
var vb=document.getElementById('view-btn');
if(vb) vb.addEventListener('click',function(){
  var on=document.body.classList.toggle('view-table');
  vb.setAttribute('aria-pressed',on?'true':'false');
  vb.textContent=on?'▤ table':'▦ cards';
  syncUrl();
});
/* a phone has no room for eight drop-downs above the first run */
if(filters){
  if(window.innerWidth<760) filters.removeAttribute('open');
  filters.addEventListener('toggle',paintChips);
}
/* one open pop-up at a time */
document.addEventListener('click',function(e){
  var open=document.querySelectorAll('details.menu[open],details.views[open]');
  for(var i=0;i<open.length;i++) if(!open[i].contains(e.target)) open[i].removeAttribute('open');
});
document.addEventListener('keydown',function(e){
  if(e.key==='/' && document.activeElement!==els.q && els.q){ e.preventDefault(); els.q.focus(); }
  if(e.key==='Escape'){
    var open=document.querySelectorAll('details.menu[open],details.views[open]');
    for(var i=0;i<open.length;i++) open[i].removeAttribute('open');
  }
});
apply();
paintSelection();
})();
"""
