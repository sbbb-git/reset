#!/usr/bin/env python3
"""Scrap fréquentation EPISOD (resamania / plugin WordPress wplugin-resamania).

Pas d'API JSON publique : la plateforme verrouille la REST WP (401) et n'a
pas d'endpoint resamania exposé. En revanche TOUT est rendu en HTML côté
serveur :

  * https://www.episod.com/planning/        -> liste des séances (id, date,
    heure, cours, coach, hub/studio) pour ~7 jours glissants.
  * https://www.episod.com/reservation/<id>/ -> plan de salle SVG dont chaque
    place porte une classe `type-<forme>` + `bookable` (libre) ou `unbookable`
    (prise). On en déduit capacité = places, présents/réservés = unbookable
    (hors `type-coach` et `type-line` décoratifs).

CORS : le site ne renvoie AUCUN header Access-Control-Allow-Origin -> un
fetch navigateur depuis github.io est bloqué. Donc PAS de bouton « Mettre à
jour » live ; seul ce robot (côté serveur, sans CORS) alimente les données.

On accumule chaque relevé dans episod_data.json (clé = id de séance). Une
séance terminée a un décompte définitif. Conçu pour tourner souvent afin de
figer le remplissage juste avant chaque séance.
"""
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
BASE = "https://www.episod.com"
PLANNING = BASE + "/planning/"
RESA = BASE + "/reservation/{}/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
FIELDS = ["date", "jour", "heure", "fin", "lieu", "cours", "coach",
          "capacite", "presents", "finie", "releve"]

KEY = "episod"
STORE = "episod_data.json"
HTML = "episod.html"
CSV = "episod_seances.csv"
PRICE = 30
ACCENT = "#111111"
ACCENT2 = "#e8c14d"
HOST = "episod.com"
BRAND = "EPISOD"


def _fetch(url, retries=4):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "fr-FR,fr;q=0.9"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"echec requete {url}: {last}")


def _clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse_planning(html):
    """Renvoie une liste de séances (métadonnées, sans capacité)."""
    out = []
    blocks = re.findall(r"(<li id=\"session-\d+\".*?</li>)", html, re.S)
    for b in blocks:
        sid = re.search(r"session-(\d+)", b).group(1)

        def f(pat, default=""):
            m = re.search(pat, b, re.S)
            return _clean(m.group(1)) if m else default

        jour = f(r'data-jour="(\d)"')
        ddmm = f(r'masterclass-txt">([0-9]{2}/[0-9]{2})</div>')
        heure = f(r"<time[^>]*>([^<]+)</time>").replace("H", ":").strip()
        cours = f(r'data-type="sport"[^>]*>([^<]+)</h2>')
        coach = f(r'data-type="coach"[^>]*>([^<]+)</h4>')
        hubraw = f(r'data-type="hub"[^>]*>(.*?)</address>')
        hubslug = (re.search(r'data-hub="([^"]*)"', b) or [None, ""])[1]
        out.append({
            "id": sid, "jour_idx": jour, "ddmm": ddmm, "heure": heure,
            "cours": cours, "coach": coach, "lieu": hubraw, "hub": hubslug,
        })
    return out


def parse_seatmap(html):
    """(capacite, presents) d'après le plan de salle. coach/line exclus.

    Renvoie (0, 0) si aucun plan (certains types de séance sans placement)."""
    seats = re.findall(r'class="(type-[a-z-]+ [^"]*?(?:bookable|unbookable)[^"]*)"', html)
    cap = pres = 0
    for s in seats:
        if "type-coach" in s or "type-line" in s:
            continue
        cap += 1
        if "unbookable" in s:
            pres += 1
    return cap, pres


def resolve_date(ddmm, now):
    """dd/mm -> date complète : année courante, +1 an si déjà passé de loin."""
    if not re.match(r"\d{2}/\d{2}$", ddmm or ""):
        return None
    d, m = int(ddmm[:2]), int(ddmm[3:5])
    for yr in (now.year, now.year + 1, now.year - 1):
        try:
            cand = dt.date(yr, m, d)
        except ValueError:
            continue
        if abs((cand - now.date()).days) <= 200:
            return cand
    return None


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


LOCK_BEFORE = 20   # min : on commence à sonder le plan de salle 20 min avant le début
MAX_FETCH = 90     # plafond de plans de salle lus par run (borne le temps d'exécution)


def capture():
    now = dt.datetime.now(PARIS)
    store = load_store(STORE)
    try:
        sessions = parse_planning(_fetch(PLANNING))
    except Exception as e:  # noqa: BLE001
        print(f"  (planning indispo : {e})", file=sys.stderr)
        return store
    fetched = withmap = 0
    for s in sessions:
        date = resolve_date(s["ddmm"], now)
        if not date or not s["heure"]:
            continue
        try:
            sdt = dt.datetime.combine(date, dt.time(int(s["heure"][:2]), int(s["heure"][3:5])), PARIS)
        except (ValueError, IndexError):
            continue
        edt = sdt + dt.timedelta(minutes=50)  # durée ~50 min (inconnue dans le planning)
        sid = s["id"]
        prev = store.get(sid) or {}
        rec = {
            "id": sid, "date": date.isoformat(), "jour": JOURS_FR[date.weekday()],
            "heure": s["heure"], "fin": edt.strftime("%H:%M"),
            "lieu": s["lieu"] or s["hub"], "cours": s["cours"], "coach": s["coach"],
            "capacite": prev.get("capacite", 0), "presents": prev.get("presents", 0),
            "finie": now >= edt, "releve": prev.get("releve", now.strftime("%Y-%m-%d %H:%M")),
        }
        # On ne lit le plan de salle QUE pour les séances proches de leur début
        # (fenêtre [-20 min, +60 min]) et pas déjà figées -> peu de requêtes/run.
        locked = prev.get("finie") and prev.get("capacite")
        in_window = (now >= sdt - dt.timedelta(minutes=LOCK_BEFORE)) and (now < edt + dt.timedelta(minutes=10))
        if (not locked) and in_window and fetched < MAX_FETCH:
            try:
                cap, pres = parse_seatmap(_fetch(RESA.format(sid)))
                fetched += 1
                if cap:
                    withmap += 1
                    rec["capacite"], rec["presents"] = cap, pres
                    rec["releve"] = now.strftime("%Y-%m-%d %H:%M")
            except Exception as e:  # noqa: BLE001
                print(f"  (resa {sid} échouée : {e})", file=sys.stderr)
        store[sid] = rec
    save_store(store, STORE)
    fin = sum(1 for v in store.values() if v.get("finie"))
    print(f"[{KEY}] {now:%Y-%m-%d %H:%M} : {len(sessions)} au planning, {fetched} plans lus "
          f"({withmap} avec capacité), {len(store)} en base ({fin} terminées).")
    return store


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"-> {path}")


def write_html(rows):
    chartjs = ""
    vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor_chartjs.min.js")
    if os.path.exists(vendor):
        with open(vendor, encoding="utf-8") as f:
            chartjs = f.read()
    html = (HTML_TEMPLATE
            .replace("__CHARTJS__", chartjs)
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
            .replace("__GENERATED__", dt.datetime.now(PARIS).strftime("%d/%m/%Y %H:%M"))
            .replace("__BRAND__", BRAND)
            .replace("__PRICE__", str(PRICE))
            .replace("__PRIXKEY__", KEY + "_prix")
            .replace("__CSVNAME__", KEY + "_seances.csv")
            .replace("__ACCENT__", ACCENT)
            .replace("__ACCENT2__", ACCENT2)
            .replace("__HOST__", HOST))
    with open(HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"-> {HTML}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__BRAND__ - Fréquentation</title>
<script>__CHARTJS__</script>
<style>
  :root{--bg:#0c0c0e;--card:#161618;--card2:#202024;--line:#2e2e34;
        --text:#f4f3ef;--muted:#9a9aa2;--accent:__ACCENT__;--accent2:__ACCENT2__;
        --green:#5fcf8a;--yellow:#e6c14d;--red:#e07a6f;}
  *{box-sizing:border-box;}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);}
  header{padding:28px 32px 12px;} h1{margin:0;font-size:24px;font-weight:800;letter-spacing:1.5px;}
  .sub{color:var(--muted);font-size:13px;margin-top:6px;}
  .wrap{padding:0 32px 48px;max-width:1180px;margin:0 auto;}
  .note{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px 18px;margin:18px 0 4px;color:var(--muted);font-size:13px;}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:16px;margin:22px 0;}
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
  .rk{display:grid;grid-template-columns:150px 1fr auto;align-items:center;gap:10px;font-size:13px;}
  .rk .lbl{font-weight:600;overflow:hidden;text-overflow:ellipsis;} .rk .track{height:9px;background:var(--line);border-radius:5px;overflow:hidden;}
  .rk .track>span{display:block;height:100%;background:var(--accent2);border-radius:5px;} .rk .val{color:var(--muted);}
  .btn{background:var(--accent2);color:#111;border:none;border-radius:9px;padding:9px 16px;font-size:13px;font-weight:700;cursor:pointer;}
  .tablewrap{max-height:600px;overflow:auto;border:1px solid var(--line);border-radius:14px;}
  .foot{color:var(--muted);font-size:12px;margin-top:18px;}
  @media(max-width:600px){header{padding:18px 14px 6px;}h1{font-size:18px;}.wrap{padding:0 12px 32px;}.kpis{grid-template-columns:1fr 1fr;gap:10px;}.kpi .v{font-size:21px;}.filters input,.filters select,.btn{font-size:15px;width:100%;}.rk{grid-template-columns:110px 1fr auto;}}
</style>
</head>
<body>
<header>
  <h1>__BRAND__ &middot; Fréquentation</h1>
  <div class="sub">généré le __GENERATED__ &middot; remplissage des plans de salle (resamania)</div>
</header>
<div class="wrap">
  <div class="note">ℹ️ Chiffres lus sur les plans de salle de chaque séance : <b>présents</b> = places prises, <b>capacité</b> = places totales. Le décompte <b>s'arrête à la première séance pas encore commencée</b> (les séances à venir, encore en remplissage, sont exclues). Un robot capte le remplissage ~5 min avant chaque séance ; l'historique s'accumule jour après jour. Les séances sans plan de salle (capacité 0) sont ignorées.</div>
  <div id="periode" style="background:var(--card2);border:1px solid var(--line);border-left:4px solid var(--accent2);border-radius:10px;padding:11px 16px;margin:14px 0 4px;color:var(--text);font-size:13.5px;font-weight:600"></div>
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
    <div class="filters"><button id="btnExport" class="btn">Exporter en Excel</button></div>
    <div class="tablewrap"><table id="tbl"><thead></thead><tbody></tbody></table></div>
  </div>
  <div class="foot">Source : __HOST__ (resamania) &middot; mise à jour auto ~5 min avant chaque séance.</div>
</div>
<script>
const ALL=__DATA__;
const JOURS=["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"];
const lkey=r=>r.date+'|'+r.heure+'|'+r.lieu+'|'+r.cours+'|'+(r.coach||'');
(function(){const m={};ALL.forEach(r=>m[lkey(r)]=r);const u=Object.values(m);if(u.length!==ALL.length){ALL.length=0;u.forEach(r=>ALL.push(r));}})();
const nf=v=>Math.round(v).toLocaleString('fr-FR');
const eur=v=>v.toLocaleString('fr-FR',{maximumFractionDigits:0})+' €';
const fillColor=t=>t>=0.75?'#5fcf8a':t>=0.5?'#e6c14d':'#e07a6f';
function fmtJ(iso){const p=iso.split('-');return `${p[2]}/${p[1]}/${p[0]}`;}
Chart.defaults.color='#9a9aa2';Chart.defaults.borderColor='#2e2e34';Chart.defaults.font.family=getComputedStyle(document.body).fontFamily;
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const ACC=css('--accent2')||'#e8c14d',ACC2=css('--accent2')||'#e8c14d';
let charts={};
const isNarrow=()=>matchMedia('(max-width:600px)').matches;

// on ne garde que les séances avec un plan de salle (capacité connue)
const valid=r=>r.capacite>0;
// on compte ce qui a DÉMARRÉ ; on s'arrête à la 1re séance à venir (calcul live)
const started=r=>new Date(r.date+'T'+(r.heure||'00:00')+':00')<=new Date();
let FINIES=ALL.filter(r=>valid(r)&&started(r));
const selLieu=document.getElementById('fLieu'),selCours=document.getElementById('fCours'),selCoach=document.getElementById('fCoach');
function fillSel(sel,vals,label){sel.innerHTML='';sel.add(new Option(label,''));[...new Set(vals)].filter(Boolean).sort().forEach(v=>sel.add(new Option(v,v)));}
fillSel(selLieu,ALL.filter(valid).map(r=>r.lieu),'Tous les studios');
fillSel(selCours,ALL.filter(valid).map(r=>r.cours),'Tous les cours');
fillSel(selCoach,ALL.filter(valid).map(r=>r.coach),'Tous les coachs');

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
  const avg=D.length?totPres/D.length:0;
  const nbStudios=new Set(D.map(r=>r.lieu)).size||1;
  const dts=[...new Set(D.map(r=>r.date))].sort(),pj=dts.length;
  document.getElementById('periode').textContent = pj
    ? `📅 Période étudiée : du ${fmtJ(dts[0])} au ${fmtJ(dts[pj-1])} · ${pj} jour${pj>1?'s':''} · ${nbStudios} studio${nbStudios>1?'s':''} · ${nf(D.length)} séances comptées`
    : 'Aucune séance commencée sur cette sélection.';
  document.getElementById('kpis').innerHTML=[
    ['Présents (total)',nf(totPres)],
    ['Studios',nf(nbStudios)],
    ['Présents / studio',nf(totPres/nbStudios)],
    ['Séances comptées',nf(D.length)],
    ['Moyenne / séance',D.length?avg.toFixed(1):'—'],
    ['Taux de remplissage',totCap?Math.round(100*totPres/totCap)+'%':'—'],
  ].map(k=>`<div class="kpi"><div class="v">${k[1]}</div><div class="l">${k[0]}</div></div>`).join('');

  const byDay={};D.forEach(r=>byDay[r.date]=(byDay[r.date]||0)+r.presents);
  const days=Object.keys(byDay).sort();
  mkChart('cDay',{type:'bar',data:{labels:days.map(d=>d.slice(8)+'/'+d.slice(5,7)),
    datasets:[{label:'présents',data:days.map(d=>byDay[d]),backgroundColor:ACC2}]},
    options:{plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,ticks:{callback:v=>nf(v)}}}}});

  const prix=parseFloat(document.getElementById('prix').value)||0;
  try{localStorage.setItem('__PRIXKEY__',prix);}catch(e){}
  const nbJours=days.length||1,totalCA=totPres*prix;
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

  const byL={};D.forEach(r=>byL[r.lieu]=(byL[r.lieu]||0)+r.presents);
  const lieux=Object.keys(byL).sort((a,b)=>byL[b]-byL[a]);
  mkChart('cLieu',{type:'doughnut',data:{labels:lieux,datasets:[{data:lieux.map(l=>byL[l]),
    backgroundColor:[ACC2,'#5fcf8a','#e07a6f','#b07ff0','#f0a26f','#6fd0e0','#e06f9c','#9ce06f','#6f9cff','#d0b06f','#cfcfcf']}]},
    options:{plugins:{legend:{position:isNarrow()?'bottom':'right'}}}});

  const byC={};D.forEach(r=>byC[r.cours]=(byC[r.cours]||0)+r.presents);
  const cours=Object.keys(byC).sort((a,b)=>byC[b]-byC[a]);
  mkChart('cCours',{type:'bar',data:{labels:cours,datasets:[{label:'présents',data:cours.map(c=>byC[c]),backgroundColor:ACC2}]},
    options:{indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{beginAtZero:true,ticks:{callback:v=>nf(v)}}}}});

  const byH={};D.forEach(r=>{byH[r.heure]=byH[r.heure]||{p:0,n:0};byH[r.heure].p+=r.presents;byH[r.heure].n++;});
  const hrs=Object.entries(byH).sort((a,b)=>b[1].p-a[1].p).slice(0,8);
  const mxH=hrs.length?hrs[0][1].p:0;
  document.getElementById('topHour').innerHTML=hrs.length?hrs.map(([h,o])=>
    `<div class="rk"><span class="lbl">${h}</span><span class="track"><span style="width:${mxH?Math.round(100*o.p/mxH):0}%"></span></span><span class="val">${nf(o.p)} (${nf(o.p/o.n)}/séance)</span></div>`).join('')
    :'<div style="color:var(--muted)">Pas encore de données.</div>';

  const byS={};D.forEach(r=>{byS[r.lieu]=byS[r.lieu]||{p:0,c:0,n:0};byS[r.lieu].p+=r.presents;byS[r.lieu].c+=r.capacite;byS[r.lieu].n++;});
  const studios=Object.entries(byS).map(([k,o])=>[k,o.c?o.p/o.c:0,o.p/o.n,o.n]).sort((a,b)=>b[1]-a[1]);
  document.getElementById('cmpStudios').innerHTML=studios.length?studios.map(([k,taux,moy,n])=>
    `<div class="rk"><span class="lbl">${k}</span><span class="track"><span style="width:${Math.round(100*taux)}%;background:${fillColor(taux)}"></span></span><span class="val">${Math.round(100*taux)}% &middot; ${moy.toFixed(1)}/séance &middot; ${n} cours</span></div>`).join('')
    :'<div style="color:var(--muted)">Pas encore de données.</div>';

  const byCo={};D.forEach(r=>{if(!r.coach)return;byCo[r.coach]=byCo[r.coach]||{p:0,n:0};byCo[r.coach].p+=r.presents;byCo[r.coach].n++;});
  const coachs=Object.entries(byCo).map(([k,o])=>[k,o.p/o.n,o.n]).sort((a,b)=>b[1]-a[1]).slice(0,10);
  const mxC=coachs.length?coachs[0][1]:0;
  document.getElementById('topCoach').innerHTML=coachs.length?coachs.map(([k,m,n])=>
    `<div class="rk"><span class="lbl">${k}</span><span class="track"><span style="width:${mxC?Math.round(100*m/mxC):0}%"></span></span><span class="val">${m.toFixed(1)}/séance &middot; ${n} cours</span></div>`).join('')
    :'<div style="color:var(--muted)">Pas encore de données.</div>';

  renderTable(D);
}

const cols=[['date','Date'],['jour','Jour'],['heure','Heure'],['lieu','Studio'],['cours','Cours'],['coach','Coach'],['presents','Présents'],['capacite','Capacité']];
document.querySelector('#tbl thead').innerHTML='<tr>'+cols.map(c=>`<th>${c[1]}</th>`).join('')+'</tr>';
let currentRows=[];
function renderTable(D){
  let rows=[...D].sort((a,b)=>(a.date+a.heure)<(b.date+b.heure)?1:-1);
  currentRows=rows;
  document.querySelector('#tbl tbody').innerHTML=rows.map(r=>{
    const t=r.capacite?r.presents/r.capacite:0;
    return `<tr><td>${fmtJ(r.date)}</td><td>${r.jour}</td><td>${r.heure}</td><td>${r.lieu}</td><td>${r.cours}</td><td>${r.coach||'—'}</td>`
      +`<td><b>${r.presents}</b> / ${r.capacite}<span class="bar" style="width:${Math.round(40*t)}px;background:${fillColor(t)}"></span></td>`
      +`<td>${r.capacite}</td></tr>`;}).join('');
}
['q','prix'].forEach(id=>document.getElementById(id).addEventListener('input',render));
[selLieu,selCours,selCoach].forEach(s=>s.addEventListener('change',render));
const _sp=(()=>{try{return localStorage.getItem('__PRIXKEY__');}catch(e){return null;}})();
if(_sp)document.getElementById('prix').value=_sp;
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


def run():
    store = capture()
    best = {}
    for r in store.values():
        k = (r["date"], r["heure"], r.get("lieu", ""), r.get("cours", ""), r.get("coach", ""))
        cur = best.get(k)
        if cur is None or (bool(r.get("finie")), r.get("releve", "")) > (bool(cur.get("finie")), cur.get("releve", "")):
            best[k] = r
    rows = sorted(best.values(), key=lambda r: (r["date"], r["heure"], r.get("lieu", "")))
    write_csv(rows, CSV)
    write_html(rows)
    fin = [r for r in rows if r.get("finie")]
    print(f"OK [{KEY}]: {len(rows)} séances en base, {len(fin)} terminées.")


if __name__ == "__main__":
    run()
