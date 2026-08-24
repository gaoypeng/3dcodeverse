"""The gallery's client-side behaviour — plain ES5-ish JS, no dependencies.

The page ships its run table as JSON in a ``<script type="application/json">``
block; filtering, sorting, the per-section counts and the whole summary strip are
recomputed from that array on every input event.  Nothing here fetches: a filter
change never reloads the page, and the URL is kept in sync with
``history.replaceState`` so a filtered view can be copied, bookmarked or curled.
"""

from __future__ import annotations

INDEX_JS = r"""
(function(){
var node=document.getElementById('gallery-data');
if(!node) return;
var DATA=JSON.parse(node.textContent);
var KEYS=['q','track','lang','tier','backend','pass','battery'];
var els={};
KEYS.concat(['sort']).forEach(function(k){els[k]=document.getElementById('f-'+k);});
var F={};

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

function summarize(rows){
  var scored=[],total=0,mins=0,judged=0,passed=0,broken=0;
  rows.forEach(function(r){
    if(typeof r.score==='number') scored.push(r.score);
    total+=r.cost||0; mins+=r.minutes||0;
    if(r.pass!=='na'){judged++; if(r.pass==='pass') passed++;}
    if(r.state!=='ok') broken++;
  });
  var mean=scored.length?scored.reduce(function(a,b){return a+b;},0)/scored.length:null;
  var srt=scored.slice().sort(function(a,b){return a-b;});
  var med=null;
  if(srt.length) med=srt.length%2?srt[(srt.length-1)/2]:(srt[srt.length/2-1]+srt[srt.length/2])/2;
  set('s-n', rows.length===DATA.length?String(rows.length):rows.length+' / '+DATA.length);
  set('s-pass', judged?Math.round(100*passed/judged)+'%  ('+passed+'/'+judged+')':'—');
  set('s-score', fmt(mean,3)+'  /  '+fmt(med,3));
  set('s-cost', '$'+total.toFixed(2));
  set('s-perpass', passed?'$'+(total/passed).toFixed(2):'—');
  set('s-time', mins>=90?(mins/60).toFixed(1)+' h':Math.round(mins)+' min');
  set('s-broken', String(broken));
}

function apply(){
  readControls();
  var keep={},rows=[];
  DATA.forEach(function(r){ if(match(r)){keep[r.key]=1; rows.push(r);} });
  var all=document.querySelectorAll('[data-run]');
  for(var i=0;i<all.length;i++){
    var hide=!keep[all[i].getAttribute('data-run')];
    all[i].classList.toggle('is-hidden',hide);
  }
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

KEYS.concat(['sort']).forEach(function(k){
  if(!els[k]) return;
  els[k].addEventListener('input',apply);
  els[k].addEventListener('change',apply);
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
document.addEventListener('keydown',function(e){
  if(e.key==='/' && document.activeElement!==els.q && els.q){ e.preventDefault(); els.q.focus(); }
});
apply();
})();
"""
