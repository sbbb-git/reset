#!/usr/bin/env python3
"""Scrape le taux de remplissage des seances Re-SET (booking bsport).

Recupere, jour par jour et seance par seance, le nombre de personnes
presentes / la capacite de chaque seance, et ecrit le tout dans un CSV.

Relancer le script reactualise les donnees (il reecrit le CSV).

Usage:
    python3 reset_scrape.py                         # depuis 2026-04-22 jusqu'a aujourd'hui
    python3 reset_scrape.py --start 2026-04-22      # date de debut
    python3 reset_scrape.py --start 2026-04-22 --end 2026-06-30
    python3 reset_scrape.py --out mes_donnees.csv

Source: https://www.re-set.club/reservation  (widget bsport, company 5181)
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

API = "https://api.production.bsport.io"
COMPANY = 5181
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def _get(path, params, retries=4):
    url = f"{API}{path}?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE401
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"echec requete {url}: {last}")


def fetch_coaches():
    """Table associated_coach_id -> nom complet (best effort)."""
    mapping = {}
    try:
        data = _get("/book/v1/associated_coach/", {"company": COMPANY, "page_size": 500})
        for c in data.get("results", []):
            name = c.get("name") or f"{c.get('firstname','')} {c.get('lastname','')}".strip()
            for key in ("associated_coach_id", "id"):
                if c.get(key):
                    mapping[c[key]] = name
            for aid in c.get("associatedcoach_set", []) or []:
                mapping.setdefault(aid, name)
    except Exception as e:  # noqa: BLE401
        print(f"  (avertissement: noms des coachs indisponibles: {e})", file=sys.stderr)
    return mapping


def fetch_offers(start, end):
    """Toutes les seances entre start et end (dates incluses)."""
    offers = []
    page = 1
    while True:
        data = _get("/book/v1/offer/", {
            "company": COMPANY,
            "only_future_strict": "false",
            "min_date": start.isoformat(),
            "max_date": end.isoformat(),
            "page_size": 300,
            "page": page,
        })
        results = data.get("results", [])
        offers.extend(results)
        if not data.get("next_page") or not results:
            break
        page += 1
    return offers


def build_rows(offers, coaches):
    rows = []
    for o in offers:
        ds = o.get("date_start", "")
        local = ds[:16].replace("T", " ")  # 'AAAA-MM-JJ HH:MM' heure locale Paris
        date_part = local[:10]
        heure = local[11:16]
        try:
            d = dt.date.fromisoformat(date_part)
            jour = JOURS_FR[d.weekday()]
        except ValueError:
            jour = ""
        present = o.get("validated_booking_count", 0)
        capacite = o.get("effectif", 0)
        taux = round(100 * present / capacite, 1) if capacite else ""
        coach_id = o.get("coach")
        rows.append({
            "date": date_part,
            "jour": jour,
            "heure": heure,
            "activite": o.get("activity_name", ""),
            "coach": coaches.get(coach_id, coach_id if coach_id else ""),
            "presents": present,
            "capacite": capacite,
            "remplissage": f"{present}/{capacite}",
            "taux_%": taux,
            "complet": "oui" if o.get("full") else "non",
            "session_id": o.get("id"),
        })

    rows.sort(key=lambda r: (r["date"], r["heure"]))
    return rows


FIELDS = ["date", "jour", "heure", "activite", "coach", "presents",
          "capacite", "remplissage", "taux_%", "complet", "session_id"]


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"-> {path}")


def write_xlsx(rows, path):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        print("  (Excel ignore: 'pip install openpyxl' pour l'activer)", file=sys.stderr)
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "Seances"
    head_fill = PatternFill("solid", fgColor="3A211B")
    head_font = Font(bold=True, color="FFFFFF")
    ws.append([h.replace("_", " ").capitalize() for h in FIELDS])
    for c in ws[1]:
        c.fill = head_fill
        c.font = head_font
        c.alignment = Alignment(horizontal="center")
    for r in rows:
        ws.append([r[h] for h in FIELDS])
    # couleur du taux selon remplissage
    taux_col = FIELDS.index("taux_%") + 1
    for row in ws.iter_rows(min_row=2, min_col=taux_col, max_col=taux_col):
        cell = row[0]
        v = cell.value
        if isinstance(v, (int, float)):
            if v >= 85:
                cell.fill = PatternFill("solid", fgColor="C6EFCE")
            elif v >= 50:
                cell.fill = PatternFill("solid", fgColor="FFEB9C")
            else:
                cell.fill = PatternFill("solid", fgColor="FFC7CE")
    widths = [12, 11, 7, 18, 26, 9, 9, 12, 9, 9, 12]
    for i, wdt in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = wdt
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(path)
    print(f"-> {path}")


def write_html(rows, path, start, end):
    payload = json.dumps(rows, ensure_ascii=False)
    generated = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    chartjs = ""
    vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor_chartjs.min.js")
    if os.path.exists(vendor):
        with open(vendor, encoding="utf-8") as f:
            chartjs = f.read()
    else:
        chartjs = ('document.write(\'<scr\'+\'ipt src="https://cdn.jsdelivr.net/npm/'
                   'chart.js@4.4.1/dist/chart.umd.min.js"><\\/scr\'+\'ipt>\');')
    html = HTML_TEMPLATE.replace("__CHARTJS__", chartjs) \
        .replace("__DATA__", payload) \
        .replace("__GENERATED__", generated) \
        .replace("__PERIODE__", f"{start} au {end}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"-> {path}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Re-SET - Taux de remplissage des seances</title>
<script>__CHARTJS__</script>
<style>
  :root{--bg:#241410;--card:#33201a;--card2:#3d271f;--line:#4d342a;
        --text:#f3e7df;--muted:#bda394;--accent:#d98b63;--accent2:#e8b08a;
        --green:#7bbf86;--yellow:#e0c06a;--red:#d9776f;}
  *{box-sizing:border-box;}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       background:var(--bg);color:var(--text);}
  header{padding:28px 32px 12px;}
  h1{margin:0;font-size:24px;letter-spacing:.5px;font-weight:700;}
  .sub{color:var(--muted);font-size:13px;margin-top:6px;}
  .wrap{padding:0 32px 48px;max-width:1280px;margin:0 auto;}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:16px;margin:24px 0;}
  .kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;}
  .kpi .v{font-size:28px;font-weight:700;color:var(--accent2);}
  .kpi .l{color:var(--muted);font-size:12px;margin-top:4px;text-transform:uppercase;letter-spacing:.6px;}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:22px;}
  @media(max-width:880px){.grid{grid-template-columns:1fr;}}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;}
  .panel h2{margin:0 0 14px;font-size:14px;color:var(--accent2);font-weight:600;letter-spacing:.4px;}
  canvas{max-height:260px;}
  .filters{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:8px 0 16px;}
  .filters input,.filters select{background:var(--card2);color:var(--text);border:1px solid var(--line);
       border-radius:9px;padding:9px 12px;font-size:13px;}
  .filters input{flex:1;min-width:180px;}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap;}
  th{color:var(--muted);font-weight:600;cursor:pointer;user-select:none;position:sticky;top:0;background:var(--card);}
  th:hover{color:var(--accent2);}
  tbody tr:hover{background:var(--card2);}
  .bar{display:inline-block;height:7px;border-radius:4px;vertical-align:middle;margin-right:8px;width:60px;
       background:var(--line);position:relative;overflow:hidden;}
  .bar>span{position:absolute;left:0;top:0;bottom:0;border-radius:4px;}
  .pill{padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600;}
  .tablewrap{max-height:560px;overflow:auto;border:1px solid var(--line);border-radius:14px;}
  .foot{color:var(--muted);font-size:12px;margin-top:18px;}
  a{color:var(--accent2);}
  label.fld{color:var(--muted);font-size:13px;display:inline-flex;align-items:center;}
  .seg button{background:var(--card2);color:var(--muted);border:1px solid var(--line);
       padding:8px 16px;font-size:13px;cursor:pointer;}
  .seg button:first-child{border-radius:9px 0 0 9px;}
  .seg button:last-child{border-radius:0 9px 9px 0;}
  .seg button:not(:last-child){border-right:none;}
  .seg button.on{background:var(--accent);color:#241410;font-weight:700;}
</style>
</head>
<body>
<header>
  <h1>Re-SET &middot; Taux de remplissage des seances</h1>
  <div class="sub">Periode __PERIODE__ &middot; donnees actualisees le __GENERATED__</div>
</header>
<div class="wrap">
  <div class="kpis" id="kpis"></div>
  <div class="panel" id="caPanel">
    <h2>Chiffre d'affaires estime</h2>
    <div class="filters">
      <label class="fld">Prix moyen par seance (&euro;)
        <input id="prix" type="number" min="0" step="0.5" value="25" style="width:90px;margin-left:8px">
      </label>
      <span class="seg" id="caSeg">
        <button data-g="jour">Par jour</button>
        <button data-g="semaine">Par semaine</button>
        <button data-g="mois" class="on">Par mois</button>
      </span>
    </div>
    <div class="kpis" id="caKpis" style="margin:6px 0 18px"></div>
    <canvas id="cCA"></canvas>
  </div>
  <div class="panel">
    <h2>Evolution du taux de remplissage depuis l'ouverture</h2>
    <span class="seg" id="evSeg">
      <button data-g="jour">Par jour</button>
      <button data-g="semaine">Par semaine</button>
      <button data-g="mois" class="on">Par mois</button>
    </span>
    <canvas id="cDay" style="margin-top:14px"></canvas>
  </div>
  <div class="grid">
    <div class="panel"><h2>Taux moyen par type de seance</h2><canvas id="cAct"></canvas></div>
    <div class="panel"><h2>Taux moyen par creneau horaire</h2><canvas id="cHour"></canvas></div>
  </div>
  <div class="panel">
    <h2>Taux de remplissage moyen par coach (les stars)</h2>
    <canvas id="cCoach" style="max-height:none"></canvas>
  </div>
  <div class="panel">
    <h2>Detail des seances</h2>
    <div class="filters">
      <input id="q" placeholder="Rechercher (activite, coach, date...)">
      <select id="fAct"><option value="">Toutes activites</option></select>
      <select id="fDay"><option value="">Tous les jours</option></select>
    </div>
    <div class="tablewrap"><table id="tbl"><thead></thead><tbody></tbody></table></div>
  </div>
  <div class="foot">Source : re-set.club (widget bsport). Genere par reset_scrape.py.</div>
</div>
<script>
const DATA = __DATA__;
const JOURS = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"];
const col = t => t>=85?'#7bbf86':t>=50?'#e0c06a':'#d9776f';
const txtCss = getComputedStyle(document.body);
Chart.defaults.color = '#bda394';
Chart.defaults.borderColor = '#4d342a';
Chart.defaults.font.family = txtCss.fontFamily;

function avg(arr){let p=0,c=0;arr.forEach(r=>{p+=r.presents;c+=r.capacite;});return c?p/c*100:0;}
function group(key){const m={};DATA.forEach(r=>{(m[key(r)]=m[key(r)]||[]).push(r);});return m;}
function lundi(s){const d=new Date(s+'T00:00:00');const j=(d.getDay()+6)%7;d.setDate(d.getDate()-j);return d.toISOString().slice(0,10);}
function periodKey(r,g){return g==='jour'?r.date:g==='mois'?r.date.slice(0,7):lundi(r.date);}

// KPIs
const totP=DATA.reduce((s,r)=>s+r.presents,0), totC=DATA.reduce((s,r)=>s+r.capacite,0);
const complets=DATA.filter(r=>r.complet==='oui').length;
const kpis=[
  ['Seances', DATA.length],
  ['Presents (total)', totP.toLocaleString('fr-FR')],
  ['Places (total)', totC.toLocaleString('fr-FR')],
  ['Taux moyen', (totC?totP/totC*100:0).toFixed(1)+'%'],
  ['Seances completes', complets],
];
document.getElementById('kpis').innerHTML = kpis.map(k=>
  `<div class="kpi"><div class="v">${k[1]}</div><div class="l">${k[0]}</div></div>`).join('');

// Evolution du taux (toggle jour/semaine/mois)
let evChart=null, evGran='mois';
function renderEv(){
  const m={};DATA.forEach(r=>{const k=periodKey(r,evGran);(m[k]=m[k]||[]).push(r);});
  const keys=Object.keys(m).sort();
  const labels=keys.map(k=>evGran==='mois'?k:k.slice(5));
  if(evChart)evChart.destroy();
  evChart=new Chart(cDay,{type:'line',data:{labels,
    datasets:[{data:keys.map(k=>avg(m[k])),borderColor:'#d98b63',
      backgroundColor:'rgba(217,139,99,.15)',fill:true,tension:.3,pointRadius:evGran==='jour'?1:3}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.parsed.y.toFixed(1)+'%'}}},
      scales:{y:{max:100,ticks:{callback:v=>v+'%'}}}}});
}
document.querySelectorAll('#evSeg button').forEach(b=>b.onclick=()=>{
  evGran=b.dataset.g;document.querySelectorAll('#evSeg button').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');renderEv();});
renderEv();

// par activite
const byAct=group(r=>r.activite); const acts=Object.keys(byAct).sort((a,b)=>avg(byAct[b])-avg(byAct[a]));
new Chart(cAct,{type:'bar',data:{labels:acts,
  datasets:[{data:acts.map(a=>avg(byAct[a])),backgroundColor:acts.map(a=>col(avg(byAct[a])))}]},
  options:{indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{max:100,ticks:{callback:v=>v+'%'}}}}});

// (jours de semaine encore calcules pour le filtre du tableau)
const byW=group(r=>r.jour);
const wlabels=JOURS.filter(j=>byW[j]);

// par coach (min. 3 seances pour eviter le bruit)
const byCoach=group(r=>r.coach||'(sans coach)');
const coachList=Object.keys(byCoach).filter(c=>byCoach[c].length>=3)
  .sort((a,b)=>avg(byCoach[b])-avg(byCoach[a]));
new Chart(cCoach,{type:'bar',data:{labels:coachList.map(c=>`${c} (${byCoach[c].length})`),
  datasets:[{data:coachList.map(c=>avg(byCoach[c])),backgroundColor:coachList.map(c=>col(avg(byCoach[c])))}]},
  options:{indexAxis:'y',plugins:{legend:{display:false},
    tooltip:{callbacks:{label:c=>c.parsed.x.toFixed(1)+'%'}}},
    scales:{x:{max:100,ticks:{callback:v=>v+'%'}}}}});

// par heure
const byH=group(r=>r.heure); const hours=Object.keys(byH).sort();
new Chart(cHour,{type:'bar',data:{labels:hours,
  datasets:[{data:hours.map(h=>avg(byH[h])),backgroundColor:hours.map(h=>col(avg(byH[h])))}]},
  options:{plugins:{legend:{display:false}},scales:{y:{max:100,ticks:{callback:v=>v+'%'}}}}});

// Table
const cols=[['date','Date'],['jour','Jour'],['heure','Heure'],['activite','Activite'],
  ['coach','Coach'],['remplissage','Remplissage'],['taux_%','Taux'],['complet','Complet']];
document.querySelector('#tbl thead').innerHTML='<tr>'+cols.map((c,i)=>`<th data-i="${i}">${c[1]}</th>`).join('')+'</tr>';
const selAct=document.getElementById('fAct'); acts.slice().sort().forEach(a=>selAct.add(new Option(a,a)));
const selDay=document.getElementById('fDay'); wlabels.forEach(j=>selDay.add(new Option(j,j)));
let sortKey='date',sortDir=1;
function render(){
  const q=document.getElementById('q').value.toLowerCase();
  const fa=selAct.value, fd=selDay.value;
  let rows=DATA.filter(r=>(!fa||r.activite===fa)&&(!fd||r.jour===fd)&&
    (!q||(r.activite+' '+r.coach+' '+r.date+' '+r.heure).toLowerCase().includes(q)));
  rows.sort((a,b)=>{let x=a[sortKey],y=b[sortKey];
    if(sortKey==='taux_%'){x=+x||0;y=+y||0;} return x>y?sortDir:x<y?-sortDir:0;});
  document.querySelector('#tbl tbody').innerHTML=rows.map(r=>{
    const t=+r['taux_%']||0;
    return `<tr><td>${r.date}</td><td>${r.jour}</td><td>${r.heure}</td><td>${r.activite}</td>
      <td>${r.coach}</td>
      <td><span class="bar"><span style="width:${Math.min(t,100)}%;background:${col(t)}"></span></span>${r.remplissage}</td>
      <td><span class="pill" style="background:${col(t)}33;color:${col(t)}">${t}%</span></td>
      <td>${r.complet}</td></tr>`;}).join('');
}
document.querySelectorAll('#tbl thead th').forEach(th=>th.onclick=()=>{
  const k=cols[+th.dataset.i][0]; sortDir=(sortKey===k)?-sortDir:1; sortKey=k; render();});
['q','fAct','fDay'].forEach(id=>document.getElementById(id).addEventListener('input',render));
render();

// ---- Chiffre d'affaires estime ----
let caChart=null, caGran='mois';
const eur = v => v.toLocaleString('fr-FR',{maximumFractionDigits:0})+' €';
function renderCA(){
  const prix=parseFloat(document.getElementById('prix').value)||0;
  try{localStorage.setItem('reset_prix',prix);}catch(e){}
  const sumBy=g=>{const m={};DATA.forEach(r=>{const k=periodKey(r,g);m[k]=(m[k]||0)+r.presents;});return m;};
  const nbMois=new Set(DATA.map(r=>r.date.slice(0,7))).size||1;
  const nbSem=new Set(DATA.map(r=>lundi(r.date))).size||1;
  const nbJours=new Set(DATA.map(r=>r.date)).size||1;
  const totalCA=DATA.reduce((s,r)=>s+r.presents,0)*prix;
  document.getElementById('caKpis').innerHTML=[
    ['CA total estime',eur(totalCA)],
    ['CA / mois (moy.)',eur(totalCA/nbMois)],
    ['CA / semaine (moy.)',eur(totalCA/nbSem)],
    ['CA / jour (moy.)',eur(totalCA/nbJours)],
  ].map(k=>`<div class="kpi"><div class="v">${k[1]}</div><div class="l">${k[0]}</div></div>`).join('');
  const m=sumBy(caGran), keys=Object.keys(m).sort();
  const labels=keys.map(k=>caGran==='mois'?k:caGran==='jour'?k.slice(5):k.slice(5));
  const vals=keys.map(k=>m[k]*prix);
  if(caChart)caChart.destroy();
  caChart=new Chart(cCA,{type:'bar',data:{labels,datasets:[{data:vals,backgroundColor:'#d98b63'}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>eur(c.parsed.y)}}},
      scales:{y:{ticks:{callback:v=>v.toLocaleString('fr-FR')+' €'}}}}});
}
document.getElementById('prix').addEventListener('input',renderCA);
document.querySelectorAll('#caSeg button').forEach(b=>b.onclick=()=>{
  caGran=b.dataset.g;document.querySelectorAll('#caSeg button').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');renderCA();});
const sp=(()=>{try{return localStorage.getItem('reset_prix');}catch(e){return null;}})();
if(sp)document.getElementById('prix').value=sp;
renderCA();
</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description="Scrape taux de remplissage des seances Re-SET")
    ap.add_argument("--start", default="2026-02-01", help="date de debut AAAA-MM-JJ (defaut: depuis l'ouverture)")
    ap.add_argument("--end", default=None, help="date de fin AAAA-MM-JJ (defaut: aujourd'hui)")
    ap.add_argument("--csv", default="reset_seances.csv", help="fichier CSV de sortie")
    ap.add_argument("--xlsx", default="reset_seances.xlsx", help="fichier Excel de sortie")
    ap.add_argument("--html", default="index.html", help="dashboard HTML de sortie")
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()

    print(f"Recuperation des seances du {start} au {end} ...")
    coaches = fetch_coaches()
    offers = fetch_offers(start, end)
    print(f"{len(offers)} seances recuperees.")
    rows = build_rows(offers, coaches)

    write_csv(rows, args.csv)
    write_xlsx(rows, args.xlsx)
    write_html(rows, args.html, start, end)

    if rows:
        jours = sorted({r["date"] for r in rows})
        tot_p = sum(r["presents"] for r in rows)
        tot_c = sum(r["capacite"] for r in rows)
        moy = round(100 * tot_p / tot_c, 1) if tot_c else 0
        print(f"OK: {len(rows)} seances sur {len(jours)} jours "
              f"({jours[0]} -> {jours[-1]}), {tot_p} presents / {tot_c} places, "
              f"taux moyen {moy}%.")
    else:
        print("Aucune seance trouvee sur cette periode.")


if __name__ == "__main__":
    main()
