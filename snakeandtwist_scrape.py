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
    html = (TPL.replace("__CHARTJS__", chartjs)
              .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
              .replace("__GENERATED__", dt.datetime.now(PARIS).strftime("%d/%m/%Y %H:%M"))
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
<div class="note">ℹ️ <b>Snake & Twist &middot; plateforme Arketa.</b> Présents = `total_booked` exact, capacité = `max_capacity`. Les rendez-vous privés (Appointment) sont exclus pour ne garder que les cours collectifs. Fenêtre courante : ~5 semaines. MAJ 30 min via robot.</div>
<div class="kpis" id="kpis"></div>
<div class="panel"><h2>Présents par jour</h2><canvas id="cDay"></canvas></div>
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
