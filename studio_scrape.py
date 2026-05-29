#!/usr/bin/env python3
"""Moteur générique de scrap fréquentation des studios Monday (Mindbody).

Réutilisé par punch_scrape.py / dynamo_scrape.py / riise_scrape.py.
L'API publique <host>/wp-json/mindbody/v1/class expose, sur une fenêtre
glissante de ~7 jours, les chiffres EXACTS : MaxCapacity (capacité),
TotalBooked (réservés), TotalSignedIn (présents réels). Pas d'historique
au-delà côté plateforme -> on accumule dans <key>_data.json (clé = Id du
cours). Quand un cours est terminé, TotalSignedIn est définitif.

Chaque studio fournit sa config et appelle run(cfg).
"""
import csv
import datetime as dt
import json
import os
import sys
import urllib.request
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

FIELDS = ["date", "jour", "heure", "fin", "lieu", "cours", "coach",
          "capacite", "reserves", "presents", "noshow", "finie", "releve"]


def fetch_classes(api):
    req = urllib.request.Request(api, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120",
        "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def short_loc(name, prefix):
    n = (name or "").strip()
    if prefix and n.lower().startswith(prefix.lower()):
        n = n[len(prefix):].strip()
    n = n.lstrip("-").strip()
    return n or name


def load_store(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (ValueError, OSError):
            pass
    return {}


def save_store(store, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=0, sort_keys=True)


def capture(cfg):
    now = dt.datetime.now(PARIS)
    store = load_store(cfg["store"])
    try:
        classes = fetch_classes(cfg["api"])
    except Exception as e:  # noqa: BLE001
        print(f"  (fetch API échoué : {e})", file=sys.stderr)
        return store
    seen = 0
    for c in classes:
        if c.get("IsCanceled"):
            continue
        start = c.get("StartDateTime", "")
        end = c.get("EndDateTime", "")
        if not start:
            continue
        try:
            sdt = dt.datetime.fromisoformat(start).replace(tzinfo=PARIS)
            edt = dt.datetime.fromisoformat(end).replace(tzinfo=PARIS) if end else sdt
        except ValueError:
            continue
        cid = str(c.get("Id"))
        cd = c.get("ClassDescription") or {}
        staff = c.get("Staff") or {}
        store[cid] = {
            "id": cid,
            "date": start[:10],
            "jour": JOURS_FR[sdt.weekday()],
            "heure": start[11:16],
            "fin": end[11:16] if end else "",
            "lieu": short_loc((c.get("Location") or {}).get("Name"), cfg["prefix"]),
            "cours": (cd.get("Name") or "").strip(),
            "coach": (staff.get("Name") or "").strip(),
            "capacite": c.get("MaxCapacity") or 0,
            "reserves": c.get("TotalBooked") or 0,
            "presents": c.get("TotalSignedIn") or 0,
            "finie": now >= edt,
            "releve": now.strftime("%Y-%m-%d %H:%M"),
        }
        seen += 1
    save_store(store, cfg["store"])
    fin = sum(1 for v in store.values() if v.get("finie"))
    print(f"[{cfg['key']}] {now:%Y-%m-%d %H:%M} : {seen} cours vus, {len(store)} en base ({fin} terminés).")
    return store


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"-> {path}")


def write_html(rows, cfg):
    chartjs = ""
    vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor_chartjs.min.js")
    if os.path.exists(vendor):
        with open(vendor, encoding="utf-8") as f:
            chartjs = f.read()
    html = (HTML_TEMPLATE
            .replace("__CHARTJS__", chartjs)
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
            .replace("__GENERATED__", dt.datetime.now(PARIS).strftime("%d/%m/%Y %H:%M"))
            .replace("__BRAND__", cfg["brand"])
            .replace("__PRICE__", str(cfg["price"]))
            .replace("__PRIXKEY__", cfg["key"] + "_prix")
            .replace("__CSVNAME__", cfg["key"] + "_seances.csv")
            .replace("__ACCENT__", cfg["accent"])
            .replace("__ACCENT2__", cfg["accent2"])
            .replace("__PREFIX__", cfg["prefix"])
            .replace("__API__", cfg["api"])
            .replace("__HOST__", cfg["host"]))
    with open(cfg["html"], "w", encoding="utf-8") as f:
        f.write(html)
    print(f"-> {cfg['html']}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__BRAND__ - Fréquentation</title>
<script>__CHARTJS__</script>
<style>
  :root{--bg:#0c1020;--card:#141a30;--card2:#1d2540;--line:#2a3358;
        --text:#eef1fb;--muted:#97a1c8;--accent:__ACCENT__;--accent2:__ACCENT2__;
        --green:#5fcf8a;--yellow:#e6c14d;--red:#e07a6f;}
  *{box-sizing:border-box;}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);}
  header{padding:28px 32px 12px;} h1{margin:0;font-size:24px;font-weight:800;letter-spacing:.5px;}
  .sub{color:var(--muted);font-size:13px;margin-top:6px;}
  .wrap{padding:0 32px 48px;max-width:1180px;margin:0 auto;}
  .note{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px 18px;margin:18px 0 4px;color:var(--muted);font-size:13px;}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin:22px 0;}
  .kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;}
  .kpi .v{font-size:28px;font-weight:800;color:var(--accent2);}
  .kpi .l{color:var(--muted);font-size:12px;margin-top:4px;text-transform:uppercase;letter-spacing:.6px;}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:22px;}
  @media(max-width:880px){.grid{grid-template-columns:1fr;}}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;}
  .panel h2{margin:0 0 14px;font-size:14px;color:var(--accent2);font-weight:700;}
  canvas{max-height:280px;}
  .filters{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:8px 0 16px;}
  .filters input,.filters select{background:var(--card2);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:9px 12px;font-size:13px;}
  .filters input{flex:1;min-width:160px;}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap;}
  th{color:var(--muted);font-weight:600;cursor:pointer;position:sticky;top:0;background:var(--card);}
  tbody tr:hover{background:var(--card2);}
  .bar{display:inline-block;height:8px;border-radius:5px;vertical-align:middle;margin-left:6px;}
  .ranklist{display:flex;flex-direction:column;gap:9px;}
  .rk{display:grid;grid-template-columns:130px 1fr auto;align-items:center;gap:10px;font-size:13px;}
  .rk .lbl{font-weight:600;overflow:hidden;text-overflow:ellipsis;} .rk .track{height:9px;background:var(--line);border-radius:5px;overflow:hidden;}
  .rk .track>span{display:block;height:100%;background:var(--accent);border-radius:5px;} .rk .val{color:var(--muted);}
  .btn{background:var(--accent);color:#fff;border:none;border-radius:9px;padding:9px 16px;font-size:13px;font-weight:700;cursor:pointer;}
  .tablewrap{max-height:600px;overflow:auto;border:1px solid var(--line);border-radius:14px;}
  .foot{color:var(--muted);font-size:12px;margin-top:18px;}
  @media(max-width:600px){header{padding:18px 14px 6px;}h1{font-size:18px;}.wrap{padding:0 12px 32px;}.kpis{grid-template-columns:1fr 1fr;gap:10px;}.kpi .v{font-size:21px;}.filters input,.filters select,.btn{font-size:15px;width:100%;}.rk{grid-template-columns:96px 1fr auto;}}
</style>
</head>
<body>
<header>
  <h1>__BRAND__ &middot; Fréquentation</h1>
  <div class="sub">généré le __GENERATED__ &middot; présences réelles (Mindbody)</div>
</header>
<div class="wrap">
  <div class="note">ℹ️ Chiffres exacts issus de la billetterie : <b>présents</b> = personnes ayant assisté au cours (cours terminés), <b>réservés</b> = inscriptions (cours à venir). Les stats ci-dessous portent sur les <b>cours terminés</b>. L'historique s'accumule à chaque relevé (la plateforme n'expose qu'une fenêtre de ~7 jours).</div>
  <div class="kpis" id="kpis"></div>
  <div class="filters">
    <input id="q" placeholder="Rechercher (cours, coach...)">
    <select id="fLieu"></select>
    <select id="fCours"></select>
    <select id="fCoach"></select>
  </div>
  <div class="grid">
    <div class="panel"><h2>Présents par jour</h2><canvas id="cDay"></canvas></div>
    <div class="panel"><h2>Présents par studio</h2><canvas id="cLieu"></canvas></div>
  </div>
  <div class="grid">
    <div class="panel"><h2>Présents par type de cours</h2><canvas id="cCours"></canvas></div>
    <div class="panel"><h2>Créneaux horaires les plus fréquentés</h2><div id="topHour" class="ranklist"></div></div>
  </div>
  <div class="panel"><h2>Comparatif des studios &mdash; qui performe (remplissage moyen)</h2><div id="cmpStudios" class="ranklist"></div></div>
  <div class="panel"><h2>Coachs &laquo; stars &raquo; (moyenne de présents / cours)</h2><div id="topCoach" class="ranklist"></div></div>
  <div class="panel">
    <h2>Chiffre d'affaires estimé</h2>
    <div style="margin:0 0 12px;color:var(--muted);font-size:13px">Prix moyen par séance
      <input id="prix" type="number" min="0" step="0.5" value="__PRICE__" style="width:90px;margin-left:8px;background:var(--card2);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:7px 10px;font-size:14px"> &euro; &middot; <span style="font-size:12px">CA = présents &times; prix (séances terminées, filtres appliqués)</span></div>
    <div class="kpis" id="caKpis" style="margin:6px 0 18px"></div>
    <canvas id="cCA"></canvas>
  </div>
  <div class="panel">
    <h2>Détail des séances</h2>
    <div class="filters">
      <button id="btnExport" class="btn">Exporter en Excel</button>
    </div>
    <div class="tablewrap"><table id="tbl"><thead></thead><tbody></tbody></table></div>
  </div>
  <div style="text-align:center;margin:24px 0 6px">
    <button id="btnMaj" class="btn">Mettre à jour (données du jour)</button>
    <span id="majMsg" style="color:var(--muted);font-size:12px;margin-left:10px"></span>
  </div>
  <div class="foot">Source : __HOST__ (Mindbody) &middot; présences réelles &middot; mise à jour auto chaque soir.</div>
</div>
<script>
const ALL=__DATA__;
const API='__API__',PREFIX='__PREFIX__';
const JOURS=["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"];
// clé logique d'une séance (1 studio, 1 créneau, 1 cours, 1 coach) -> évite tout doublon
const lkey=r=>r.date+'|'+r.heure+'|'+r.lieu+'|'+r.cours+'|'+(r.coach||'');
(function(){const m={};ALL.forEach(r=>m[lkey(r)]=r);const u=Object.values(m);if(u.length!==ALL.length){ALL.length=0;u.forEach(r=>ALL.push(r));}})();
const nf=v=>Math.round(v).toLocaleString('fr-FR');
const eur=v=>v.toLocaleString('fr-FR',{maximumFractionDigits:0})+' €';
const fillColor=t=>t>=0.75?'#5fcf8a':t>=0.5?'#e6c14d':'#e07a6f';
function fmtJ(iso){const p=iso.split('-');return `${p[2]}/${p[1]}/${p[0]}`;}
Chart.defaults.color='#97a1c8';Chart.defaults.borderColor='#2a3358';Chart.defaults.font.family=getComputedStyle(document.body).fontFamily;
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const ACC=css('--accent')||'#263fff',ACC2=css('--accent2')||'#6f82ff';
let charts={};
const isNarrow=()=>matchMedia('(max-width:600px)').matches;

let FINIES=ALL.filter(r=>r.finie);   // stats sur les cours terminés (présence réelle)
const selLieu=document.getElementById('fLieu'),selCours=document.getElementById('fCours'),selCoach=document.getElementById('fCoach');
function fillSel(sel,vals,label){sel.innerHTML='';sel.add(new Option(label,''));[...new Set(vals)].filter(Boolean).sort().forEach(v=>sel.add(new Option(v,v)));}
fillSel(selLieu,ALL.map(r=>r.lieu),'Tous les studios');
fillSel(selCours,ALL.map(r=>r.cours),'Tous les cours');
fillSel(selCoach,ALL.map(r=>r.coach),'Tous les coachs');

function current(){
  const q=document.getElementById('q').value.toLowerCase();
  return FINIES.filter(r=>(!selLieu.value||r.lieu===selLieu.value)
    &&(!selCours.value||r.cours===selCours.value)
    &&(!selCoach.value||r.coach===selCoach.value)
    &&(!q||((r.cours+' '+r.coach+' '+r.lieu).toLowerCase().includes(q))));
}
function mkChart(id,cfg){if(charts[id])charts[id].destroy();charts[id]=new Chart(document.getElementById(id),cfg);}

function render(){
  const D=current();
  const totPres=D.reduce((s,r)=>s+r.presents,0);
  const totCap=D.reduce((s,r)=>s+r.capacite,0);
  const totRes=D.reduce((s,r)=>s+r.reserves,0);
  const totNo=D.reduce((s,r)=>s+(r.noshow||0),0);
  const avg=D.length?totPres/D.length:0;
  const nbStudios=new Set(D.map(r=>r.lieu)).size||1;
  document.getElementById('kpis').innerHTML=[
    ['Présents (total)',nf(totPres)],
    ['Studios',nf(nbStudios)],
    ['Présents / studio',nf(totPres/nbStudios)],
    ['Séances terminées',nf(D.length)],
    ['Moyenne / séance',D.length?avg.toFixed(1):'—'],
    ['Taux de remplissage',totCap?Math.round(100*totPres/totCap)+'%':'—'],
    ['No-shows (total)',nf(totNo)+(totRes?` (${Math.round(100*totNo/totRes)}%)`:'')],
  ].map(k=>`<div class="kpi"><div class="v">${k[1]}</div><div class="l">${k[0]}</div></div>`).join('');

  // présents par jour
  const byDay={};D.forEach(r=>byDay[r.date]=(byDay[r.date]||0)+r.presents);
  const days=Object.keys(byDay).sort();
  mkChart('cDay',{type:'bar',data:{labels:days.map(d=>d.slice(8)+'/'+d.slice(5,7)),
    datasets:[{label:'présents',data:days.map(d=>byDay[d]),backgroundColor:ACC}]},
    options:{plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,ticks:{callback:v=>nf(v)}}}}});

  // chiffre d'affaires estimé
  const prix=parseFloat(document.getElementById('prix').value)||0;
  try{localStorage.setItem('__PRIXKEY__',prix);}catch(e){}
  const nbJours=days.length||1;
  const totalCA=totPres*prix;
  document.getElementById('caKpis').innerHTML=[
    ['CA total estimé',eur(totalCA)],
    ['CA / studio (moy.)',eur(totalCA/nbStudios)],
    ['CA / jour (moy.)',eur(totalCA/nbJours)],
    ['CA / séance (moy.)',eur(D.length?totalCA/D.length:0)],
  ].map(k=>`<div class="kpi"><div class="v">${k[1]}</div><div class="l">${k[0]}</div></div>`).join('');
  mkChart('cCA',{type:'bar',data:{labels:days.map(d=>d.slice(8)+'/'+d.slice(5,7)),
    datasets:[{data:days.map(d=>byDay[d]*prix),backgroundColor:'#5fcf8a'}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>eur(c.parsed.y)}}},
      scales:{y:{beginAtZero:true,ticks:{callback:v=>v.toLocaleString('fr-FR')+' €'}}}}});

  // présents par studio
  const byL={};D.forEach(r=>byL[r.lieu]=(byL[r.lieu]||0)+r.presents);
  const lieux=Object.keys(byL).sort((a,b)=>byL[b]-byL[a]);
  mkChart('cLieu',{type:'doughnut',data:{labels:lieux,datasets:[{data:lieux.map(l=>byL[l]),
    backgroundColor:[ACC,ACC2,'#5fcf8a','#e6c14d','#e07a6f','#b07ff0','#f0a26f','#6fd0e0','#e06f9c']}]},
    options:{plugins:{legend:{position:isNarrow()?'bottom':'right'}}}});

  // présents par type de cours
  const byC={};D.forEach(r=>byC[r.cours]=(byC[r.cours]||0)+r.presents);
  const cours=Object.keys(byC).sort((a,b)=>byC[b]-byC[a]);
  mkChart('cCours',{type:'bar',data:{labels:cours,datasets:[{label:'présents',data:cours.map(c=>byC[c]),backgroundColor:ACC2}]},
    options:{indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{beginAtZero:true,ticks:{callback:v=>nf(v)}}}}});

  // créneaux horaires
  const byH={};D.forEach(r=>{byH[r.heure]=byH[r.heure]||{p:0,n:0};byH[r.heure].p+=r.presents;byH[r.heure].n++;});
  const hrs=Object.entries(byH).sort((a,b)=>b[1].p-a[1].p).slice(0,8);
  const mxH=hrs.length?hrs[0][1].p:0;
  document.getElementById('topHour').innerHTML=hrs.length?hrs.map(([h,o])=>
    `<div class="rk"><span class="lbl">${h}</span><span class="track"><span style="width:${mxH?Math.round(100*o.p/mxH):0}%"></span></span><span class="val">${nf(o.p)} (${nf(o.p/o.n)}/séance)</span></div>`).join('')
    :'<div style="color:var(--muted)">Pas encore de données.</div>';

  // comparatif des studios : taux de remplissage moyen + présents/séance (vert = performe, rouge = faible)
  const byS={};D.forEach(r=>{byS[r.lieu]=byS[r.lieu]||{p:0,c:0,n:0};byS[r.lieu].p+=r.presents;byS[r.lieu].c+=r.capacite;byS[r.lieu].n++;});
  const studios=Object.entries(byS).map(([k,o])=>[k,o.c?o.p/o.c:0,o.p/o.n,o.n]).sort((a,b)=>b[1]-a[1]);
  document.getElementById('cmpStudios').innerHTML=studios.length?studios.map(([k,taux,moy,n])=>
    `<div class="rk"><span class="lbl">${k}</span><span class="track"><span style="width:${Math.round(100*taux)}%;background:${fillColor(taux)}"></span></span><span class="val">${Math.round(100*taux)}% &middot; ${moy.toFixed(1)}/séance &middot; ${n} cours</span></div>`).join('')
    :'<div style="color:var(--muted)">Pas encore de données.</div>';

  // coachs stars (moyenne présents/cours)
  const byCo={};D.forEach(r=>{if(!r.coach)return;byCo[r.coach]=byCo[r.coach]||{p:0,n:0};byCo[r.coach].p+=r.presents;byCo[r.coach].n++;});
  const coachs=Object.entries(byCo).map(([k,o])=>[k,o.p/o.n,o.n]).sort((a,b)=>b[1]-a[1]).slice(0,10);
  const mxC=coachs.length?coachs[0][1]:0;
  document.getElementById('topCoach').innerHTML=coachs.length?coachs.map(([k,m,n])=>
    `<div class="rk"><span class="lbl">${k}</span><span class="track"><span style="width:${mxC?Math.round(100*m/mxC):0}%"></span></span><span class="val">${m.toFixed(1)}/séance &middot; ${n} cours</span></div>`).join('')
    :'<div style="color:var(--muted)">Pas encore de données.</div>';

  renderTable(D);
}

const cols=[['date','Date'],['jour','Jour'],['heure','Heure'],['lieu','Studio'],['cours','Cours'],['coach','Coach'],['presents','Présents'],['reserves','Réservés'],['noshow','No-show'],['capacite','Capacité']];
document.querySelector('#tbl thead').innerHTML='<tr>'+cols.map(c=>`<th>${c[1]}</th>`).join('')+'</tr>';
let currentRows=[];
function renderTable(D){
  let rows=[...D].sort((a,b)=>(a.date+a.heure)<(b.date+b.heure)?1:-1);
  currentRows=rows;
  document.querySelector('#tbl tbody').innerHTML=rows.map(r=>{
    const t=r.capacite?r.presents/r.capacite:0;
    const ns=r.noshow||0;
    return `<tr><td>${fmtJ(r.date)}</td><td>${r.jour}</td><td>${r.heure}</td><td>${r.lieu}</td><td>${r.cours}</td><td>${r.coach||'—'}</td>`
      +`<td><b>${r.presents}</b> / ${r.capacite}<span class="bar" style="width:${Math.round(40*t)}px;background:${fillColor(t)}"></span></td>`
      +`<td>${r.reserves}</td><td>${ns?'<b style="color:#e07a6f">'+ns+'</b>':'0'}</td><td>${r.capacite}</td></tr>`;}).join('');
}
['q','prix'].forEach(id=>document.getElementById(id).addEventListener('input',render));
[selLieu,selCours,selCoach].forEach(s=>s.addEventListener('change',render));
const _sp=(()=>{try{return localStorage.getItem('__PRIXKEY__');}catch(e){return null;}})();
if(_sp)document.getElementById('prix').value=_sp;

// mise à jour live depuis l'API (CORS autorisé) : fusionne les cours du jour
function shortLoc(n){n=(n||'').trim();if(PREFIX&&n.toLowerCase().startsWith(PREFIX.toLowerCase()))n=n.slice(PREFIX.length).trim();return n.replace(/^-+/,'').trim()||n;}
function refreshFilters(){const a=selLieu.value,b=selCours.value,c=selCoach.value;
  fillSel(selLieu,ALL.map(r=>r.lieu),'Tous les studios');fillSel(selCours,ALL.map(r=>r.cours),'Tous les cours');fillSel(selCoach,ALL.map(r=>r.coach),'Tous les coachs');
  selLieu.value=a;selCours.value=b;selCoach.value=c;}
async function majNow(){
  const btn=document.getElementById('btnMaj'),msg=document.getElementById('majMsg'),old=btn.textContent;
  btn.disabled=true;btn.textContent='Mise à jour…';msg.textContent='';
  try{
    const r=await fetch(API,{headers:{'Accept':'application/json'}});
    if(!r.ok)throw new Error('HTTP '+r.status);
    const data=await r.json(),now=new Date();
    const byKey={};ALL.forEach(x=>byKey[lkey(x)]=x);   // fusion par séance (pas par id) -> zéro doublon
    let add=0;
    data.forEach(c=>{if(c.IsCanceled||!c.StartDateTime)return;
      const s=c.StartDateTime,e=c.EndDateTime||s,sd=new Date(s);
      const row={id:String(c.Id),date:s.slice(0,10),jour:JOURS[(sd.getDay()+6)%7],heure:s.slice(11,16),fin:e.slice(11,16),
        lieu:shortLoc((c.Location||{}).Name),cours:((c.ClassDescription||{}).Name||'').trim(),
        coach:((c.Staff||{}).Name||'').trim(),capacite:c.MaxCapacity||0,reserves:c.TotalBooked||0,
        presents:c.TotalSignedIn||0,finie:now>=new Date(e)};
      row.noshow=Math.max(0,row.reserves-row.presents);
      const k=lkey(row);if(!(k in byKey))add++;byKey[k]=row;});
    ALL.length=0;Object.values(byKey).forEach(x=>ALL.push(x));
    FINIES=ALL.filter(x=>x.finie);refreshFilters();render();
    msg.textContent='À jour ✓ '+now.toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'})+(add?` (+${add} séances)`:' (aucune nouvelle)');
  }catch(err){msg.textContent='Échec : '+err.message;}
  btn.disabled=false;btn.textContent=old;
}
document.getElementById('btnMaj').addEventListener('click',majNow);
document.getElementById('btnExport').addEventListener('click',()=>{
  const esc=v=>{v=(''+v).replace(/"/g,'""');return /[";\n]/.test(v)?`"${v}"`:v;};
  const lines=[cols.map(c=>c[1]).join(';')];
  currentRows.forEach(r=>lines.push(cols.map(c=>esc(c[0]==='date'?fmtJ(r.date):(r[c[0]]==null?'':r[c[0]]))).join(';')));
  const blob=new Blob(['﻿'+lines.join('\r\n')],{type:'text/csv;charset=utf-8;'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='__CSVNAME__';document.body.appendChild(a);a.click();a.remove();
});
render();
</script>
</body>
</html>"""


def run(cfg):
    store = capture(cfg)
    # dédoublonnage par séance logique (1 studio / créneau / cours / coach) :
    # si un id a changé entre deux relevés, on garde le relevé le plus récent.
    best = {}
    for r in store.values():
        k = (r["date"], r["heure"], r.get("lieu", ""), r.get("cours", ""), r.get("coach", ""))
        cur = best.get(k)
        if cur is None or (bool(r.get("finie")), r.get("releve", "")) > (bool(cur.get("finie")), cur.get("releve", "")):
            best[k] = r
    rows = sorted(best.values(), key=lambda r: (r["date"], r["heure"], r.get("lieu", "")))
    for r in rows:
        r["noshow"] = max(0, (r.get("reserves") or 0) - (r.get("presents") or 0))
    write_csv(rows, cfg["csv"])
    write_html(rows, cfg)
    fin = [r for r in rows if r.get("finie")]
    print(f"OK [{cfg['key']}]: {len(rows)} cours en base, {len(fin)} terminés.")
