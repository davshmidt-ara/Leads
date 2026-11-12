"""Interactive web app: search for leads, browse and filter them, edit drafts, record outcomes.

Served by server.py at /app (JSON API under /api/). Local tool: run it with
`python -m leadgen app <slug>` on your own computer.
"""
import csv
import io
import json
import os
import re
import threading
import time

from . import config, enrich, geo, outreach, scoring
from .db import STATUSES, Store, display_name
from .sources import overture

CAT_RE = re.compile(r"^[a-z0-9_*?]{1,60}$")


class ApiError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


class App:
    def __init__(self, slug):
        self.slug = slug
        self.cfg = config.load(slug)
        self.db = config.db_path(slug)
        self.lock = threading.Lock()
        self.job = {"running": False, "kind": "", "log": [], "error": None, "finished": 0}

    # ---- helpers -------------------------------------------------------------------------
    def store(self):
        return Store(self.db)

    def _view(self, lead, full=False):
        d = {k: lead[k] for k in ("id", "score", "segment", "status", "touches", "source", "name", "company",
                                  "title", "email", "phone", "website", "location", "url")}
        d.update(display=display_name(lead), tier=scoring.tier(lead["score"], self.cfg),
                 channel=outreach.channel_for(lead), due=outreach.is_due(lead, self.cfg),
                 has_draft=bool(lead["draft"]))
        if full:
            d.update(signal=lead["signal"], notes=lead["notes"], draft=lead["draft"] or "",
                     max_touches=outreach.max_touches(lead, self.cfg))
        return d

    def _search(self):
        sc = self.cfg["sources"].get("overture") or {}
        around = sc.get("around") or [None, None, 10000]
        return {"lat": around[0], "lon": around[1], "radius_km": round(around[2] / 1000, 1),
                "categories": sc.get("categories", []), "min_confidence": sc.get("min_confidence", 0.6),
                "skip_chains": bool(sc.get("skip_chains")), "per_category": sc.get("per_category", 150)}

    def _area(self, p):
        try:
            lat, lon, km = float(p["lat"]), float(p["lon"]), float(p["radius_km"])
        except (KeyError, TypeError, ValueError):
            raise ApiError("Enter a latitude, longitude and radius first.")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0.2 <= km <= 80):
            raise ApiError("Latitude must be -90..90, longitude -180..180 and radius 0.2-80 km.")
        return [round(lat, 5), round(lon, 5), int(km * 1000)]

    def _start(self, kind, fn):
        with self.lock:
            if self.job["running"]:
                raise ApiError("Another task is still running. Wait for it to finish.", 409)
            self.job = {"running": True, "kind": kind, "log": [], "error": None, "finished": 0}

        def run():
            try:
                fn(lambda m: self.job["log"].append(str(m)))
            except Exception as ex:  # shown to the user in the app
                self.job["error"] = str(ex)
                self.job["log"].append(f"Stopped: {ex}")
            finally:
                self.job["finished"] = time.time()
                self.job["running"] = False

        threading.Thread(target=run, daemon=True).start()
        return {"started": kind}

    # ---- GET -----------------------------------------------------------------------------
    def state(self, q):
        store = self.store()
        try:
            leads = store.all()
            tiers = {"hot": 0, "warm": 0, "cold": 0}
            for l in leads:
                tiers[scoring.tier(l["score"], self.cfg)] += 1
            agenda = {k: len(v) for k, v in outreach.agenda(store, self.cfg, limit=25).items()}
            return {"name": self.cfg["name"], "slug": self.slug, "total": len(leads), "tiers": tiers,
                    "with_email": sum(bool(l["email"]) for l in leads),
                    "by_status": store.stats()["by_status"], "agenda": agenda, "search": self._search(),
                    "segments": [s["name"] for s in self.cfg["icp"].get("segments", [])],
                    "statuses": STATUSES, "job": self.job, "offer": self.cfg["offer"].get("summary", "")}
        finally:
            store.close()

    def leads(self, q):
        g = lambda k: (q.get(k) or [""])[0]
        store = self.store()
        try:
            if g("view") == "today":
                groups = outreach.agenda(store, self.cfg, limit=25)
                return {"groups": [{"key": k, "leads": [self._view(l) for l in v]} for k, v in groups.items() if v]}
            rows = store.all()
        finally:
            store.close()
        text = g("q").lower()
        out = []
        for l in rows:
            if g("status") and l["status"] != g("status"):
                continue
            if g("segment") and (l["segment"] or "-") != g("segment"):
                continue
            if g("tier") and scoring.tier(l["score"], self.cfg) != g("tier"):
                continue
            if g("has") == "email" and not l["email"]:
                continue
            if g("has") == "phone" and not l["phone"]:
                continue
            if text and text not in " ".join(str(l[k] or "") for k in
                                              ("name", "company", "email", "location", "notes", "signal", "title")).lower():
                continue
            out.append(l)
        limit = min(int(g("limit") or 200), 1000)
        return {"total": len(out), "leads": [self._view(l) for l in out[:limit]]}

    def lead(self, q):
        store = self.store()
        try:
            lead = store.get(int((q.get("id") or ["0"])[0] or 0))
        finally:
            store.close()
        if not lead:
            raise ApiError("Lead not found.", 404)
        return self._view(lead, full=True)

    def categories(self, q):
        p = {k: (q.get(k) or [""])[0] for k in ("lat", "lon", "radius_km")}
        text = (q.get("q") or [""])[0].strip().lower()
        cats = overture.categories({"around": self._area(p)}, text, log=lambda m: None)
        return {"categories": [{"cat": c, "n": n, "email": m} for c, n, m in cats[:300]], "more": max(0, len(cats) - 300)}

    def locate(self, q):
        place = (q.get("q") or [""])[0].strip()
        if not place:
            raise ApiError("Type a place name.")
        try:
            return {"places": geo.locate(place)}
        except OSError as ex:
            raise ApiError(f"Place lookup is unavailable ({ex}). Enter latitude and longitude instead; "
                           "you can copy them from any map app.", 502)

    def report(self, q):
        store = self.store()
        try:
            return {"source": store.report("source"), "segment": store.report("segment")}
        finally:
            store.close()

    def export_csv(self, q):
        store = self.store()
        try:
            leads = store.all()
        finally:
            store.close()
        cols = ["id", "score", "tier", "segment", "status", "touches", "source", "channel", "first_name", "name",
                "company", "title", "email", "phone", "website", "location", "url", "signal", "notes", "draft"]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for l in leads:
            l.update(tier=scoring.tier(l["score"], self.cfg), channel=outreach.channel_for(l),
                     first_name=outreach.first_name(l))
            w.writerow(l)
        return buf.getvalue()

    # ---- POST ----------------------------------------------------------------------------
    def find(self, p):
        around = self._area(p)
        cats = [str(c).strip().lower() for c in p.get("categories") or []]
        if not cats or any(not CAT_RE.match(c) for c in cats) or len(cats) > 60:
            raise ApiError("Pick between 1 and 60 business types.")
        try:
            conf = min(1.0, max(0.0, float(p.get("min_confidence", 0.6))))
            per_cat = min(1000, max(1, int(p.get("per_category", 150))))
        except (TypeError, ValueError):
            raise ApiError("Confidence and per-type limit must be numbers.")
        path = os.path.join(config.business_dir(self.slug), "business.json")
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        sc = raw.setdefault("sources", {}).setdefault("overture", {})
        sc.update(around=around, categories=cats, min_confidence=conf, skip_chains=bool(p.get("skip_chains")),
                  per_category=per_cat)
        sc.setdefault("limit", 1500)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)
            f.write("\n")
        self.cfg = config.load(self.slug)
        sc = self.cfg["sources"]["overture"]

        def work(log):
            store = self.store()
            try:
                new = total = 0
                for lead in overture.collect(self.cfg, sc, log=log):
                    total += 1
                    new += store.add("overture", lead)
                log(f"{total} businesses matched, {new} new leads added ({total - new} already in your list).")
                scoring.score_all(store, self.cfg)
                n = outreach.draft_all(store, self.cfg, min_score=self.cfg["scoring"]["warm"])
                log(f"Scored everything and wrote {n} outreach drafts.")
            finally:
                store.close()

        return self._start("find", work)

    def enrich(self, p):
        limit = min(100, max(1, int(p.get("limit", 25))))

        def work(log):
            store = self.store()
            try:
                log(f"Checking up to {limit} websites for a contact email...")
                checked, found = enrich.enrich_all(store, self.cfg["scoring"]["warm"], limit, log=log)
                scoring.score_all(store, self.cfg)
                outreach.draft_all(store, self.cfg, min_score=self.cfg["scoring"]["warm"])
                log(f"{checked} websites checked, {found} emails found.")
            finally:
                store.close()

        return self._start("enrich", work)

    def mark(self, p):
        status, ids = p.get("status"), p.get("ids") or []
        if status not in STATUSES or not ids or len(ids) > 500:
            raise ApiError("Unknown status or no leads selected.")
        store = self.store()
        try:
            done = sum(store.mark(int(i), status) for i in ids)
            outreach.draft_all(store, self.cfg, min_score=self.cfg["scoring"]["warm"])
        finally:
            store.close()
        return {"updated": done}

    def draft(self, p):
        store = self.store()
        try:
            lead = store.get(int(p.get("id") or 0))
            if not lead:
                raise ApiError("Lead not found.", 404)
            text = p.get("text")
            if text is None:                       # regenerate from the template
                text = outreach.draft(lead, self.cfg)
            store.update(lead["id"], draft=str(text)[:10000])
            return {"draft": str(text)[:10000]}
        finally:
            store.close()

    GET = {"state": state, "leads": leads, "lead": lead, "categories": categories, "locate": locate, "report": report}
    POST = {"find": find, "enrich": enrich, "mark": mark, "draft": draft}


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Lead Finder</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--mut:#5b6472;--line:#dfe3ea;--acc:#1f6feb;--acc-ink:#fff;
--hot:#c0362c;--warm:#a15c00;--cold:#5b6472;--ok:#1a7f4b;--chip:#eef1f6}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b22;--ink:#e8eaee;--mut:#9aa3b2;--line:#2a2f3a;
--acc:#4c8dff;--acc-ink:#fff;--hot:#ff7b72;--warm:#e3a341;--cold:#9aa3b2;--ok:#4ac17f;--chip:#232833}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{padding:14px 16px 0;max-width:1100px;margin:auto}h1{font-size:20px;margin:0}h2{font-size:16px;margin:0 0 10px}
.sub{color:var(--mut);font-size:13px}.wrap{max-width:1100px;margin:auto;padding:12px 16px 80px}
.stats{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 12px;min-width:92px}
.stat b{display:block;font-size:20px}.stat span{color:var(--mut);font-size:12px}
nav{display:flex;gap:4px;border-bottom:1px solid var(--line);overflow-x:auto}
nav button{background:none;border:0;border-bottom:3px solid transparent;color:var(--mut);padding:10px 12px;font:inherit;cursor:pointer;white-space:nowrap}
nav button.on{color:var(--ink);border-color:var(--acc);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin-top:12px}
label{display:block;font-size:12px;color:var(--mut);margin-bottom:3px}
input,select,textarea{font:inherit;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;width:100%}
textarea{min-height:230px;font:13px/1.5 ui-monospace,Menlo,Consolas,monospace}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}.row>div{flex:1 1 130px}
button.b,a.b{white-space:nowrap}button.b{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:8px 12px;cursor:pointer}
button.b:hover{border-color:var(--acc)}button.p{background:var(--acc);border-color:var(--acc);color:var(--acc-ink);font-weight:600}
button:disabled{opacity:.5;cursor:default}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;max-height:230px;overflow:auto}
.chip{background:var(--chip);border:1px solid transparent;border-radius:999px;padding:4px 10px;font-size:13px;cursor:pointer;user-select:none}
.chip.on{background:var(--acc);color:var(--acc-ink)}.chip small{opacity:.7}
.log{font:12px/1.5 ui-monospace,Menlo,monospace;color:var(--mut);white-space:pre-wrap;margin-top:8px}
table{width:100%;border-collapse:collapse}th{font-size:12px;color:var(--mut);text-align:left;font-weight:600;padding:6px 8px}
td{padding:8px;border-top:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}tbody tr{cursor:pointer}tbody tr:hover{background:var(--chip)}
.score{font-weight:700}.hot{color:var(--hot)}.warm{color:var(--warm)}.cold{color:var(--cold)}
.tag{display:inline-block;background:var(--chip);border-radius:6px;padding:1px 7px;font-size:12px;color:var(--mut);margin-right:4px}
.tag.due{background:var(--warm);color:#fff}.msg{padding:10px 12px;border-radius:8px;background:var(--chip);margin-top:10px;font-size:14px}
.err{background:#c0362c22;color:var(--hot)}
#drawer{position:fixed;top:0;right:0;bottom:0;width:min(520px,100%);background:var(--card);border-left:1px solid var(--line);
padding:16px;overflow:auto;transform:translateX(105%);transition:transform .18s;z-index:5}#drawer.on{transform:none}
#shade{position:fixed;inset:0;background:#0006;display:none;z-index:4}#shade.on{display:block}
.kv{display:grid;grid-template-columns:84px 1fr;gap:4px 10px;font-size:14px;margin:10px 0}.kv span{color:var(--mut)}
.acts{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}a{color:var(--acc)}
.grp{font-size:12px;font-weight:700;letter-spacing:.04em;color:var(--mut);text-transform:uppercase;margin:16px 0 4px}
@media(max-width:700px){.hide-s{display:none}td,th{padding:7px 5px}}
</style></head><body>
<header><h1 id="biz">Lead Finder</h1><div class="sub" id="offer"></div>
<div class="stats" id="stats"></div>
<nav id="tabs"><button data-t="find" class="on">Find leads</button><button data-t="today">Today</button>
<button data-t="leads">All leads</button><button data-t="results">Results</button></nav></header>
<div class="wrap">

<section id="find">
 <div class="card"><h2>1. Where</h2>
  <div class="row"><div style="flex:2 1 220px"><label for="place">Look up a place (optional)</label><input id="place" placeholder="e.g. Austin, Texas"></div>
   <div style="flex:0"><button class="b" id="locate">Look up</button></div></div>
  <div id="places"></div>
  <div class="row" style="margin-top:10px"><div><label for="lat">Latitude</label><input id="lat" inputmode="decimal"></div>
   <div><label for="lon">Longitude</label><input id="lon" inputmode="decimal"></div>
   <div><label for="km">Radius (km)</label><input id="km" inputmode="decimal"></div></div></div>
 <div class="card"><h2>2. What kind of business</h2>
  <div class="row"><div style="flex:2 1 220px"><label for="catq">Filter business types</label><input id="catq" placeholder="e.g. cafe, dentist, gym"></div>
   <div style="flex:0"><button class="b" id="loadcats">Show types in this area</button></div></div>
  <div class="sub" id="cathint" style="margin-top:8px">Selected types are searched in the order you pick them.</div>
  <div class="chips" id="picked"></div><div class="chips" id="cats"></div></div>
 <div class="card"><h2>3. Search</h2>
  <div class="row"><div><label for="conf">Minimum data confidence</label><select id="conf"><option value="0.5">0.5 - more results</option>
   <option value="0.6">0.6</option><option value="0.7">0.7 - balanced</option><option value="0.85">0.85 - most reliable</option></select></div>
   <div><label for="percat">Max leads per type</label><input id="percat" inputmode="numeric"></div>
   <div><label for="chains">Chains</label><select id="chains"><option value="0">Keep (one lead per chain)</option><option value="1">Skip chains</option></select></div>
   <div style="flex:0"><button class="b p" id="go">Find leads</button></div></div>
  <div class="row" style="margin-top:10px"><div class="sub">Many businesses list a website but no email.</div>
   <div style="flex:0"><button class="b" id="enrich">Find missing emails (25 sites)</button></div></div>
  <div class="msg" id="findmsg" hidden></div><div class="log" id="log"></div></div>
</section>

<section id="today" hidden><div id="todaybody"></div></section>

<section id="leads" hidden>
 <div class="card"><div class="row">
  <div style="flex:2 1 200px"><label for="q">Search</label><input id="q" placeholder="name, email, address, notes"></div>
  <div><label for="ftier">Tier</label><select id="ftier"><option value="">All</option><option>hot</option><option>warm</option><option>cold</option></select></div>
  <div><label for="fseg">Segment</label><select id="fseg"></select></div>
  <div><label for="fstatus">Status</label><select id="fstatus"></select></div>
  <div><label for="fhas">Has</label><select id="fhas"><option value="">Anything</option><option value="email">Email</option><option value="phone">Phone</option></select></div>
  <div style="flex:0"><a class="b" id="csv" style="text-decoration:none;display:inline-block;border:1px solid var(--line);border-radius:8px;padding:8px 12px">Download CSV</a></div>
 </div><div class="sub" id="count" style="margin-top:8px"></div></div>
 <div class="card" style="padding:4px 6px"><table><thead><tr><th>Score</th><th>Business</th><th>Contact</th><th class="hide-s">Segment</th><th>Status</th></tr></thead><tbody id="rows"></tbody></table></div>
</section>

<section id="results" hidden><div class="card"><h2>By source</h2><div id="rsource"></div></div>
 <div class="card"><h2>By segment</h2><div id="rsegment"></div></div>
 <div class="sub" style="margin-top:10px">Reply rate = leads that replied, out of leads you marked contacted. Raise the weight of segments that reply.</div></section>
</div>

<div id="shade"></div><aside id="drawer" aria-label="Lead details"></aside>
<script>
const TOKEN=new URLSearchParams(location.search).get('token')||'';
const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let S=null, picked=[], allCats=[], pollT=null;
async function api(path,body){
  const opt={headers:{'X-Token':TOKEN,'X-Leadgen':'1'}};
  if(body!==undefined){opt.method='POST';opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(body)}
  const r=await fetch('/api/'+path,opt); const j=await r.json().catch(()=>({error:'Unexpected response'}));
  if(!r.ok) throw new Error(j.error||('Error '+r.status)); return j;
}
function msg(el,text,err){el.hidden=!text;el.textContent=text||'';el.className='msg'+(err?' err':'')}
const TITLES={reply_now:'Replied - answer these first',inbound:'Inbound - they asked, respond today',followups_due:'Follow-ups due',
 first_touch:'First contact - best new leads',close_out:'No reply after all touches - mark lost'};

async function refresh(){
  S=await api('state'); $('biz').textContent=S.name+' - Lead Finder'; $('offer').textContent=S.offer;
  const st=[['Leads',S.total],['Hot',S.tiers.hot],['Warm',S.tiers.warm],['With email',S.with_email],
   ['To do today',Object.values(S.agenda).reduce((a,b)=>a+b,0)],['Replied',(S.by_status.replied||0)+(S.by_status.qualified||0)+(S.by_status.won||0)],['Won',S.by_status.won||0]];
  $('stats').innerHTML=st.map(([k,v])=>`<div class="stat"><b>${v}</b><span>${k}</span></div>`).join('');
  if(!$('fseg').options.length){
    $('fseg').innerHTML='<option value="">All</option>'+S.segments.map(s=>`<option>${esc(s)}</option>`).join('')+'<option value="-">(none)</option>';
    $('fstatus').innerHTML='<option value="">All</option>'+S.statuses.map(s=>`<option>${esc(s)}</option>`).join('');
    const s=S.search; $('lat').value=s.lat??''; $('lon').value=s.lon??''; $('km').value=s.radius_km; picked=[...s.categories];
    $('conf').value=String(s.min_confidence); if(!$('conf').value) $('conf').value='0.6';
    $('percat').value=s.per_category; $('chains').value=s.skip_chains?'1':'0'; drawPicked();
  }
  showJob(S.job);
}
function showJob(j){
  $('log').textContent=(j.log||[]).join('\n');
  const busy=j.running; $('go').disabled=$('enrich').disabled=busy; $('go').textContent=busy&&j.kind==='find'?'Searching...':'Find leads';
  if(busy&&!pollT) pollT=setInterval(async()=>{const s=await api('state');S=s;showJob(s.job);if(!s.job.running){clearInterval(pollT);pollT=null;refresh();
     msg($('findmsg'),s.job.error?('Stopped: '+s.job.error):'Done. Open "Today" or "All leads" to work them.',!!s.job.error)}},1500);
}
function drawPicked(){
  $('picked').innerHTML=picked.map(c=>`<span class="chip on" data-c="${esc(c)}">${esc(c.replace(/_/g,' '))} &times;</span>`).join('');
  drawCats();
}
function drawCats(){
  const f=$('catq').value.trim().toLowerCase().replace(/\s+/g,'_');
  $('cats').innerHTML=allCats.filter(c=>!picked.includes(c.cat)&&c.cat.includes(f)).slice(0,120)
   .map(c=>`<span class="chip" data-c="${esc(c.cat)}">${esc(c.cat.replace(/_/g,' '))} <small>${c.n} / ${c.email} with email</small></span>`).join('');
}
$('picked').onclick=e=>{const c=e.target.closest('.chip');if(c){picked=picked.filter(x=>x!==c.dataset.c);drawPicked()}};
$('cats').onclick=e=>{const c=e.target.closest('.chip');if(c){picked.push(c.dataset.c);drawPicked()}};
$('catq').oninput=drawCats;
const area=()=>({lat:$('lat').value,lon:$('lon').value,radius_km:$('km').value});
$('loadcats').onclick=async()=>{
  const b=$('loadcats');b.disabled=true;b.textContent='Loading (first time for an area takes ~30s)...';msg($('findmsg'),'');
  try{const a=area();const r=await api('categories?'+new URLSearchParams(a));allCats=r.categories;drawCats();
      $('cathint').textContent=`${allCats.length}${r.more?'+':''} business types found here. Click to select; selected types are searched in the order you pick them.`}
  catch(e){msg($('findmsg'),e.message,true)} b.disabled=false;b.textContent='Show types in this area';
};
$('locate').onclick=async()=>{
  $('places').innerHTML='';try{const r=await api('locate?q='+encodeURIComponent($('place').value));
   $('places').innerHTML='<div class="chips">'+(r.places.length?r.places.map(p=>`<span class="chip" data-lat="${p.lat}" data-lon="${p.lon}">${esc(p.name)}</span>`).join(''):'No match.')+'</div>'}
  catch(e){$('places').innerHTML=`<div class="msg err">${esc(e.message)}</div>`}
};
$('places').onclick=e=>{const c=e.target.closest('.chip');if(c&&c.dataset.lat){$('lat').value=c.dataset.lat;$('lon').value=c.dataset.lon;allCats=[];drawCats()}};
$('go').onclick=async()=>{
  msg($('findmsg'),'');try{await api('find',{...area(),categories:picked,min_confidence:$('conf').value,per_category:$('percat').value,skip_chains:$('chains').value==='1'});
   showJob({running:true,kind:'find',log:['Starting...']})}catch(e){msg($('findmsg'),e.message,true)}
};
$('enrich').onclick=async()=>{msg($('findmsg'),'');try{await api('enrich',{limit:25});showJob({running:true,kind:'enrich',log:['Starting...']})}catch(e){msg($('findmsg'),e.message,true)}};

function rowHtml(l){
  const contact=[l.email,l.phone].filter(Boolean).map(esc).join('<br>')||(l.website?esc(l.website.replace(/^https?:\/\//,'').slice(0,40)):'<span class="sub">no contact yet</span>');
  return `<tr data-id="${l.id}"><td><span class="score ${l.tier}">${l.score}</span></td><td><b>${esc(l.display)}</b><div class="sub">${esc(l.title||'')} ${esc(l.location||'')}</div></td>
   <td>${contact}</td><td class="hide-s">${l.segment?`<span class="tag">${esc(l.segment)}</span>`:''}</td>
   <td><span class="tag">${esc(l.status)}</span>${l.due?'<span class="tag due">follow-up due</span>':''}${l.touches?`<div class="sub">${l.touches} sent</div>`:''}</td></tr>`;
}
async function loadLeads(){
  const p=new URLSearchParams({q:$('q').value,tier:$('ftier').value,segment:$('fseg').value,status:$('fstatus').value,has:$('fhas').value});
  const r=await api('leads?'+p); $('rows').innerHTML=r.leads.map(rowHtml).join('')||'<tr><td colspan=5 class="sub">No leads match. Use "Find leads" to add some.</td></tr>';
  $('count').textContent=`${r.total} leads`+(r.total>r.leads.length?` (showing the top ${r.leads.length})`:'');
  $('csv').href='/api/export.csv?token='+encodeURIComponent(TOKEN);
}
async function loadToday(){
  const r=await api('leads?view=today');
  $('todaybody').innerHTML=r.groups.length?r.groups.map(g=>`<div class="grp">${TITLES[g.key]} (${g.leads.length})</div><div class="card" style="padding:4px 6px"><table><tbody>${g.leads.map(rowHtml).join('')}</tbody></table></div>`).join('')
   :'<div class="card">Nothing to do yet. Use "Find leads" first.</div>';
}
async function loadResults(){
  const r=await api('report'); const t=rows=>rows.length?`<table><thead><tr><th>Group</th><th>Leads</th><th>With email</th><th>Contacted</th><th>Replied</th><th>Won</th><th>Reply rate</th></tr></thead><tbody>${rows.map(x=>
   `<tr style="cursor:default"><td>${esc(x.grp)}</td><td>${x.leads}</td><td>${x.with_email}</td><td>${x.contacted}</td><td>${x.replied}</td><td>${x.won}</td><td>${x.reply_rate}</td></tr>`).join('')}</tbody></table>`:'<span class="sub">No leads yet.</span>';
  $('rsource').innerHTML=t(r.source); $('rsegment').innerHTML=t(r.segment);
}
let tq=null;['q','ftier','fseg','fstatus','fhas'].forEach(i=>$(i).oninput=()=>{clearTimeout(tq);tq=setTimeout(loadLeads,200)});
let tab='find';
function show(t){tab=t;document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('on',b.dataset.t===t));
  ['find','today','leads','results'].forEach(s=>$(s).hidden=s!==t); if(t==='leads')loadLeads(); if(t==='today')loadToday(); if(t==='results')loadResults()}
$('tabs').onclick=e=>{if(e.target.dataset.t)show(e.target.dataset.t)};
document.body.addEventListener('click',e=>{const tr=e.target.closest('tr[data-id]');if(tr)openLead(tr.dataset.id)});

function mailto(l){const m=l.draft.match(/^Subject:\s*(.*)\n+([\s\S]*)$/);const sub=m?m[1]:'',body=m?m[2]:l.draft;
  return 'mailto:'+encodeURIComponent(l.email)+'?subject='+encodeURIComponent(sub)+'&body='+encodeURIComponent(body)}
async function openLead(id){
  const l=await api('lead?id='+id), d=$('drawer');
  const kv=[['Score',`<span class="score ${l.tier}">${l.score}</span> ${l.tier}${l.segment?' - '+esc(l.segment):''}`],['Email',esc(l.email||'-')],
   ['Phone',l.phone?`<a href="tel:${esc(l.phone)}">${esc(l.phone)}</a>`:'-'],['Website',/^https?:\/\//.test(l.website||'')?`<a href="${esc(l.website)}" target="_blank" rel="noreferrer noopener">${esc(l.website)}</a>`:esc(l.website||'-')],
   ['Address',esc(l.location||'-')],['Why',esc(l.signal||'-')],['Notes',esc(l.notes||'-')],['Status',`${esc(l.status)} - ${l.touches} of ${l.max_touches} messages sent`]];
  d.innerHTML=`<div style="display:flex;justify-content:space-between;gap:8px"><h2>${esc(l.display)}</h2><button class="b" id="dx">Close</button></div>
   <div class="kv">${kv.map(([k,v])=>`<span>${k}</span><div>${v}</div>`).join('')}</div>
   ${/^https?:\/\//.test(l.url||'')?`<a href="${esc(l.url)}" target="_blank" rel="noreferrer noopener">Open on map</a>`:''}
   <h2 style="margin-top:14px">${l.touches?'Follow-up':'First'} message (${esc(l.channel)})</h2>
   <textarea id="dtext" aria-label="Message draft">${esc(l.draft)}</textarea>
   <div class="acts"><button class="b" id="dsave">Save edits</button><button class="b" id="dregen">Rewrite from template</button><button class="b" id="dcopy">Copy</button>
    ${l.email?`<a class="b" id="dmail" style="text-decoration:none;border:1px solid var(--line);border-radius:8px;padding:8px 12px" href="#">Open in my email app</a>`:''}</div>
   <div class="sub" style="margin-top:6px">Nothing is sent from here. Send it yourself, then record what happened:</div>
   <div class="acts"><button class="b p" data-s="contacted">I sent it</button><button class="b" data-s="replied">They replied</button><button class="b" data-s="qualified">Qualified</button>
    <button class="b" data-s="won">Won</button><button class="b" data-s="lost">Lost</button><button class="b" data-s="do_not_contact">Do not contact</button></div><div class="msg" id="dmsg" hidden></div>`;
  d.classList.add('on');$('shade').classList.add('on');
  $('dx').onclick=closeLead;
  $('dsave').onclick=async()=>{await api('draft',{id:l.id,text:$('dtext').value});msg($('dmsg'),'Saved.')};
  $('dregen').onclick=async()=>{const r=await api('draft',{id:l.id});$('dtext').value=r.draft;msg($('dmsg'),'Rewritten from the template.')};
  $('dcopy').onclick=async()=>{try{await navigator.clipboard.writeText($('dtext').value);msg($('dmsg'),'Copied.')}catch(e){$('dtext').select();msg($('dmsg'),'Press Ctrl/Cmd+C to copy.')}};
  if($('dmail'))$('dmail').onclick=e=>{e.currentTarget.href=mailto({...l,draft:$('dtext').value})};
  d.querySelectorAll('[data-s]').forEach(b=>b.onclick=async()=>{await api('mark',{ids:[l.id],status:b.dataset.s});closeLead();await refresh();show(tab)});
}
function closeLead(){$('drawer').classList.remove('on');$('shade').classList.remove('on')}
$('shade').onclick=closeLead;document.addEventListener('keydown',e=>{if(e.key==='Escape')closeLead()});
refresh().catch(e=>{document.querySelector('.wrap').innerHTML=`<div class="msg err">${esc(e.message)}</div>`});
</script></body></html>"""
