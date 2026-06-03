#!/usr/bin/env python3
"""Occupation des terrains Anybuddy (padel/tennis/squash/pelote).

Cas particulier : pas de "présents". On mesure l'OCCUPATION des terrains.
Anybuddy ne renvoie QUE les créneaux DISPONIBLES (un terrain libre à une heure
donnée). Endpoint (proxy Next.js de anybuddyapp.com, le backend api.anybuddyapp
.com refuse l'appel direct sans en-têtes internes) :

  GET https://www.anybuddyapp.com/api/v1/availabilities
      ?clubSlug=<slug>&dateFrom=<ISO>&dateTo=<ISO>&activity=<padel|tennis|...>

Réponse : {"data":[{"startDateTime":"2026-05-30T08:00",
  "services":[{"id":<court uuid>,"duration":60|90,"price":<centimes>,
  "availablePlaces":4,"totalCapacity":4,...}]}]}

ATTENTION sur le modèle : availablePlaces / totalCapacity = la TAILLE DE PARTIE
(4 joueurs sur un court de padel), PAS le nombre de terrains. Le nombre de
terrains n'est PAS donné directement ; chaque entrée "service" = UN terrain
LIBRE à ce créneau (les 4 courts du club ont chacun leur uuid). Donc :
  - on n'a PAS "total terrains + dispo" pour calculer l'occupation directement ;
  - on a SEULEMENT la liste des terrains libres -> logique "créneau disparu = réservé".

Stratégie : scraper souvent (~5-15 min) la fenêtre aujourd'hui->+7j. Pour chaque
offre (date|heure|terrain|durée) on note vu_dispo=true et le dernier relevé.
Quand une offre vue dispo DISPARAÎT des dispos alors qu'elle n'est pas encore
passée -> réservée (statut "booked"). En accumulant sur une semaine on
reconstruit le taux d'occupation par jour / créneau / terrain.

CORS fermé (pas d'en-tête Access-Control-Allow-Origin) -> pas de bouton live,
mise à jour côté serveur uniquement.
"""
import csv
import datetime as dt
import json
import os
import safestore
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
ORIGIN = "https://www.anybuddyapp.com"
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
HORIZON_JOURS = 7
FIELDS = ["date", "jour", "heure", "fin", "terrain", "duree", "prix",
          "statut", "vu_dispo", "premier_vu", "dernier_vu", "releve"]

# Club ciblé. Les noms de terrains (uuid -> libellé) sont issus du centerData
# de la page club ; complétés automatiquement par ce qui est vu dans l'API.
CONFIG = {
    "key": "anybuddy", "brand": "TRINQUET VILLAGE PARIS",
    "slug": "trinquet-village-paris",
    "activities": ["padel"],
    "host": "anybuddyapp.com",
    "store": "anybuddy_data.json", "html": "anybuddy.html",
    "csv": "anybuddy_creneaux.csv", "price": 54,
    "accent": "#2CDF62", "accent2": "#7af0a0",
    "courts": {
        "fa3e549a-2c3f-4785-b984-0d11bf8b9070": "Padel 1 (gauche)",
        "4dcaafea-826d-47db-9bae-24b9b101e4d6": "Padel 2",
        "c742dd6c-f37a-4129-a282-fffef4c5147c": "Padel 3",
        "ad06e932-5518-4849-bdd5-ed20a52cee22": "Padel 4 (droite)",
        # ids alternatifs vus dans le centerData (mêmes terrains)
        "db1eb2b6-9892-4c77-9440-d8434d9cf91a": "Padel 1 (gauche)",
        "e8443777-66b9-49d0-9dc2-dd8ddf673ba8": "Padel 2",
        "cb48fead-e6ea-4966-9b93-d437ff4fdb49": "Padel 3",
        "711e70fe-7f04-4562-a347-c0227f9eff9d": "Padel 4 (droite)",
    },
}


def _get(url, retries=4):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "application/json",
                "Referer": f"{ORIGIN}/fr/club/"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"echec requete {url}: {last}")


def fetch_day(slug, activity, day):
    """Renvoie la liste des créneaux disponibles d'un jour pour une activité."""
    params = urllib.parse.urlencode({
        "clubSlug": slug,
        "dateFrom": f"{day.isoformat()}T00:00",
        "dateTo": f"{day.isoformat()}T23:59",
        "activity": activity})
    url = f"{ORIGIN}/api/v1/availabilities?{params}"
    d = _get(url)
    return (d or {}).get("data", []) or []


def load_store(path):
    return safestore.load(path)


def save_store(store, path):
    safestore.save(store, path)


def court_name(cfg, cid):
    return cfg["courts"].get(cid) or f"Terrain {cid[:8]}"


def capture(cfg):
    now = dt.datetime.now(PARIS)
    store = load_store(cfg["store"])
    today = now.date()
    days = [today + dt.timedelta(days=i) for i in range(HORIZON_JOURS + 1)]

    # 1) Ensemble des offres VUES DISPONIBLES à ce relevé (clé = date|heure|terrain|duree)
    seen_now = set()
    seen = 0
    for activity in cfg["activities"]:
        for day in days:
            try:
                data = fetch_day(cfg["slug"], activity, day)
            except Exception as e:  # noqa: BLE001
                print(f"  (jour {day} {activity} échoué : {e})", file=sys.stderr)
                continue
            for ts in data:
                iso = ts.get("startDateTime") or ""
                if not iso:
                    continue
                try:
                    sdt = dt.datetime.fromisoformat(iso).replace(tzinfo=PARIS)
                except ValueError:
                    continue
                for sv in ts.get("services", []):
                    cid = sv.get("id") or ""
                    dur = sv.get("duration") or 0
                    key = f"{sdt.date().isoformat()}|{sdt.strftime('%H:%M')}|{cid}|{dur}"
                    seen_now.add(key)
                    edt = sdt + dt.timedelta(minutes=dur)
                    rec = store.get(key) or {}
                    store[key] = {
                        "id": key,
                        "date": sdt.strftime("%Y-%m-%d"),
                        "jour": JOURS_FR[sdt.weekday()],
                        "heure": sdt.strftime("%H:%M"),
                        "fin": edt.strftime("%H:%M"),
                        "terrain": court_name(cfg, cid),
                        "court_id": cid,
                        "duree": dur,
                        "prix": round((sv.get("price") or 0) / 100, 2),
                        "vu_dispo": True,
                        "statut": "dispo",           # encore réservable -> libre
                        "premier_vu": rec.get("premier_vu") or now.strftime("%Y-%m-%d %H:%M"),
                        "dernier_vu": now.strftime("%Y-%m-%d %H:%M"),
                        "releve": now.strftime("%Y-%m-%d %H:%M"),
                    }
                    seen += 1

    # 2) Offres connues, déjà vues dispo, qui ne réapparaissent plus :
    #    - si le créneau n'est pas encore passé -> il a été RÉSERVÉ
    #    - si le créneau est passé sans avoir disparu -> resté LIBRE (jamais réservé)
    reserved = 0
    for key, rec in store.items():
        if not rec.get("vu_dispo"):
            continue
        try:
            start = dt.datetime.fromisoformat(f"{rec['date']}T{rec['heure']}:00").replace(tzinfo=PARIS)
        except (ValueError, KeyError):
            continue
        if key in seen_now:
            # toujours proposé : libre (statut déjà mis plus haut, mais on garde pour les jours hors fenêtre)
            if rec.get("statut") not in ("dispo",):
                rec["statut"] = "dispo"
            continue
        # plus proposé
        if start > now:
            # disparu avant l'heure -> réservé
            if rec.get("statut") != "reserve":
                rec["statut"] = "reserve"
                rec["dernier_vu"] = rec.get("dernier_vu") or now.strftime("%Y-%m-%d %H:%M")
            reserved += 1
        else:
            # le créneau est passé. S'il était encore vu dispo récemment -> resté libre.
            rec["statut"] = "libre_fin"

    save_store(store, cfg["store"])
    res = sum(1 for v in store.values() if v.get("statut") == "reserve")
    print(f"[{cfg['key']}] {now:%Y-%m-%d %H:%M} : {seen} offres dispo vues, "
          f"{len(store)} en base ({res} réservées détectées).")
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
            .replace("__CSVNAME__", cfg["key"] + "_creneaux.csv")
            .replace("__ACCENT__", cfg["accent"])
            .replace("__ACCENT2__", cfg["accent2"])
            .replace("__HOST__", cfg["host"]))
    with open(cfg["html"], "w", encoding="utf-8") as f:
        f.write(html)
    print(f"-> {cfg['html']}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__BRAND__ - Occupation des terrains</title>
<script>__CHARTJS__</script>
<style>
  :root{--bg:#0b140d;--card:#122115;--card2:#1b2e20;--line:#27412e;
        --text:#eef9f0;--muted:#9bc4a6;--accent:__ACCENT__;--accent2:__ACCENT2__;
        --green:#5fcf8a;--yellow:#e6c14d;--red:#e07a6f;}
  *{box-sizing:border-box;}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);}
  header{padding:28px 32px 12px;} h1{margin:0;font-size:24px;font-weight:800;letter-spacing:.5px;}
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
  .pill{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;}
  .pill.r{background:rgba(224,122,111,.18);color:#f0a79e;}
  .pill.l{background:rgba(95,207,138,.18);color:#86e0a8;}
  .ranklist{display:flex;flex-direction:column;gap:9px;}
  .rk{display:grid;grid-template-columns:150px 1fr auto;align-items:center;gap:10px;font-size:13px;}
  .rk .lbl{font-weight:600;overflow:hidden;text-overflow:ellipsis;} .rk .track{height:9px;background:var(--line);border-radius:5px;overflow:hidden;}
  .rk .track>span{display:block;height:100%;background:var(--accent);border-radius:5px;} .rk .val{color:var(--muted);}
  .btn{background:var(--accent);color:#06200f;border:none;border-radius:9px;padding:9px 16px;font-size:13px;font-weight:700;cursor:pointer;}
  .tablewrap{max-height:600px;overflow:auto;border:1px solid var(--line);border-radius:14px;}
  .foot{color:var(--muted);font-size:12px;margin-top:18px;}
  @media(max-width:600px){header{padding:18px 14px 6px;}h1{font-size:18px;}.wrap{padding:0 12px 32px;}.kpis{grid-template-columns:1fr 1fr;gap:10px;}.kpi .v{font-size:21px;}.filters input,.filters select,.btn{font-size:15px;width:100%;}.rk{grid-template-columns:110px 1fr auto;}}
  @media(max-width:600px){canvas{max-height:200px!important}th,td{padding:7px 6px;font-size:12px}.panel{padding:15px 14px}.kpi .v{font-size:20px}.note{font-size:12px}.ctrl{font-size:12px;gap:7px}.pinp{width:64px}}
</style>
</head>
<body>
<header>
  <h1>__BRAND__ &middot; Occupation des terrains</h1>
  <div class="sub">généré le __GENERATED__ &middot; réservations déduites (Anybuddy)</div>
</header>
<div class="wrap">
  <div class="note">ℹ️ Anybuddy n'expose que les créneaux <b>disponibles</b>. Méthode : un robot relève très régulièrement (~5&ndash;15&nbsp;min) les terrains libres. Quand un créneau vu libre <b>disparaît avant son heure</b>, c'est qu'il a été <b>réservé</b>. L'<b>occupation</b> = créneaux réservés / (réservés + restés libres), par jour, heure et terrain. Plus l'historique s'accumule, plus la mesure est fiable. Pas de mise à jour live (le site bloque les appels navigateur externes).</div>
  <div id="periode" style="background:var(--card2);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:11px 16px;margin:14px 0 4px;color:var(--text);font-size:13.5px;font-weight:600"></div>
  <div class="kpis" id="kpis"></div>
  <div class="filters">
    <input id="q" placeholder="Rechercher (terrain, jour...)">
    <select id="fTerrain"></select>
    <select id="fJour"></select>
    <select id="fStatut"></select>
  </div>
  <div class="grid">
    <div class="panel"><h2>Taux d'occupation par jour</h2><canvas id="cDay"></canvas></div>
    <div class="panel"><h2>Réservés vs libres par terrain</h2><canvas id="cCourt"></canvas></div>
  </div>
  <div class="grid">
    <div class="panel"><h2>Taux d'occupation par créneau horaire</h2><canvas id="cHour"></canvas></div>
    <div class="panel"><h2>Créneaux les plus demandés (taux d'occupation)</h2><div id="topHour" class="ranklist"></div></div>
  </div>
  <div class="panel"><h2>Terrains &mdash; comparatif d'occupation</h2><div id="cmpCourts" class="ranklist"></div></div>

  <div class="panel">
    <h2>📅 Heatmap occupation jour &times; heure <span style="font-size:11px;color:var(--muted);font-weight:400;text-transform:none;letter-spacing:0">(taux d'occupation, pondéré par nb créneaux observés)</span></h2>
    <p style="color:var(--muted);font-size:12.5px;margin:-4px 0 12px"><b>Taux d'occupation</b> par bucket jour × heure (créneaux réservés / créneaux tranchés). Évite le biais "Mardi 10× vs Samedi 5×" : on compare des <b>taux</b>, pas des cumuls. Plus foncé = bucket plus systématiquement plein.</p>
    <div id="heatmap" style="display:grid;grid-template-columns:48px repeat(17,1fr);gap:2px;font-size:10px"></div>
  </div>

  <div class="panel">
    <h2>⚖️ Comparateur de créneaux</h2>
    <p style="color:var(--muted);font-size:12.5px;margin:-4px 0 14px">Compare 2 créneaux jour × tranche horaire : volume observé, taux d'occupation, top terrains.</p>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px" id="creneauCompare"></div>
  </div>

  <div class="panel">
    <h2>🏆 Top 20 créneaux jour × tranche horaire</h2>
    <p style="color:var(--muted);font-size:12.5px;margin:-4px 0 14px">7 jours × 5 tranches (matin 7-12h · midi 12-14h · aprem 14-18h · soirée 18-22h · fin 22h+) = 35 buckets, classés par <b>taux d'occupation</b> (min. 3 créneaux observés).</p>
    <div id="topBuckets"></div>
  </div>

  <div class="panel">
    <h2>Chiffre d'affaires estimé (réservations détectées)</h2>
    <div style="margin:0 0 12px;color:var(--muted);font-size:13px">Prix moyen par créneau
      <input id="prix" type="number" min="0" step="0.5" value="__PRICE__" style="width:90px;margin-left:8px;background:var(--card2);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:7px 10px;font-size:14px"> &euro; &middot; <span style="font-size:12px">CA = somme des <b>prix réels</b> Anybuddy de chaque créneau réservé (ce champ = repli si un prix manque)</span></div>
    <div class="kpis" id="caKpis" style="margin:6px 0 18px"></div>
    <canvas id="cCA"></canvas>
  </div>
  <div class="panel">
    <h2>Détail des créneaux</h2>
    <div class="filters"><button id="btnExport" class="btn">Exporter en Excel</button></div>
    <div class="tablewrap"><table id="tbl"><thead></thead><tbody></tbody></table></div>
  </div>
  <div class="foot">Source : __HOST__ &middot; occupation reconstruite par disparition des créneaux. Mise à jour côté serveur (~5&ndash;15 min).</div>
</div>
<script>
const ALL=__DATA__;
const JOURS=["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"];
const nf=v=>Math.round(v).toLocaleString('fr-FR');
const eur=v=>v.toLocaleString('fr-FR',{maximumFractionDigits:0})+' €';
const occColor=t=>t>=0.75?'#5fcf8a':t>=0.5?'#e6c14d':'#e07a6f';
function fmtJ(iso){const p=iso.split('-');return `${p[2]}/${p[1]}/${p[0]}`;}
Chart.defaults.color='#9bc4a6';Chart.defaults.borderColor='#27412e';Chart.defaults.font.family=getComputedStyle(document.body).fontFamily;
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const ACC=css('--accent')||'#2CDF62',ACC2=css('--accent2')||'#7af0a0';
let charts={};
const isNarrow=()=>matchMedia('(max-width:600px)').matches;

// On ne garde QUE les créneaux dont le statut est tranché (réservé ou resté libre).
// "dispo" = encore réservable dans le futur : indécis, exclu du taux d'occupation.
const tranche=r=>r.statut==='reserve'||r.statut==='libre_fin';
const isResa=r=>r.statut==='reserve';
let BASE=ALL.filter(tranche);

const selT=document.getElementById('fTerrain'),selJ=document.getElementById('fJour'),selS=document.getElementById('fStatut');
function fillSel(sel,vals,label){sel.innerHTML='';sel.add(new Option(label,''));[...new Set(vals)].filter(Boolean).sort().forEach(v=>sel.add(new Option(v,v)));}
fillSel(selT,ALL.map(r=>r.terrain),'Tous les terrains');
fillSel(selJ,ALL.map(r=>r.jour),'Tous les jours');
selS.innerHTML='';selS.add(new Option('Tous statuts',''));selS.add(new Option('Réservés','reserve'));selS.add(new Option('Restés libres','libre_fin'));

function current(){
  const q=document.getElementById('q').value.toLowerCase();
  return BASE.filter(r=>(!selT.value||r.terrain===selT.value)
    &&(!selJ.value||r.jour===selJ.value)
    &&(!selS.value||r.statut===selS.value)
    &&(!q||((r.terrain+' '+r.jour+' '+r.heure+' '+r.date).toLowerCase().includes(q))));
}
function mkChart(id,cfg){if(charts[id])charts[id].destroy();charts[id]=new Chart(document.getElementById(id),cfg);}

function render(){
  const D=current();
  const tot=D.length, resa=D.filter(isResa).length;
  const occ=tot?resa/tot:0;
  const nbCourts=new Set(D.map(r=>r.terrain)).size||1;
  const dts=[...new Set(D.map(r=>r.date))].sort(),pj=dts.length;
  document.getElementById('periode').textContent = pj
    ? `📅 Période étudiée : du ${fmtJ(dts[0])} au ${fmtJ(dts[pj-1])} · ${pj} jour${pj>1?'s':''} · ${nbCourts} terrain${nbCourts>1?'s':''} · ${nf(tot)} créneaux analysés`
    : 'Pas encore de créneaux tranchés : laissez le robot accumuler quelques heures.';
  document.getElementById('kpis').innerHTML=[
    ['Taux d\'occupation',tot?Math.round(100*occ)+'%':'—'],
    ['Créneaux réservés',nf(resa)],
    ['Créneaux analysés',nf(tot)],
    ['Terrains suivis',nf(nbCourts)],
    ['Réservés / jour',pj?(resa/pj).toFixed(1):'—'],
    ['Heures réservées',nf(D.filter(isResa).reduce((s,r)=>s+(r.duree||0)/60,0))],
  ].map(k=>`<div class="kpi"><div class="v">${k[1]}</div><div class="l">${k[0]}</div></div>`).join('');

  // occupation par jour
  const byDay={};D.forEach(r=>{byDay[r.date]=byDay[r.date]||{r:0,t:0};byDay[r.date].t++;if(isResa(r))byDay[r.date].r++;});
  const days=Object.keys(byDay).sort();
  mkChart('cDay',{type:'bar',data:{labels:days.map(d=>d.slice(8)+'/'+d.slice(5,7)),
    datasets:[{label:'occupation %',data:days.map(d=>Math.round(100*byDay[d].r/byDay[d].t)),backgroundColor:days.map(d=>occColor(byDay[d].r/byDay[d].t))}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.parsed.y+'% ('+byDay[days[c.dataIndex]].r+'/'+byDay[days[c.dataIndex]].t+')'}}},scales:{y:{beginAtZero:true,max:100,ticks:{callback:v=>v+'%'}}}}});

  // réservés vs libres par terrain
  const byCt={};D.forEach(r=>{byCt[r.terrain]=byCt[r.terrain]||{r:0,l:0};if(isResa(r))byCt[r.terrain].r++;else byCt[r.terrain].l++;});
  const courts=Object.keys(byCt).sort();
  mkChart('cCourt',{type:'bar',data:{labels:courts,datasets:[
    {label:'réservés',data:courts.map(c=>byCt[c].r),backgroundColor:'#e07a6f'},
    {label:'restés libres',data:courts.map(c=>byCt[c].l),backgroundColor:ACC}]},
    options:{plugins:{legend:{position:'bottom'}},scales:{x:{stacked:true},y:{stacked:true,beginAtZero:true,ticks:{callback:v=>nf(v)}}}}});

  // occupation par créneau horaire
  const byH={};D.forEach(r=>{byH[r.heure]=byH[r.heure]||{r:0,t:0};byH[r.heure].t++;if(isResa(r))byH[r.heure].r++;});
  const hrsSorted=Object.keys(byH).sort();
  mkChart('cHour',{type:'bar',data:{labels:hrsSorted,datasets:[{label:'occupation %',data:hrsSorted.map(h=>Math.round(100*byH[h].r/byH[h].t)),backgroundColor:hrsSorted.map(h=>occColor(byH[h].r/byH[h].t))}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.parsed.y+'% ('+byH[hrsSorted[c.dataIndex]].r+'/'+byH[hrsSorted[c.dataIndex]].t+')'}}},scales:{y:{beginAtZero:true,max:100,ticks:{callback:v=>v+'%'}}}}});

  // top créneaux par taux
  const hrs=Object.entries(byH).map(([h,o])=>[h,o.t?o.r/o.t:0,o.r,o.t]).sort((a,b)=>b[1]-a[1]).slice(0,8);
  document.getElementById('topHour').innerHTML=hrs.length?hrs.map(([h,t,r,n])=>
    `<div class="rk"><span class="lbl">${h}</span><span class="track"><span style="width:${Math.round(100*t)}%;background:${occColor(t)}"></span></span><span class="val">${Math.round(100*t)}% &middot; ${r}/${n}</span></div>`).join('')
    :'<div style="color:var(--muted)">Pas encore de données.</div>';

  // comparatif terrains
  const cmp=Object.entries(byCt).map(([k,o])=>{const tt=o.r+o.l;return [k,tt?o.r/tt:0,o.r,tt];}).sort((a,b)=>b[1]-a[1]);
  document.getElementById('cmpCourts').innerHTML=cmp.length?cmp.map(([k,t,r,n])=>
    `<div class="rk"><span class="lbl">${k}</span><span class="track"><span style="width:${Math.round(100*t)}%;background:${occColor(t)}"></span></span><span class="val">${Math.round(100*t)}% &middot; ${r}/${n} créneaux</span></div>`).join('')
    :'<div style="color:var(--muted)">Pas encore de données.</div>';

  // CA estimé
  const prix=parseFloat(document.getElementById('prix').value)||0;
  try{localStorage.setItem('__PRIXKEY__',prix);}catch(e){}
  // CA = somme des PRIX RÉELS Anybuddy des créneaux réservés (fallback = prix saisi)
  const resaRows=D.filter(isResa);
  const nbJours=days.length||1,totalCA=resaRows.reduce((s,r)=>s+(r.prix||prix),0);
  document.getElementById('caKpis').innerHTML=[
    ['CA total estimé',eur(totalCA)],
    ['CA / jour (moy.)',eur(totalCA/nbJours)],
    ['CA / terrain (moy.)',eur(totalCA/nbCourts)],
    ['CA / créneau réservé',eur(resa?totalCA/resa:0)],
  ].map(k=>`<div class="kpi"><div class="v">${k[1]}</div><div class="l">${k[0]}</div></div>`).join('');
  const caDay={};resaRows.forEach(r=>caDay[r.date]=(caDay[r.date]||0)+(r.prix||prix));
  const cdays=Object.keys(caDay).sort();
  mkChart('cCA',{type:'bar',data:{labels:cdays.map(d=>d.slice(8)+'/'+d.slice(5,7)),
    datasets:[{data:cdays.map(d=>caDay[d]),backgroundColor:'#5fcf8a'}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>eur(c.parsed.y)}}},
      scales:{y:{beginAtZero:true,ticks:{callback:v=>v.toLocaleString('fr-FR')+' €'}}}}});

  renderHeatmap(D);
  renderCreneauCompare(D);
  renderTopBuckets(D);
  renderTable(D);
}

// ============== HEATMAP / CRÉNEAUX (taux occupation pondéré) ==============
const TRANCHES={matin:{label:'Matin (7-12h)',hours:[7,8,9,10,11]},
  midi:{label:'Midi (12-14h)',hours:[12,13]},
  aprem:{label:'Après-midi (14-18h)',hours:[14,15,16,17]},
  soiree:{label:'Soirée (18-22h)',hours:[18,19,20,21]},
  fin:{label:'Fin soirée (22h+)',hours:[22,23]}};
let CRENEAU_A={jour:'Mardi',tranche:'aprem'};
let CRENEAU_B={jour:'Samedi',tranche:'matin'};
function renderHeatmap(D){
  const hm=document.getElementById('heatmap');if(!hm)return;
  const hours=Array.from({length:17},(_,i)=>i+7);
  const heat={};let max=0;
  D.forEach(r=>{const h=parseInt((r.heure||'').slice(0,2));if(isNaN(h))return;
    const k=r.jour+'|'+h;heat[k]=heat[k]||{r:0,n:0};
    heat[k].n++;if(isResa(r))heat[k].r++;});
  Object.values(heat).forEach(x=>{x.rate=x.n?x.r/x.n:0;if(x.rate>max)max=x.rate;});
  let html='<div></div>';
  hours.forEach(h=>html+=`<div style="text-align:center;color:var(--muted);font-weight:600;padding:3px 0">${h}h</div>`);
  JOURS.forEach(j=>{
    html+=`<div style="color:var(--muted);text-align:right;padding:0 6px;font-weight:600">${j.slice(0,3)}</div>`;
    hours.forEach(h=>{const cell=heat[j+'|'+h]||{rate:0,n:0,r:0};const v=cell.rate;const t=max?v/max:0;
      const r=Math.round(44+(255-44)*t),g=Math.round(223-(223-100)*t),b=Math.round(98-(98-50)*t);
      const op=cell.n?0.2+0.75*t:0.05;
      const label=cell.n?Math.round(100*v)+'%':'';
      html+=`<div style="aspect-ratio:1;background:rgba(${r},${g},${b},${op});border-radius:3px;display:flex;align-items:center;justify-content:center;font-weight:700;color:#fff;font-size:9.5px;cursor:default" title="${j} ${h}h : ${Math.round(100*v)}% occupation (${cell.r}/${cell.n} créneaux tranchés)">${label}</div>`;});
  });
  hm.innerHTML=html;
}
function computeCreneau(D,jour,tranche){
  const hours=new Set(TRANCHES[tranche].hours);
  const f=D.filter(r=>{const h=parseInt((r.heure||'').slice(0,2));return r.jour===jour&&hours.has(h);});
  const r=f.filter(isResa).length;
  const rate=f.length?r/f.length:0;
  const byT={};f.forEach(x=>{if(!x.terrain)return;byT[x.terrain]=byT[x.terrain]||{r:0,n:0};byT[x.terrain].n++;if(isResa(x))byT[x.terrain].r++;});
  const topT=Object.entries(byT).map(([k,o])=>[k,o.n?o.r/o.n:0,o.r,o.n]).filter(x=>x[3]>=2).sort((a,b)=>b[1]-a[1]).slice(0,5);
  return {n:f.length,r,rate,topT};
}
function renderCreneauCompare(D){
  const wrap=document.getElementById('creneauCompare');if(!wrap)return;
  const box=(side,sel)=>{const c=computeCreneau(D,sel.jour,sel.tranche);
    const color=side==='A'?ACC:ACC2;
    return `<div style="background:var(--card2);padding:14px 16px;border-radius:10px;border-left:3px solid ${color}">
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <select data-side="${side}" data-field="jour" style="flex:1;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:13px">${JOURS.map(j=>`<option value="${j}" ${j===sel.jour?'selected':''}>${j}</option>`).join('')}</select>
        <select data-side="${side}" data-field="tranche" style="flex:1;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:13px">${Object.entries(TRANCHES).map(([k,v])=>`<option value="${k}" ${k===sel.tranche?'selected':''}>${v.label}</option>`).join('')}</select>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">
        <div><div style="font-size:22px;font-weight:800;color:${ACC2}">${nf(c.n)}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Créneaux tranchés</div></div>
        <div><div style="font-size:22px;font-weight:800;color:${occColor(c.rate)}">${Math.round(100*c.rate)}%</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Taux occupation</div></div>
        <div><div style="font-size:22px;font-weight:800;color:${ACC2}">${nf(c.r)}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Réservés</div></div>
        <div><div style="font-size:22px;font-weight:800;color:${ACC2}">${nf(c.n-c.r)}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Restés libres</div></div>
      </div>
      <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px">Top terrains (taux occupation)</div>
      <div style="font-size:11.5px">${c.topT.length?c.topT.map(([k,t,rr,n],i)=>`<div style="padding:3px 7px;background:var(--bg);border-radius:5px;margin-bottom:3px"><span style="color:var(--muted)">${i+1}.</span> ${k.slice(0,28)} <span style="color:${occColor(t)};font-weight:700;float:right">${Math.round(100*t)}% (${rr}/${n})</span></div>`).join(''):'<div style="color:var(--muted);font-style:italic">—</div>'}</div>
    </div>`;};
  const cA=computeCreneau(D,CRENEAU_A.jour,CRENEAU_A.tranche),cB=computeCreneau(D,CRENEAU_B.jour,CRENEAU_B.tranche);
  const dR=Math.round(100*(cA.rate-cB.rate));
  wrap.innerHTML=`${box('A',CRENEAU_A)}${box('B',CRENEAU_B)}
    <div style="grid-column:span 2;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px 16px;text-align:center;font-size:13px;color:var(--muted)">
      <b style="color:${ACC}">${CRENEAU_A.jour} ${TRANCHES[CRENEAU_A.tranche].label}</b> vs <b style="color:${ACC2}">${CRENEAU_B.jour} ${TRANCHES[CRENEAU_B.tranche].label}</b> ·
      Écart occupation : <span style="color:${dR>0?'#5fcf8a':'#e07a6f'};font-weight:700">${dR>0?'+':''}${dR} pts</span>
    </div>`;
  wrap.querySelectorAll('select[data-side]').forEach(s=>s.addEventListener('change',e=>{
    const sel=e.target.dataset.side==='A'?CRENEAU_A:CRENEAU_B;sel[e.target.dataset.field]=e.target.value;
    renderCreneauCompare(D);
  }));
}
function renderTopBuckets(D){
  const buckets=[];
  for(const jour of JOURS){for(const[tk,tv]of Object.entries(TRANCHES)){
    const hours=new Set(tv.hours);
    const f=D.filter(r=>{const h=parseInt((r.heure||'').slice(0,2));return r.jour===jour&&hours.has(h);});
    if(f.length<3)continue;
    const r=f.filter(isResa).length;
    buckets.push({label:jour+' '+tv.label,n:f.length,r,t:f.length?r/f.length:0});
  }}
  buckets.sort((a,b)=>b.t-a.t);
  const w=document.getElementById('topBuckets');if(!w)return;
  w.innerHTML=buckets.slice(0,20).map((b,i)=>`<div style="display:grid;grid-template-columns:32px 280px 1fr auto;gap:10px;align-items:center;font-size:13px;padding:5px 0;border-bottom:1px solid var(--line)">
    <span style="color:var(--muted);font-weight:700;text-align:right">${i+1}.</span>
    <span style="font-weight:600">${b.label}</span>
    <span style="height:8px;background:var(--line);border-radius:4px;overflow:hidden"><span style="display:block;height:100%;background:${occColor(b.t)};width:${Math.round(100*b.t)}%"></span></span>
    <span style="color:var(--muted);font-variant-numeric:tabular-nums;min-width:160px;text-align:right">${Math.round(100*b.t)}% occupation · ${b.r}/${b.n} créneaux</span>
  </div>`).join('')||'<div style="color:var(--muted)">Pas encore assez de créneaux tranchés par bucket (min. 3).</div>';
}

const cols=[['date','Date'],['jour','Jour'],['heure','Heure'],['fin','Fin'],['terrain','Terrain'],['duree','Durée'],['prix','Prix'],['statut','Statut']];
document.querySelector('#tbl thead').innerHTML='<tr>'+cols.map(c=>`<th>${c[1]}</th>`).join('')+'</tr>';
let currentRows=[];
function renderTable(D){
  let rows=[...D].sort((a,b)=>(a.date+a.heure)<(b.date+b.heure)?1:-1);
  currentRows=rows;
  document.querySelector('#tbl tbody').innerHTML=rows.map(r=>{
    const pill=isResa(r)?'<span class="pill r">réservé</span>':'<span class="pill l">resté libre</span>';
    return `<tr><td>${fmtJ(r.date)}</td><td>${r.jour}</td><td>${r.heure}</td><td>${r.fin}</td><td>${r.terrain}</td><td>${r.duree} min</td><td>${r.prix} €</td><td>${pill}</td></tr>`;}).join('');
}
['q','prix'].forEach(id=>document.getElementById(id).addEventListener('input',render));
[selT,selJ,selS].forEach(s=>s.addEventListener('change',render));
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


def run(cfg):
    store = capture(cfg)
    rows = sorted(store.values(), key=lambda r: (r["date"], r["heure"], r.get("terrain", "")))
    write_csv(rows, cfg["csv"])
    write_html(rows, cfg)
    res = [r for r in rows if r.get("statut") == "reserve"]
    print(f"OK [{cfg['key']}]: {len(rows)} créneaux en base, {len(res)} réservés détectés.")


if __name__ == "__main__":
    run(CONFIG)
