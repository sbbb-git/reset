#!/usr/bin/env python3
"""Capture du planning Sense-Club (Mindbody) avec verrouillage pres du debut.

Mindbody n'expose AUCUN nombre d'inscrits : seulement un statut
(disponible / presque complet "X places restantes" / complet).
Pas d'historique cote plateforme -> on accumule.

Objectif : tourner souvent (toutes les ~10 min) pendant les heures
d'ouverture. Pour chaque seance, on fige (verrouille) le statut quand on
est a <=10 min du debut -> statut quasi definitif (la seance a-t-elle fini
pleine, combien de places restaient).

Le fetch du planning est fait par senseclub_fetch.cjs (Playwright/Node),
car Mindbody charge ses donnees en JS.

Genere : senseclub_seances.csv et senseclub.html
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
STORE = "senseclub_data.json"
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
LOCK_MIN = 10  # verrouille a <= 10 min du debut
CAPACITE = 5   # Sense-Club : 5 places max par seance -> complet = 5 personnes


def presents(statut, places):
    """Nombre d'inscrits deduit du statut (capacite 5)."""
    if statut == "complet":
        return CAPACITE
    if statut == "presque complet" and places is not None:
        return max(0, CAPACITE - places)
    return None  # "disponible" : nombre exact inconnu


def fetch_today():
    here = os.path.dirname(os.path.abspath(__file__))
    res = subprocess.run(["node", os.path.join(here, "senseclub_fetch.cjs")],
                         capture_output=True, text=True, timeout=120)
    if res.returncode != 0 or not res.stdout.strip():
        print("  (fetch Mindbody vide/erreur)", file=sys.stderr)
        return []
    return json.loads(res.stdout)


def normalize(brut):
    b = brut.lower()
    if "liste d'attente" in b or b.strip() == "complet":
        return "complet", 0
    m = re.search(r"reste\s+(\d+)\s+place", b)
    if m:
        return "presque complet", int(m.group(1))
    if "une place" in b:
        return "presque complet", 1
    if "réserver" in b or "reserver" in b:
        return "disponible", None
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
        heure = s["heure"]
        key = f"{today.isoformat()}|{heure}|{s['cours']}"
        prev = store.get(key)
        if prev and prev.get("locked"):
            continue
        statut, places = normalize(s["statut_brut"])
        try:
            hh, mm = map(int, heure.split(":"))
            start = dt.datetime(today.year, today.month, today.day, hh, mm, tzinfo=PARIS)
        except ValueError:
            continue
        lock = now >= start - dt.timedelta(minutes=LOCK_MIN)
        store[key] = {
            "date": today.isoformat(), "jour": jour, "heure": heure,
            "cours": s["cours"], "statut": statut, "places_restantes": places,
            "locked": lock, "releve": now.strftime("%Y-%m-%d %H:%M"),
        }
        if lock:
            locked_now += 1
    # finalise les seances passees qu'on ne voit plus (lock du dernier statut connu)
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
    print(f"{now:%Y-%m-%d %H:%M} : {len(sessions)} séances vues, {locked_now} verrouillées ce passage, {len(store)} au total.")
    return store


FIELDS = ["date", "jour", "heure", "cours", "statut", "places_restantes", "presents", "locked", "releve"]


def enrich(rows):
    for r in rows:
        r["presents"] = presents(r.get("statut"), r.get("places_restantes"))
    return rows


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
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
<title>Sense-Club - Suivi des séances</title>
<script>__CHARTJS__</script>
<style>
  :root{--bg:#13111c;--card:#1c1930;--card2:#262040;--line:#322a52;
        --text:#ece8ف7;--text:#ece8f7;--muted:#a89fc4;--accent:#9b7ff0;--accent2:#bda6ff;
        --green:#6fcf97;--yellow:#e6c14d;--red:#e07a6f;}
  *{box-sizing:border-box;}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);}
  header{padding:28px 32px 12px;} h1{margin:0;font-size:24px;font-weight:700;}
  .sub{color:var(--muted);font-size:13px;margin-top:6px;}
  .wrap{padding:0 32px 48px;max-width:1180px;margin:0 auto;}
  .note{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px 18px;margin:18px 0 4px;color:var(--muted);font-size:13px;}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin:22px 0;}
  .kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;}
  .kpi .v{font-size:28px;font-weight:700;color:var(--accent2);}
  .kpi .l{color:var(--muted);font-size:12px;margin-top:4px;text-transform:uppercase;letter-spacing:.6px;}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:22px;}
  @media(max-width:880px){.grid{grid-template-columns:1fr;}}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;}
  .panel h2{margin:0 0 14px;font-size:14px;color:var(--accent2);font-weight:600;}
  canvas{max-height:280px;}
  .filters{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:8px 0 16px;}
  .filters input,.filters select{background:var(--card2);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:9px 12px;font-size:13px;}
  .filters input{flex:1;min-width:180px;}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap;}
  th{color:var(--muted);font-weight:600;cursor:pointer;position:sticky;top:0;background:var(--card);}
  tbody tr:hover{background:var(--card2);}
  .pill{padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;}
  .ranklist{display:flex;flex-direction:column;gap:9px;}
  .rk{display:grid;grid-template-columns:70px 1fr auto;align-items:center;gap:10px;font-size:13px;}
  .rk .lbl{font-weight:600;} .rk .track{height:9px;background:var(--line);border-radius:5px;overflow:hidden;}
  .rk .track>span{display:block;height:100%;background:var(--accent);border-radius:5px;} .rk .val{color:var(--muted);}
  .btn{background:var(--accent);color:#13111c;border:none;border-radius:9px;padding:9px 16px;font-size:13px;font-weight:700;cursor:pointer;}
  .tablewrap{max-height:600px;overflow:auto;border:1px solid var(--line);border-radius:14px;}
  .foot{color:var(--muted);font-size:12px;margin-top:18px;}
  @media(max-width:600px){header{padding:18px 14px 6px;}h1{font-size:18px;}.wrap{padding:0 12px 32px;}.kpis{grid-template-columns:1fr 1fr;gap:10px;}.kpi .v{font-size:21px;}.filters input,.filters select,.btn{font-size:15px;width:100%;}}
  @media(max-width:600px){canvas{max-height:200px!important}th,td{padding:7px 6px;font-size:12px}.panel{padding:15px 14px}.kpi .v{font-size:20px}.note{font-size:12px}.ctrl{font-size:12px;gap:7px}.pinp{width:64px}}
</style>
</head>
<body>
<header>
  <h1>Sense-Club &middot; Suivi des séances</h1>
  <div class="sub">généré le __GENERATED__</div>
</header>
<div class="wrap">
  <div class="note">ℹ️ Séances de <b>5 places max</b> : <b>complet = 5 personnes</b>, et « presque complet — reste X places » = <b>5 − X présents</b>. Mindbody n'affiche pas le compte exact quand il reste beaucoup de place (statut « disponible »). Le statut est <b>figé ~10 min avant chaque séance</b> (quasi définitif). L'historique se construit jour après jour.</div>
  <div class="kpis" id="kpis"></div>
  <div class="grid">
    <div class="panel"><h2>Séances complètes par jour</h2><canvas id="cDay"></canvas></div>
    <div class="panel"><h2>Créneaux qui affichent le plus « complet »</h2><div id="topHour" class="ranklist"></div></div>
  </div>
  <div class="panel"><h2>Répartition des statuts par type de cours</h2><canvas id="cCours"></canvas></div>
  <div class="panel">
    <h2>Détail des séances (statut figé)</h2>
    <div class="filters">
      <input id="q" placeholder="Rechercher (cours, date...)">
      <select id="fStatut"></select>
      <button id="btnExport" class="btn">Exporter en Excel</button>
    </div>
    <div class="tablewrap"><table id="tbl"><thead></thead><tbody></tbody></table></div>
  </div>
  <div class="foot">Source : sense-club.fr (widget Mindbody). Statut relevé près du début de chaque séance.</div>
</div>
<script>
const ALL=__DATA__;
const DATA=ALL.filter(r=>r.locked);   // on n'affiche que les statuts figés (definitifs)
const JOURS=["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"];
const MOIS=['janv.','févr.','mars','avr.','mai','juin','juil.','août','sept.','oct.','nov.','déc.'];
const nf=v=>Math.round(v).toLocaleString('fr-FR');
const STC={'complet':'#e07a6f','presque complet':'#e6c14d','disponible':'#6fcf97'};
function fmtJ(iso){const p=iso.split('-');return `${p[2]}/${p[1]}/${p[0]}`;}
Chart.defaults.color='#a89fc4';Chart.defaults.borderColor='#322a52';Chart.defaults.font.family=getComputedStyle(document.body).fontFamily;

const CAP=5;
const nComplet=DATA.filter(r=>r.statut==='complet').length;
const nPresque=DATA.filter(r=>r.statut==='presque complet').length;
const nDispo=DATA.filter(r=>r.statut==='disponible').length;
const jours=new Set(DATA.map(r=>r.date)).size;
const known=DATA.filter(r=>r.presents!=null);
const totPres=known.reduce((s,r)=>s+r.presents,0);
const avgPres=known.length?totPres/known.length:0;
document.getElementById('kpis').innerHTML=[
  ['Séances suivies',nf(DATA.length)],
  ['Présents (total connu)',nf(totPres)],
  ['Moy. présents / séance',known.length?avgPres.toFixed(1)+' / '+CAP:'—'],
  ['Complètes (5/5)',nf(nComplet)+(DATA.length?` (${Math.round(100*nComplet/DATA.length)}%)`:'')],
].map(k=>`<div class="kpi"><div class="v">${k[1]}</div><div class="l">${k[0]}</div></div>`).join('');

// séances complètes par jour
const byDay={};DATA.forEach(r=>{byDay[r.date]=byDay[r.date]||{c:0,t:0};byDay[r.date].t++;if(r.statut==='complet')byDay[r.date].c++;});
const days=Object.keys(byDay).sort();
new Chart(cDay,{type:'bar',data:{labels:days.map(d=>d.slice(8)+'/'+d.slice(5,7)),
  datasets:[{label:'complètes',data:days.map(d=>byDay[d].c),backgroundColor:'#e07a6f'},
            {label:'autres',data:days.map(d=>byDay[d].t-byDay[d].c),backgroundColor:'#9b7ff0'}]},
  options:{plugins:{legend:{display:true}},scales:{x:{stacked:true},y:{stacked:true,beginAtZero:true,ticks:{callback:v=>nf(v)}}}}});

// créneaux les plus "complet"
const byH={};DATA.forEach(r=>{if(r.statut==='complet'){byH[r.heure]=(byH[r.heure]||0)+1;}});
const hrs=Object.entries(byH).sort((a,b)=>b[1]-a[1]).slice(0,8);
const mx=hrs.length?hrs[0][1]:0;
document.getElementById('topHour').innerHTML = hrs.length? hrs.map(([h,v])=>
  `<div class="rk"><span class="lbl">${h}</span><span class="track"><span style="width:${mx?Math.round(100*v/mx):0}%"></span></span><span class="val">${v}×</span></div>`).join('')
  : '<div style="color:var(--muted)">Pas encore de séance complète enregistrée.</div>';

// statuts par type de cours
const byC={};DATA.forEach(r=>{const k=r.cours||'?';byC[k]=byC[k]||{'complet':0,'presque complet':0,'disponible':0};byC[k][r.statut]++;});
const cours=Object.keys(byC).sort((a,b)=>(byC[b].complet+byC[b]['presque complet'])-(byC[a].complet+byC[a]['presque complet']));
new Chart(cCours,{type:'bar',data:{labels:cours,datasets:[
  {label:'complet',data:cours.map(c=>byC[c].complet),backgroundColor:STC.complet},
  {label:'presque complet',data:cours.map(c=>byC[c]['presque complet']),backgroundColor:STC['presque complet']},
  {label:'disponible',data:cours.map(c=>byC[c].disponible),backgroundColor:STC.disponible}]},
  options:{indexAxis:'y',plugins:{legend:{display:true}},scales:{x:{stacked:true,beginAtZero:true,ticks:{callback:v=>nf(v)}},y:{stacked:true}}}});

// table
const cols=[['date','Date'],['jour','Jour'],['heure','Heure'],['cours','Cours'],['statut','Statut'],['presents','Présents'],['places_restantes','Places restantes']];
document.querySelector('#tbl thead').innerHTML='<tr>'+cols.map(c=>`<th>${c[1]}</th>`).join('')+'</tr>';
const sel=document.getElementById('fStatut');
['','complet','presque complet','disponible'].forEach(s=>sel.add(new Option(s||'Tous les statuts',s)));
let currentRows=[];
function render(){
  const q=document.getElementById('q').value.toLowerCase(), fs=sel.value;
  let rows=DATA.filter(r=>(!fs||r.statut===fs)&&(!q||(r.cours+' '+r.date).toLowerCase().includes(q)));
  rows.sort((a,b)=>(a.date+a.heure)<(b.date+b.heure)?1:-1);
  currentRows=rows;
  document.querySelector('#tbl tbody').innerHTML=rows.map(r=>
    `<tr><td>${fmtJ(r.date)}</td><td>${r.jour}</td><td>${r.heure}</td><td>${r.cours}</td>`
    +`<td><span class="pill" style="background:${STC[r.statut]}33;color:${STC[r.statut]}">${r.statut}</span></td>`
    +`<td>${r.presents==null?'—':r.presents+' / '+CAP}</td>`
    +`<td>${r.places_restantes==null?'—':r.places_restantes}</td></tr>`).join('');
}
['q'].forEach(id=>document.getElementById(id).addEventListener('input',render));
sel.addEventListener('change',render);
document.getElementById('btnExport').addEventListener('click',()=>{
  const c2=[['date','Date'],['jour','Jour'],['heure','Heure'],['cours','Cours'],['statut','Statut'],['presents','Présents'],['places_restantes','Places restantes']];
  const esc=v=>{v=(''+v).replace(/"/g,'""');return /[";\n]/.test(v)?`"${v}"`:v;};
  const lines=[c2.map(c=>c[1]).join(';')];
  currentRows.forEach(r=>lines.push(c2.map(c=>esc(c[0]==='date'?fmtJ(r.date):(r[c[0]]==null?'':r[c[0]]))).join(';')));
  const blob=new Blob(['﻿'+lines.join('\r\n')],{type:'text/csv;charset=utf-8;'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='senseclub_seances.csv';document.body.appendChild(a);a.click();a.remove();
});
render();
</script>
</body>
</html>"""


def main():
    store = capture()
    rows = enrich(sorted(store.values(), key=lambda r: (r["date"], r["heure"])))
    write_csv(rows, "senseclub_seances.csv")
    write_html(rows, "senseclub.html")
    locked = [r for r in rows if r.get("locked")]
    print(f"OK: {len(rows)} séances en base, {len(locked)} figées (définitives).")


if __name__ == "__main__":
    main()
