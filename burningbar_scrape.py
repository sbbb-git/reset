#!/usr/bin/env python3
"""Burning Bar (Mindbody) — capture du STATUT, comme Sense-Club.

Burning Bar utilise le widget Mindbody standard (pas de proxy maison comme
Punch), qui n'expose QUE le statut (Réserver / Complet / Il reste X places),
jamais le nombre exact. On rend le widget des 2 salles (Playwright via
burningbar_fetch.cjs) et on fige le statut ~10 min avant chaque séance.

Génère : burningbar_seances.csv et burningbar.html
"""
import csv
import datetime as dt
import json
import os
import safestore
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
STORE = "burningbar_data.json"
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
LOCK_MIN = 10


def fetch_today():
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        res = subprocess.run(["node", os.path.join(here, "burningbar_fetch.cjs")],
                             capture_output=True, text=True, timeout=180)
    except Exception as e:  # noqa: BLE001
        print(f"  (fetch échoué : {e})", file=sys.stderr)
        return []
    if res.returncode != 0 or not res.stdout.strip():
        print("  (fetch Mindbody vide/erreur)", file=sys.stderr)
        return []
    try:
        return json.loads(res.stdout)
    except ValueError:
        return []


def normalize(brut):
    b = (brut or "").lower()
    if "liste d'attente" in b or b.strip() == "complet":
        return "complet", 0
    m = re.search(r"reste\s+(\d+)\s+place", b)
    if m:
        return "presque complet", int(m.group(1))
    if "une place" in b:
        return "presque complet", 1
    return "disponible", None


def load_store():
    return safestore.load(STORE)


def save_store(store):
    safestore.save(store, STORE)


def capture():
    now = dt.datetime.now(PARIS)
    today = now.date()
    jour = JOURS_FR[today.weekday()]
    store = load_store()
    sessions = fetch_today()
    locked_now = 0
    for s in sessions:
        heure = s.get("heure", "")
        salle = s.get("salle", "")
        key = f"{today.isoformat()}|{heure}|{salle}|{s.get('cours','')}"
        prev = store.get(key)
        if prev and prev.get("locked"):
            continue
        statut, places = normalize(s.get("statut_brut", ""))
        try:
            hh, mm = map(int, heure.split(":"))
            start = dt.datetime(today.year, today.month, today.day, hh, mm, tzinfo=PARIS)
        except ValueError:
            continue
        lock = now >= start - dt.timedelta(minutes=LOCK_MIN)
        store[key] = {
            "date": today.isoformat(), "jour": jour, "heure": heure, "salle": salle,
            "cours": s.get("cours", ""), "statut": statut, "places_restantes": places,
            "locked": lock, "releve": now.strftime("%Y-%m-%d %H:%M"),
        }
        if lock:
            locked_now += 1
    for key, v in store.items():
        if v.get("locked"):
            continue
        try:
            d = dt.date.fromisoformat(v["date"]); hh, mm = map(int, v["heure"].split(":"))
            start = dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=PARIS)
        except (ValueError, KeyError):
            continue
        if now >= start:
            v["locked"] = True
    save_store(store)
    print(f"[burningbar] {now:%Y-%m-%d %H:%M} : {len(sessions)} séances vues, "
          f"{locked_now} verrouillées, {len(store)} au total.")
    return store


FIELDS = ["date", "jour", "heure", "salle", "cours", "statut", "places_restantes", "locked", "releve"]


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"-> {path}")


def write_html(rows, path):
    chartjs = ""
    vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor_chartjs.min.js")
    if os.path.exists(vendor):
        with open(vendor, encoding="utf-8") as f:
            chartjs = f.read()
    html = (HTML_TEMPLATE
            .replace("__CHARTJS__", chartjs)
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
            .replace("__GENERATED__", dt.datetime.now(PARIS).strftime("%d/%m/%Y %H:%M")))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"-> {path}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Burning Bar - Suivi des séances</title>
<script>__CHARTJS__</script>
<style>
  :root{--bg:#140d0a;--card:#1f1411;--card2:#2a1c16;--line:#3a261d;
        --text:#fbeee8;--muted:#c8a99c;--accent:#ff5a1f;--accent2:#ff8a5b;
        --green:#5fcf8a;--yellow:#e6c14d;--red:#e07a6f;}
  *{box-sizing:border-box;}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);}
  header{padding:28px 32px 12px;} h1{margin:0;font-size:24px;font-weight:800;}
  .sub{color:var(--muted);font-size:13px;margin-top:6px;}
  .wrap{padding:0 32px 48px;max-width:1100px;margin:0 auto;}
  .note{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px 18px;margin:18px 0 4px;color:var(--muted);font-size:13px;}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin:22px 0;}
  .kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;}
  .kpi .v{font-size:26px;font-weight:800;color:var(--accent2);}
  .kpi .l{color:var(--muted);font-size:12px;margin-top:4px;text-transform:uppercase;letter-spacing:.6px;}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:22px;}
  .panel h2{margin:0 0 14px;font-size:14px;color:var(--accent2);font-weight:700;}
  .filters{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:8px 0 16px;}
  .filters input,.filters select{background:var(--card2);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:9px 12px;font-size:13px;}
  .filters input{flex:1;min-width:160px;}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap;}
  th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--card);}
  tbody tr:hover{background:var(--card2);}
  .pill{padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;}
  .btn{background:var(--accent);color:#fff;border:none;border-radius:9px;padding:9px 16px;font-size:13px;font-weight:700;cursor:pointer;}
  .tablewrap{max-height:600px;overflow:auto;border:1px solid var(--line);border-radius:14px;}
  .empty{background:var(--card);border:1px dashed var(--line);border-radius:14px;padding:24px;text-align:center;color:var(--muted);margin:22px 0;}
  @media(max-width:600px){header{padding:18px 14px 6px;}h1{font-size:18px;}.wrap{padding:0 12px 32px;}.kpis{grid-template-columns:1fr 1fr;}.filters input,.filters select,.btn{font-size:15px;width:100%;}}
  @media(max-width:600px){canvas{max-height:200px!important}th,td{padding:7px 6px;font-size:12px}.panel{padding:15px 14px}.kpi .v{font-size:20px}.note{font-size:12px}.ctrl{font-size:12px;gap:7px}.pinp{width:64px}}
</style>
</head>
<body>
<header>
  <h1>BURNING BAR &middot; Suivi des séances</h1>
  <div class="sub">généré le __GENERATED__ &middot; statut Mindbody</div>
</header>
<div class="wrap">
  <div class="note">ℹ️ <b>Burning Bar &middot; plateforme Mindbody (widget standard).</b> Comme Sense-Club, Mindbody n'expose <b>que le statut</b> (Réserver / Il reste X places / Complet), <b>pas le nombre exact</b>. Statut lu sur les widgets des 2 salles (The Hot Room, The Reformer Room) et <b>figé ~10 min avant chaque séance</b>. L'historique s'accumule. MAJ toutes les 10 min.</div>
  <div id="emptywrap"></div>
  <div class="kpis" id="kpis"></div>
  <div class="panel">
    <h2>Séances complètes par jour</h2><canvas id="cDay" style="max-height:260px"></canvas>
  </div>
  <div class="panel">
    <h2>Détail des séances (statut figé)</h2>
    <div class="filters">
      <input id="q" placeholder="Rechercher (cours, salle...)">
      <select id="fSalle"></select>
      <select id="fStatut"></select>
      <button id="btnExport" class="btn">Exporter en Excel</button>
    </div>
    <div class="tablewrap"><table id="tbl"><thead></thead><tbody></tbody></table></div>
  </div>
  <div class="foot" style="color:var(--muted);font-size:12px">Source : burningbar.fr (widget Mindbody). Statut relevé près du début de chaque séance.</div>
</div>
<script>
const ALL=__DATA__;
const DATA=ALL.filter(r=>r.locked);
const STC={'complet':'#e07a6f','presque complet':'#e6c14d','disponible':'#5fcf8a'};
const nf=v=>Math.round(v).toLocaleString('fr-FR');
function fmtJ(iso){const p=iso.split('-');return `${p[2]}/${p[1]}/${p[0]}`;}
if(window.Chart){Chart.defaults.color='#c8a99c';Chart.defaults.borderColor='#3a261d';Chart.defaults.font.family=getComputedStyle(document.body).fontFamily;}

if(!ALL.length){
  document.getElementById('emptywrap').innerHTML='<div class="empty">⏳ Pas encore de données — la capture du planning se fait au fil des séances (toutes les 10 min). Reviens un peu plus tard.</div>';
}
const nComplet=DATA.filter(r=>r.statut==='complet').length;
const nPresque=DATA.filter(r=>r.statut==='presque complet').length;
const salles=new Set(DATA.map(r=>r.salle)).size;
const jours=new Set(DATA.map(r=>r.date)).size;
document.getElementById('kpis').innerHTML=[
  ['Séances suivies',nf(DATA.length)],
  ['Complètes',nf(nComplet)+(DATA.length?` (${Math.round(100*nComplet/DATA.length)}%)`:'')],
  ['Presque complètes',nf(nPresque)],
  ['Salles',nf(salles)],
  ['Jours couverts',nf(jours)],
].map(k=>`<div class="kpi"><div class="v">${k[1]}</div><div class="l">${k[0]}</div></div>`).join('');

if(window.Chart){
  const byDay={};DATA.forEach(r=>{byDay[r.date]=byDay[r.date]||{c:0,t:0};byDay[r.date].t++;if(r.statut==='complet')byDay[r.date].c++;});
  const days=Object.keys(byDay).sort();
  new Chart(cDay,{type:'bar',data:{labels:days.map(d=>d.slice(8)+'/'+d.slice(5,7)),
    datasets:[{label:'complètes',data:days.map(d=>byDay[d].c),backgroundColor:'#e07a6f'},
              {label:'autres',data:days.map(d=>byDay[d].t-byDay[d].c),backgroundColor:'#ff5a1f'}]},
    options:{plugins:{legend:{display:true}},scales:{x:{stacked:true},y:{stacked:true,beginAtZero:true,ticks:{callback:v=>nf(v)}}}}});
}

const cols=[['date','Date'],['jour','Jour'],['heure','Heure'],['salle','Salle'],['cours','Cours'],['statut','Statut'],['places_restantes','Places restantes']];
document.querySelector('#tbl thead').innerHTML='<tr>'+cols.map(c=>`<th>${c[1]}</th>`).join('')+'</tr>';
const selSalle=document.getElementById('fSalle'),selStatut=document.getElementById('fStatut');
selSalle.add(new Option('Toutes les salles',''));[...new Set(ALL.map(r=>r.salle))].filter(Boolean).sort().forEach(s=>selSalle.add(new Option(s,s)));
['','complet','presque complet','disponible'].forEach(s=>selStatut.add(new Option(s||'Tous les statuts',s)));
let currentRows=[];
function render(){
  const q=document.getElementById('q').value.toLowerCase(),fs=selStatut.value,fl=selSalle.value;
  let rows=DATA.filter(r=>(!fs||r.statut===fs)&&(!fl||r.salle===fl)&&(!q||((r.cours+' '+r.salle).toLowerCase().includes(q))));
  rows.sort((a,b)=>(a.date+a.heure)<(b.date+b.heure)?1:-1);
  currentRows=rows;
  document.querySelector('#tbl tbody').innerHTML=rows.map(r=>
    `<tr><td>${fmtJ(r.date)}</td><td>${r.jour}</td><td>${r.heure}</td><td>${r.salle||'—'}</td><td>${r.cours}</td>`
    +`<td><span class="pill" style="background:${STC[r.statut]}33;color:${STC[r.statut]}">${r.statut}</span></td>`
    +`<td>${r.places_restantes==null?'—':r.places_restantes}</td></tr>`).join('');
}
['q'].forEach(id=>document.getElementById(id).addEventListener('input',render));
[selSalle,selStatut].forEach(s=>s.addEventListener('change',render));
document.getElementById('btnExport').addEventListener('click',()=>{
  const esc=v=>{v=(''+v).replace(/"/g,'""');return /[";\n]/.test(v)?`"${v}"`:v;};
  const lines=[cols.map(c=>c[1]).join(';')];
  currentRows.forEach(r=>lines.push(cols.map(c=>esc(c[0]==='date'?fmtJ(r.date):(r[c[0]]==null?'':r[c[0]]))).join(';')));
  const blob=new Blob(['﻿'+lines.join('\r\n')],{type:'text/csv;charset=utf-8;'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='burningbar_seances.csv';document.body.appendChild(a);a.click();a.remove();
});
render();
</script>
</body>
</html>"""


def main():
    store = capture()
    rows = sorted(store.values(), key=lambda r: (r["date"], r["heure"], r.get("salle", "")))
    write_csv(rows, "burningbar_seances.csv")
    write_html(rows, "burningbar.html")
    locked = [r for r in rows if r.get("locked")]
    print(f"OK [burningbar]: {len(rows)} séances en base, {len(locked)} figées.")


if __name__ == "__main__":
    main()
