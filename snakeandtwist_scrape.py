#!/usr/bin/env python3
"""Snake & Twist Pilates (Arketa) — chiffres EXACTS.

Plateforme : Arketa (app.arketa.co). API publique trouvée via Playwright :

  GET https://app.arketa.co/api/widget/data?widgetName=snakeandtwist&type=classes&start_time=<UNIX>

Chaque appel = 1 SEMAINE (start_time aligné sur lundi 00:00 UTC, +604800 par
semaine). Champs par cours : `id`, `name`/`class_name`, `start_time`,
`duration`, `max_capacity`, `total_booked`, `waitlistLength`, `instructor_name`,
`location` (dict), `canceled`, `deleted`, `hidden`, `experience_type`.

CORS ouvert (access-control-allow-origin: *). On exclut les rendez-vous
privés (experience_type=Appointment) qui ne reflètent pas la fréquentation
d'un cours collectif.
"""
import csv
import dashboard_meta
from template_common import meta_panel_html
import datetime as dt
import json
import os
import safestore
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
API = "https://app.arketa.co/api/widget/data"
WIDGET = "snakeandtwist"
STORE = "snakeandtwist_data.json"
HTML = "snakeandtwist.html"
CSV = "snakeandtwist_seances.csv"
BRAND = "SNAKE & TWIST"
ACCENT = "#3a7d44"
ACCENT2 = "#7fbd8a"
PRICE = 22
FIELDS = ["date", "jour", "heure", "fin", "lieu", "cours", "coach",
          "capacite", "presents", "finie", "releve"]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"


def _get(start_ts, retries=4):
    url = f"{API}?widgetName={WIDGET}&type=classes&start_time={start_ts}"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"echec {url}: {last}")


def week_starts(now, past_weeks=3, future_weeks=2):
    """Liste de start_time UNIX (UTC) couvrant past_weeks .. future_weeks."""
    today_utc = dt.datetime(now.year, now.month, now.day, tzinfo=dt.timezone.utc)
    # Arketa cale ses semaines sur des bornes 604800s alignées sur l'epoch
    ts = int(today_utc.timestamp()) // 604800 * 604800
    return [ts + 604800 * i for i in range(-past_weeks, future_weeks + 1)]


def capture():
    now = dt.datetime.now(PARIS)
    store = safestore.load(STORE)
    seen = 0
    for ts in week_starts(now):
        try:
            d = _get(ts)
        except Exception as e:  # noqa: BLE001
            print(f"  (semaine {ts} échouée : {e})", file=sys.stderr)
            continue
        for c in d.get("data", {}).get("classes", []):
            if c.get("canceled") or c.get("deleted") or c.get("hidden"):
                continue
            if c.get("experience_type") == "Appointment":
                continue  # rendez-vous privés -> hors stat de fréquentation
            cap = c.get("max_capacity") or 0
            if not cap or cap >= 999:
                continue  # cap=0 ou 9999 (livestream illimité) -> hors stats
            sid = str(c.get("id") or "")
            if not sid:
                continue
            try:
                sdt = dt.datetime.fromtimestamp(int(c.get("start_time") or 0), PARIS)
            except (ValueError, OSError):
                continue
            edt = sdt + dt.timedelta(minutes=int(c.get("duration") or 0))
            loc = c.get("location") or {}
            lieu = (loc.get("name") if isinstance(loc, dict) else "") or "Snake & Twist"
            store[sid] = {
                "id": sid,
                "date": sdt.strftime("%Y-%m-%d"),
                "jour": JOURS_FR[sdt.weekday()],
                "heure": sdt.strftime("%H:%M"),
                "fin": edt.strftime("%H:%M"),
                "lieu": lieu,
                "cours": (c.get("name") or c.get("class_name") or "").strip(),
                "coach": (c.get("instructor_name") or c.get("host_name") or "").strip(),
                "capacite": cap,
                "presents": c.get("total_booked") or 0,
                "finie": now >= edt,
                "releve": now.strftime("%Y-%m-%d %H:%M"),
            }
            seen += 1
    safestore.save(store, STORE)
    fin = sum(1 for v in store.values() if v.get("finie"))
    print(f"[snakeandtwist] {now:%Y-%m-%d %H:%M} : {seen} cours captés, {len(store)} en base ({fin} terminés).")
    return store


def write_csv(rows):
    with open(CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"-> {CSV}")


def write_html(rows):
    chartjs = ""
    vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor_chartjs.min.js")
    if os.path.exists(vendor):
        with open(vendor, encoding="utf-8") as f:
            chartjs = f.read()
    last_iso = ""
    _releves = [r.get("releve") or "" for r in (rows if isinstance(rows,list) else rows.values()) if isinstance(r, dict)]
    _last = max(_releves) if _releves else ""
    if _last:
        try:
            last_iso = dt.datetime.strptime(_last[:16], "%Y-%m-%d %H:%M").replace(tzinfo=PARIS).isoformat()
        except ValueError:
            pass
    _n_rows = len(rows) if isinstance(rows, (list, dict)) else 0
    _m = dashboard_meta.get("snakeandtwist")
    _meta_html = meta_panel_html(_m["method"], _m["risk"], _m["freq"], last_iso, _n_rows)

    html = (TPL.replace("__CHARTJS__", chartjs)
              .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
              .replace("__GENERATED__", dt.datetime.now(PARIS).strftime("%d/%m/%Y %H:%M")).replace("__META_PANEL__", _meta_html)
              .replace("__ACCENT__", ACCENT).replace("__ACCENT2__", ACCENT2)
              .replace("__BRAND__", BRAND))
    with open(HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"-> {HTML}")


TPL = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__BRAND__ - Fréquentation</title>
<script>__CHARTJS__</script>
<style>
  :root{--bg:#0c1a14;--card:#142a20;--card2:#1c3a2c;--line:#2a4a3a;--text:#eef9f1;
        --muted:#9ac0a8;--accent:__ACCENT__;--accent2:__ACCENT2__;--green:#5fcf8a;--yellow:#e6c14d;--red:#e07a6f;}
  *{box-sizing:border-box;}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);}
  header{padding:26px 30px 8px;}h1{margin:0;font-size:24px;font-weight:800;}
  .sub{color:var(--muted);font-size:13px;margin-top:6px;}
  .wrap{padding:0 30px 48px;max-width:1180px;margin:0 auto;}
  .note{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px 18px;margin:16px 0;color:var(--muted);font-size:13px;}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:16px;margin:18px 0;}
  .kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;}
  .kpi .v{font-size:26px;font-weight:800;color:var(--accent2);}
  .kpi .l{color:var(--muted);font-size:11.5px;margin-top:4px;text-transform:uppercase;letter-spacing:.5px;}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:20px;}
  .panel h2{margin:0 0 14px;font-size:14px;color:var(--accent2);font-weight:700;}
  canvas{max-height:260px;}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap;}
  th{color:var(--muted);font-weight:600;}
  tbody tr:hover{background:var(--card2);}
  .tablewrap{max-height:600px;overflow:auto;border:1px solid var(--line);border-radius:14px;}
  .fillbar{display:inline-block;height:8px;border-radius:5px;vertical-align:middle;margin-left:6px;}
  @media(max-width:600px){canvas{max-height:200px!important}th,td{padding:7px 6px;font-size:12px}.panel{padding:15px 14px}.kpi .v{font-size:20px}.note{font-size:12px}}
</style></head>
<body>
<header><h1>SNAKE &amp; TWIST &middot; Fréquentation</h1>
<div class="sub">généré le __GENERATED__ &middot; chiffres exacts (Arketa)</div></header>
<div class="wrap">
  __META_PANEL__
<div class="note">ℹ️ <b>Snake & Twist &middot; plateforme Arketa.</b> Présents = `total_booked` exact, capacité = `max_capacity`. Les rendez-vous privés (Appointment) sont exclus pour ne garder que les cours collectifs. Fenêtre courante : ~5 semaines. MAJ 30 min via robot.</div>
<div class="kpis" id="kpis"></div>
<div class="panel"><h2>Présents par jour</h2><canvas id="cDay"></canvas></div>

<div class="panel">
  <h2>📅 Heatmap jour &times; heure <span style="font-size:11px;color:var(--muted);font-weight:400;text-transform:none;letter-spacing:0">(moyenne présents/séance, pondérée)</span></h2>
  <p style="color:var(--muted);font-size:12.5px;margin:-4px 0 12px"><b>Moyenne</b> par séance pour chaque bucket jour × heure (évite le biais "Mardi 10× vs Samedi 5×").</p>
  <div id="heatmap" style="display:grid;grid-template-columns:48px repeat(17,1fr);gap:2px;font-size:10px"></div>
</div>

<div class="panel">
  <h2>⚖️ Comparateur de créneaux</h2>
  <p style="color:var(--muted);font-size:12.5px;margin:-4px 0 14px">Compare 2 créneaux jour × tranche horaire.</p>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px" id="creneauCompare"></div>
</div>

<div class="panel">
  <h2>🏆 Top 20 créneaux jour × tranche horaire</h2>
  <p style="color:var(--muted);font-size:12.5px;margin:-4px 0 14px">7 jours × 5 tranches = 35 buckets, classés par présents cumulés.</p>
  <div id="topBuckets"></div>
</div>

<div class="panel"><h2>Détail des séances</h2>
<div class="tablewrap"><table id="tbl"><thead></thead><tbody></tbody></table></div></div>
</div>
<script>
const ALL=__DATA__;const nf=v=>Math.round(v).toLocaleString('fr-FR');
const fill=t=>t>=0.75?'#5fcf8a':t>=0.5?'#e6c14d':'#e07a6f';
function fmtJ(iso){const p=iso.split('-');return `${p[2]}/${p[1]}/${p[0]}`;}
const DATA=ALL.filter(r=>r.finie && (r.capacite||0)>0);
const tot=DATA.reduce((s,r)=>s+r.presents,0),cap=DATA.reduce((s,r)=>s+r.capacite,0);
document.getElementById('kpis').innerHTML=[
 ['Séances suivies',nf(DATA.length)],['Présents (total)',nf(tot)],
 ['Moy. / séance',DATA.length?(tot/DATA.length).toFixed(1):'—'],
 ['Taux de remplissage',cap?Math.round(100*tot/cap)+'%':'—'],
].map(k=>`<div class="kpi"><div class="v">${k[1]}</div><div class="l">${k[0]}</div></div>`).join('');
if(window.Chart){Chart.defaults.color='#9ac0a8';
const byDay={};DATA.forEach(r=>byDay[r.date]=(byDay[r.date]||0)+r.presents);
const days=Object.keys(byDay).sort();
new Chart(cDay,{type:'bar',data:{labels:days.map(d=>d.slice(8)+'/'+d.slice(5,7)),datasets:[{data:days.map(d=>byDay[d]),backgroundColor:'__ACCENT__'}]},options:{plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,ticks:{callback:v=>nf(v)}}}}});}

// ============== HEATMAP / CRÉNEAUX (moyenne présents/séance pondérée) ==============
const JOURS=["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"];
const TRANCHES={matin:{label:'Matin (7-12h)',hours:[7,8,9,10,11]},
  midi:{label:'Midi (12-14h)',hours:[12,13]},
  aprem:{label:'Après-midi (14-18h)',hours:[14,15,16,17]},
  soiree:{label:'Soirée (18-22h)',hours:[18,19,20,21]},
  fin:{label:'Fin soirée (22h+)',hours:[22,23]}};
let CRENEAU_A={jour:'Mardi',tranche:'aprem'};
let CRENEAU_B={jour:'Samedi',tranche:'matin'};
(function renderHeatmap(){
  const hm=document.getElementById('heatmap');if(!hm)return;
  const hours=Array.from({length:17},(_,i)=>i+7);
  const heat={};let max=0;
  DATA.forEach(r=>{const h=parseInt((r.heure||'').slice(0,2));if(isNaN(h))return;
    const k=r.jour+'|'+h;heat[k]=heat[k]||{p:0,n:0};
    heat[k].p+=(r.presents||0);heat[k].n++;});
  Object.values(heat).forEach(x=>{x.avg=x.n?x.p/x.n:0;if(x.avg>max)max=x.avg;});
  let html='<div></div>';
  hours.forEach(h=>html+=`<div style="text-align:center;color:var(--muted);font-weight:600;padding:3px 0">${h}h</div>`);
  JOURS.forEach(j=>{
    html+=`<div style="color:var(--muted);text-align:right;padding:0 6px;font-weight:600">${j.slice(0,3)}</div>`;
    hours.forEach(h=>{const cell=heat[j+'|'+h]||{avg:0,n:0,p:0};const v=cell.avg;const t=max?v/max:0;
      const r=Math.round(44+(255-44)*t),g=Math.round(223-(223-100)*t),b=Math.round(98-(98-50)*t);
      const op=v>0?0.25+0.7*t:0.05;
      const label=v?(v>=10?Math.round(v):v.toFixed(1)):'';
      html+=`<div style="aspect-ratio:1;background:rgba(${r},${g},${b},${op});border-radius:3px;display:flex;align-items:center;justify-content:center;font-weight:700;color:#fff;font-size:9.5px;cursor:default" title="${j} ${h}h : ${v.toFixed(1)} présents/séance (${cell.n} séances obs.)">${label}</div>`;});
  });
  hm.innerHTML=html;
})();
function computeCreneau(jour,tranche){
  const hours=new Set(TRANCHES[tranche].hours);
  const f=DATA.filter(r=>{const h=parseInt((r.heure||'').slice(0,2));return r.jour===jour&&hours.has(h);});
  const presents=f.reduce((s,r)=>s+(r.presents||0),0);
  const capacite=f.reduce((s,r)=>s+(r.capacite||0),0);
  const remplissage=capacite?presents/capacite:0;
  return {n:f.length,presents,capacite,remplissage,moy:f.length?presents/f.length:0};
}
function renderCreneauCompare(){
  const wrap=document.getElementById('creneauCompare');if(!wrap)return;
  const box=(side,sel)=>{const c=computeCreneau(sel.jour,sel.tranche);
    const color=side==='A'?'__ACCENT__':'#5fcf8a';
    return `<div style="background:var(--card2);padding:14px 16px;border-radius:10px;border-left:3px solid ${color}">
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <select data-side="${side}" data-field="jour" style="flex:1;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:13px">${JOURS.map(j=>`<option value="${j}" ${j===sel.jour?'selected':''}>${j}</option>`).join('')}</select>
        <select data-side="${side}" data-field="tranche" style="flex:1;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:13px">${Object.entries(TRANCHES).map(([k,v])=>`<option value="${k}" ${k===sel.tranche?'selected':''}>${v.label}</option>`).join('')}</select>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <div><div style="font-size:22px;font-weight:800;color:#9ac0a8">${nf(c.n)}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Séances</div></div>
        <div><div style="font-size:22px;font-weight:800;color:#9ac0a8">${c.moy.toFixed(1)}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Présents/séance</div></div>
        <div><div style="font-size:22px;font-weight:800;color:${fill(c.remplissage)}">${Math.round(100*c.remplissage)}%</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Remplissage</div></div>
        <div><div style="font-size:22px;font-weight:800;color:#9ac0a8">${nf(c.presents)}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Présents cumulés</div></div>
      </div>
    </div>`;};
  wrap.innerHTML=`${box('A',CRENEAU_A)}${box('B',CRENEAU_B)}`;
  wrap.querySelectorAll('select[data-side]').forEach(s=>s.addEventListener('change',e=>{
    const sel=e.target.dataset.side==='A'?CRENEAU_A:CRENEAU_B;sel[e.target.dataset.field]=e.target.value;
    renderCreneauCompare();
  }));
}
(function renderTopBuckets(){
  const buckets=[];
  for(const jour of JOURS){for(const[tk,tv]of Object.entries(TRANCHES)){
    const hours=new Set(tv.hours);
    const f=DATA.filter(r=>{const h=parseInt((r.heure||'').slice(0,2));return r.jour===jour&&hours.has(h);});
    const p=f.reduce((s,r)=>s+(r.presents||0),0);
    const c=f.reduce((s,r)=>s+(r.capacite||0),0);
    buckets.push({label:jour+' '+tv.label,n:f.length,p,c,r:c?p/c:0});
  }}
  buckets.sort((a,b)=>b.p-a.p);
  const max=buckets[0]?.p||1;
  const w=document.getElementById('topBuckets');if(!w)return;
  w.innerHTML=buckets.slice(0,20).map((b,i)=>`<div style="display:grid;grid-template-columns:32px 280px 1fr auto;gap:10px;align-items:center;font-size:13px;padding:5px 0;border-bottom:1px solid var(--line)">
    <span style="color:var(--muted);font-weight:700;text-align:right">${i+1}.</span>
    <span style="font-weight:600">${b.label}</span>
    <span style="height:8px;background:var(--line);border-radius:4px;overflow:hidden"><span style="display:block;height:100%;background:${fill(b.r)};width:${Math.round(100*b.p/max)}%"></span></span>
    <span style="color:var(--muted);font-variant-numeric:tabular-nums;min-width:160px;text-align:right">${nf(b.p)} présents · ${Math.round(100*b.r)}% remplissage</span>
  </div>`).join('');
})();
renderCreneauCompare();

const cols=[['date','Date'],['jour','Jour'],['heure','Heure'],['lieu','Lieu'],['cours','Cours'],['coach','Coach'],['presents','Présents'],['capacite','Capacité']];
document.querySelector('#tbl thead').innerHTML='<tr>'+cols.map(c=>`<th>${c[1]}</th>`).join('')+'</tr>';
const rows=[...DATA].sort((a,b)=>(a.date+a.heure)<(b.date+b.heure)?1:-1);
document.querySelector('#tbl tbody').innerHTML=rows.map(r=>{const t=r.capacite?r.presents/r.capacite:0;
return `<tr><td>${fmtJ(r.date)}</td><td>${r.jour}</td><td>${r.heure}</td><td>${r.lieu}</td><td>${r.cours}</td><td>${r.coach||'—'}</td>`
+`<td><b>${r.presents}</b> / ${r.capacite}<span class="fillbar" style="width:${Math.round(40*t)}px;background:${fill(t)}"></span></td><td>${r.capacite}</td></tr>`;}).join('');
</script></body></html>"""


def main():
    store = capture()
    rows = sorted(store.values(), key=lambda r: (r["date"], r["heure"]))
    write_csv(rows)
    write_html(rows)
    fin = [r for r in rows if r.get("finie")]
    print(f"OK [snakeandtwist]: {len(rows)} cours, {len(fin)} terminés.")


if __name__ == "__main__":
    main()
