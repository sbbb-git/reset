#!/usr/bin/env python3
"""Moteur générique de scrap fréquentation pour les studios sur bsport.

Réutilisé par thenewme / lecercle / spacecycle / poses (et compatible Re-SET).
API publique : https://api.production.bsport.io/book/v1/offer/?company=<ID>
-> effectif (capacité), validated_booking_count (présents validés), establishment
(studio), activity_name (cours), coach. CORS ouvert -> appel navigateur OK.

On accumule chaque relevé dans <key>_data.json (clé = id de la séance). Un
cours terminé a un compte définitif. Conçu pour tourner souvent (toutes les
~5 min) afin de figer le remplissage juste avant chaque séance.

Chaque studio fournit sa config et appelle run(cfg).
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
API = "https://api.production.bsport.io"
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
FIELDS = ["date", "jour", "heure", "fin", "lieu", "cours", "coach",
          "capacite", "presents", "finie", "releve"]


def _get(path, params, retries=4):
    url = f"{API}{path}?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120",
                "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"echec requete {url}: {last}")


def fetch_establishments(company):
    """Renvoie (noms {id:titre}, set d'ids 'XMBO' à ignorer car doublons)."""
    names, xmbo = {}, set()
    try:
        d = _get("/book/v1/establishment/", {"company": company, "page_size": 200})
        for e in d.get("results", []):
            nm = (e.get("title") or e.get("name") or "").strip()
            if nm.upper().startswith("XMBO"):   # système parallèle -> doublons
                xmbo.add(e["id"])
                continue
            if nm:
                names[e["id"]] = nm   # nom brut, pas de nettoyage
    except Exception as e:  # noqa: BLE001
        print(f"  (établissements indispo company {company} : {e})", file=sys.stderr)
    return names, xmbo


def fetch_coaches(company):
    out = {}
    try:
        d = _get("/book/v1/associated_coach/", {"company": company, "page_size": 500})
        for c in d.get("results", []):
            name = c.get("name") or f"{c.get('firstname','')} {c.get('lastname','')}".strip()
            for key in ("associated_coach_id", "id"):
                if c.get(key):
                    out[c[key]] = name
            for aid in c.get("associatedcoach_set", []) or []:
                out.setdefault(aid, name)
    except Exception as e:  # noqa: BLE001
        print(f"  (coachs indispo : {e})", file=sys.stderr)
    return out


def fetch_offers(company, start, end):
    offers, page = [], 1
    while True:
        d = _get("/book/v1/offer/", {
            "company": company, "only_future_strict": "false",
            "min_date": start.isoformat(), "max_date": end.isoformat(),
            "page_size": 300, "page": page})
        res = d.get("results", [])
        offers.extend(res)
        if not d.get("next_page") or not res:
            break
        page += 1
    return offers


def load_store(path):
    return safestore.load(path)


def save_store(store, path):
    safestore.save(store, path)


def capture(cfg):
    now = dt.datetime.now(PARIS)
    store = load_store(cfg["store"])
    est, coaches, skip = cfg["_est"], cfg["_coaches"], cfg["_skip"]
    offers = []
    for co in cfg["_companies"]:
        try:
            offers += fetch_offers(co, now.date() - dt.timedelta(days=3),
                                   now.date() + dt.timedelta(days=2))
        except Exception as e:  # noqa: BLE001
            print(f"  (fetch offers company {co} échoué : {e})", file=sys.stderr)
    seen = 0
    for o in offers:
        if o.get("establishment") in skip:
            continue
        ds = o.get("date_start") or ""
        if not ds:
            continue
        try:
            sdt = dt.datetime.fromisoformat(ds)
        except ValueError:
            continue
        dur = o.get("duration_minute") or 0
        edt = sdt + dt.timedelta(minutes=dur)
        sid = str(o.get("id"))
        cid = o.get("coach")
        store[sid] = {
            "id": sid,
            "date": ds[:10],
            "jour": JOURS_FR[sdt.weekday()],
            "heure": ds[11:16],
            "fin": edt.strftime("%H:%M"),
            "lieu": est.get(o.get("establishment"), f"Studio {o.get('establishment')}"),
            "cours": (o.get("activity_name") or o.get("name_override") or "").strip(),
            "coach": coaches.get(cid, "") if cid else "",
            "capacite": o.get("effectif") or 0,
            "presents": o.get("validated_booking_count") or 0,
            "finie": now >= edt.replace(tzinfo=sdt.tzinfo),
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
    import dashboard_meta
    from template_common import meta_panel_html
    chartjs = ""
    vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor_chartjs.min.js")
    if os.path.exists(vendor):
        with open(vendor, encoding="utf-8") as f:
            chartjs = f.read()
    est_js = {str(k): v for k, v in cfg["_est"].items()}
    coa_js = {str(k): v for k, v in cfg["_coaches"].items()}
    # META panel (état du scrap pour ce dashboard)
    last_iso = ""
    _releves = [r.get("releve") or "" for r in (rows if isinstance(rows, list) else rows.values()) if isinstance(r, dict)]
    _last = max(_releves) if _releves else ""
    if _last:
        try:
            last_iso = dt.datetime.strptime(_last[:16], "%Y-%m-%d %H:%M").replace(tzinfo=PARIS).isoformat()
        except ValueError:
            pass
    _m = dashboard_meta.get(cfg.get("key") or "bsport_generic")
    _meta_html = meta_panel_html(_m["method"], _m["risk"], _m["freq"], last_iso, len(rows))
    html = (HTML_TEMPLATE
            .replace("__CHARTJS__", chartjs)
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
            .replace("__EST__", json.dumps(est_js, ensure_ascii=False))
            .replace("__COACHES__", json.dumps(coa_js, ensure_ascii=False))
            .replace("__GENERATED__", dt.datetime.now(PARIS).strftime("%d/%m/%Y %H:%M"))
            .replace("__META_PANEL__", _meta_html)
            .replace("__BRAND__", cfg["brand"])
            .replace("__METHODE__", cfg.get("methode", ""))
            .replace("__PRICE__", str(cfg["price"]))
            .replace("__PRIXKEY__", cfg["key"] + "_prix")
            .replace("__CSVNAME__", cfg["key"] + "_seances.csv")
            .replace("__ACCENT__", cfg["accent"])
            .replace("__ACCENT2__", cfg["accent2"])
            .replace("__COMPANIES__", json.dumps(cfg["_companies"]))
            .replace("__HOST__", cfg["host"]))
    with open(cfg["html"], "w", encoding="utf-8") as f:
        f.write(html)
    print(f"-> {cfg['html']}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<script>if(sessionStorage.getItem("auth_ok")!=="1")location.replace("login.html?next="+encodeURIComponent(location.pathname+location.search+location.hash));</script>
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
  .rk .track>span{display:block;height:100%;background:var(--accent);border-radius:5px;} .rk .val{color:var(--muted);}
  .btn{background:var(--accent);color:#fff;border:none;border-radius:9px;padding:9px 16px;font-size:13px;font-weight:700;cursor:pointer;}
  .tablewrap{max-height:600px;overflow:auto;border:1px solid var(--line);border-radius:14px;}
  .foot{color:var(--muted);font-size:12px;margin-top:18px;}
  @media(max-width:600px){header{padding:18px 14px 6px;}h1{font-size:18px;}.wrap{padding:0 12px 32px;}.kpis{grid-template-columns:1fr 1fr;gap:10px;}.kpi .v{font-size:21px;}.filters input,.filters select,.btn{font-size:15px;width:100%;}.rk{grid-template-columns:110px 1fr auto;}}
  @media(max-width:600px){canvas{max-height:200px!important}th,td{padding:7px 6px;font-size:12px}.panel{padding:15px 14px}.kpi .v{font-size:20px}.note{font-size:12px}.ctrl{font-size:12px;gap:7px}.pinp{width:64px}}
</style>
</head>
<body>
<header>
  <h1>__BRAND__ &middot; Fréquentation</h1>
  <div class="sub">généré le __GENERATED__ &middot; réservations validées (bsport)</div>
</header>
<div class="wrap">
  __META_PANEL__
  <div class="note">ℹ️ __METHODE__</div>
  <div id="periode" style="background:var(--card2);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:11px 16px;margin:14px 0 4px;color:var(--text);font-size:13.5px;font-weight:600"></div>
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
    <h2>📅 Heatmap fréquentation jour &times; heure <span style="font-size:11px;color:var(--muted);font-weight:400;text-transform:none;letter-spacing:0">(moyenne présents/séance, pondérée)</span></h2>
    <p style="color:var(--muted);font-size:12.5px;margin:-4px 0 12px"><b>Moyenne</b> des présents par séance pour chaque bucket jour × heure. Pondéré par le nombre de séances observées dans la case (évite le biais : un mardi qu'on observe 10× n'est pas mécaniquement plus chargé qu'un samedi observé 5×). Plus foncé = créneau plus chargé en moyenne.</p>
    <div id="heatmap" style="display:grid;grid-template-columns:48px repeat(17,1fr);gap:2px;font-size:10px"></div>
  </div>

  <div class="panel">
    <h2>⚖️ Comparateur de créneaux</h2>
    <p style="color:var(--muted);font-size:12.5px;margin:-4px 0 14px">Compare 2 créneaux jour × tranche horaire (ex. mardi après-midi vs samedi matin) : volume, présents moyen, taux de remplissage, top cours, top coachs.</p>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px" id="creneauCompare"></div>
  </div>

  <div class="panel">
    <h2>🏆 Top 20 créneaux jour × tranche horaire</h2>
    <p style="color:var(--muted);font-size:12.5px;margin:-4px 0 14px">7 jours × 5 tranches (matin 7-12h · midi 12-14h · aprem 14-18h · soirée 18-22h · fin 22h+) = 35 buckets, classés par présents cumulés.</p>
    <div id="topBuckets"></div>
  </div>
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
  <div style="text-align:center;margin:24px 0 6px">
    <button id="btnMaj" class="btn">Mettre à jour (live)</button>
    <span id="majMsg" style="color:var(--muted);font-size:12px;margin-left:10px"></span>
  </div>
  <div class="foot">Source : __HOST__ (bsport) &middot; mise à jour auto ~5 min avant chaque séance.</div>
</div>
<script>
const ALL=__DATA__;
const API="https://api.production.bsport.io",COMPANIES=__COMPANIES__,EST=__EST__,COACHES=__COACHES__;
const JOURS=["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"];
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

// on compte tout ce qui a DÉMARRÉ ; on s'arrête à la 1re séance à venir (calcul live)
const started=r=>new Date(r.date+'T'+(r.heure||'00:00')+':00')<=new Date();
let FINIES=ALL.filter(r=>started(r)&&(r.capacite||0)>0);
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
    datasets:[{label:'présents',data:days.map(d=>byDay[d]),backgroundColor:ACC}]},
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
    backgroundColor:[ACC,ACC2,'#5fcf8a','#e6c14d','#e07a6f','#b07ff0','#f0a26f','#6fd0e0','#e06f9c','#9ce06f','#6f9cff']}]},
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

  renderHeatmap(D);
  renderCreneauCompare(D);
  renderTopBuckets(D);

  renderTable(D);
}

// ============== HEATMAP jour × heure ==============
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
  // PONDÉRÉ : moyenne présents/séance (évite biais "Mardi observé 10x vs Samedi 5x")
  const heat={};let max=0;
  D.forEach(r=>{const h=parseInt((r.heure||'').slice(0,2));if(isNaN(h))return;
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
      html+=`<div style="aspect-ratio:1;background:rgba(${r},${g},${b},${op});border-radius:3px;display:flex;align-items:center;justify-content:center;font-weight:700;color:#fff;font-size:9.5px;cursor:default" title="${j} ${h}h : ${v.toFixed(1)} présents/séance (sur ${cell.n} séances observées, ${nf(cell.p)} cumulés)">${label}</div>`;});
  });
  hm.innerHTML=html;
}
function computeCreneau(D,jour,tranche){
  const hours=new Set(TRANCHES[tranche].hours);
  const f=D.filter(r=>{const h=parseInt((r.heure||'').slice(0,2));return r.jour===jour&&hours.has(h);});
  const presents=f.reduce((s,r)=>s+(r.presents||0),0);
  const capacite=f.reduce((s,r)=>s+(r.capacite||0),0);
  const remplissage=capacite?presents/capacite:0;
  const byCours={};f.forEach(r=>{if(!r.cours)return;byCours[r.cours]=(byCours[r.cours]||0)+(r.presents||0);});
  const topCours=Object.entries(byCours).sort((a,b)=>b[1]-a[1]).slice(0,5);
  const byCoach={};f.forEach(r=>{if(!r.coach)return;byCoach[r.coach]=byCoach[r.coach]||{p:0,n:0};byCoach[r.coach].p+=(r.presents||0);byCoach[r.coach].n++;});
  const topCoachs=Object.entries(byCoach).map(([k,o])=>[k,o.p/o.n,o.n]).sort((a,b)=>b[1]-a[1]).slice(0,5);
  return {n:f.length,presents,capacite,remplissage,moy:f.length?presents/f.length:0,topCours,topCoachs};
}
function renderCreneauCompare(D){
  const wrap=document.getElementById('creneauCompare');if(!wrap)return;
  const box=(side,sel)=>{const c=computeCreneau(D,sel.jour,sel.tranche);
    const color=side==='A'?'var(--accent)':'var(--gold)';
    return `<div style="background:var(--card2);padding:14px 16px;border-radius:10px;border-left:3px solid ${color}">
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <select data-side="${side}" data-field="jour" style="flex:1;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:13px">${JOURS.map(j=>`<option value="${j}" ${j===sel.jour?'selected':''}>${j}</option>`).join('')}</select>
        <select data-side="${side}" data-field="tranche" style="flex:1;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:13px">${Object.entries(TRANCHES).map(([k,v])=>`<option value="${k}" ${k===sel.tranche?'selected':''}>${v.label}</option>`).join('')}</select>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">
        <div><div style="font-size:22px;font-weight:800;color:var(--accent2)">${nf(c.n)}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Séances</div></div>
        <div><div style="font-size:22px;font-weight:800;color:var(--accent2)">${c.moy.toFixed(1)}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Présents/séance</div></div>
        <div><div style="font-size:22px;font-weight:800;color:${fillColor(c.remplissage)}">${Math.round(100*c.remplissage)}%</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Remplissage</div></div>
        <div><div style="font-size:22px;font-weight:800;color:var(--accent2)">${nf(c.presents)}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Présents cumulés</div></div>
      </div>
      <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px">Top cours · Top coachs</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11.5px">
        <div>${c.topCours.length?c.topCours.map(([k,v],i)=>`<div style="padding:3px 7px;background:var(--bg);border-radius:5px;margin-bottom:3px"><span style="color:var(--muted)">${i+1}.</span> ${k.slice(0,20)} <span style="color:var(--accent2);font-weight:700;float:right">${v}</span></div>`).join(''):'<div style="color:var(--muted);font-style:italic">—</div>'}</div>
        <div>${c.topCoachs.length?c.topCoachs.map(([k,m,n],i)=>`<div style="padding:3px 7px;background:var(--bg);border-radius:5px;margin-bottom:3px"><span style="color:var(--muted)">${i+1}.</span> ${k.slice(0,20)} <span style="color:var(--accent2);font-weight:700;float:right">${m.toFixed(1)}</span></div>`).join(''):'<div style="color:var(--muted);font-style:italic">—</div>'}</div>
      </div>
    </div>`;};
  const cA=computeCreneau(D,CRENEAU_A.jour,CRENEAU_A.tranche),cB=computeCreneau(D,CRENEAU_B.jour,CRENEAU_B.tranche);
  const dV=cB.n?Math.round(100*(cA.n-cB.n)/cB.n):0;
  const dM=cB.moy?Math.round(10*(cA.moy-cB.moy))/10:0;
  wrap.innerHTML=`${box('A',CRENEAU_A)}${box('B',CRENEAU_B)}
    <div style="grid-column:span 2;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px 16px;text-align:center;font-size:13px;color:var(--muted)">
      <b style="color:var(--accent)">${CRENEAU_A.jour} ${TRANCHES[CRENEAU_A.tranche].label}</b> vs <b style="color:var(--gold)">${CRENEAU_B.jour} ${TRANCHES[CRENEAU_B.tranche].label}</b> ·
      Volume : <span style="color:${dV>0?'var(--green)':'var(--red)'};font-weight:700">${dV>0?'+':''}${dV}%</span> ·
      Présents moy. : <span style="color:${dM>0?'var(--green)':'var(--red)'};font-weight:700">${dM>0?'+':''}${dM}</span>
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
    <span style="height:8px;background:var(--line);border-radius:4px;overflow:hidden"><span style="display:block;height:100%;background:${fillColor(b.r)};width:${Math.round(100*b.p/max)}%"></span></span>
    <span style="color:var(--muted);font-variant-numeric:tabular-nums;min-width:160px;text-align:right">${nf(b.p)} présents · ${Math.round(100*b.r)}% remplissage</span>
  </div>`).join('');
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

// mise à jour live via l'API bsport (CORS ouvert) -> fusion par séance
function pad2(n){return (n<10?'0':'')+n;}
function ymd(d){return d.getFullYear()+'-'+pad2(d.getMonth()+1)+'-'+pad2(d.getDate());}
async function majNow(){
  const btn=document.getElementById('btnMaj'),msg=document.getElementById('majMsg'),old=btn.textContent;
  btn.disabled=true;btn.textContent='Mise à jour…';msg.textContent='';
  try{
    const now=new Date();
    const min=new Date(now);min.setDate(min.getDate()-3);
    const max=new Date(now);max.setDate(max.getDate()+2);
    let offers=[];
    for(const co of COMPANIES){
      let page=1;
      while(page<=10){
        const u=`${API}/book/v1/offer/?company=${co}&only_future_strict=false&min_date=${ymd(min)}&max_date=${ymd(max)}&page_size=300&page=${page}`;
        const r=await fetch(u,{headers:{'Accept':'application/json'}});
        if(!r.ok)break;
        const d=await r.json();offers=offers.concat(d.results||[]);
        if(!d.next_page||!(d.results||[]).length)break;page++;
      }
    }
    const byKey={};ALL.forEach(x=>byKey[lkey(x)]=x);
    let add=0;
    offers.forEach(o=>{const ds=o.date_start;if(!ds)return;
      const sd=new Date(ds),ed=new Date(sd.getTime()+(o.duration_minute||0)*60000);
      const row={id:String(o.id),date:ds.slice(0,10),jour:JOURS[(sd.getDay()+6)%7],heure:ds.slice(11,16),
        fin:pad2(ed.getHours())+':'+pad2(ed.getMinutes()),
        lieu:EST[String(o.establishment)]||('Studio '+o.establishment),
        cours:(o.activity_name||o.name_override||'').trim(),coach:COACHES[String(o.coach)]||'',
        capacite:o.effectif||0,presents:o.validated_booking_count||0,finie:now>=ed};
      const k=lkey(row);if(!(k in byKey))add++;byKey[k]=row;});
    ALL.length=0;Object.values(byKey).forEach(x=>ALL.push(x));
    FINIES=ALL.filter(started);
    const a=selLieu.value,b=selCours.value,c=selCoach.value;
    fillSel(selLieu,ALL.map(r=>r.lieu),'Tous les studios');fillSel(selCours,ALL.map(r=>r.cours),'Tous les cours');fillSel(selCoach,ALL.map(r=>r.coach),'Tous les coachs');
    selLieu.value=a;selCours.value=b;selCoach.value=c;
    render();
    msg.textContent='À jour ✓ '+now.toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'})+(add?` (+${add} séances)`:'');
  }catch(err){msg.textContent='Échec : '+err.message;}
  btn.disabled=false;btn.textContent=old;
}
document.getElementById('btnMaj').addEventListener('click',majNow);
render();
</script>
</body>
</html>"""


def run(cfg):
    companies = cfg.get("companies") or [cfg["company"]]
    est, coaches, skip = {}, {}, set()
    for co in companies:
        names, xmbo = fetch_establishments(co)
        est.update(names)
        skip |= xmbo
        coaches.update(fetch_coaches(co))
    # skip par nom de studio (ex: "Studio Bastille" pour Le Cercle = établissement
    # backoffice configuré mais pas ouvert au public : 0 réservation, capacité aberrante)
    for skip_name in cfg.get("skip_lieux") or []:
        for eid, nm in est.items():
            if nm.strip().lower() == skip_name.strip().lower():
                skip.add(eid)
    cfg["_companies"], cfg["_est"], cfg["_coaches"], cfg["_skip"] = companies, est, coaches, skip
    store = capture(cfg)
    best = {}
    for r in store.values():
        k = (r["date"], r["heure"], r.get("lieu", ""), r.get("cours", ""), r.get("coach", ""))
        cur = best.get(k)
        if cur is None or (bool(r.get("finie")), r.get("releve", "")) > (bool(cur.get("finie")), cur.get("releve", "")):
            best[k] = r
    rows = sorted(best.values(), key=lambda r: (r["date"], r["heure"], r.get("lieu", "")))
    write_csv(rows, cfg["csv"])
    write_html(rows, cfg)
    fin = [r for r in rows if r.get("finie")]
    print(f"OK [{cfg['key']}]: {len(rows)} cours en base, {len(fin)} terminés.")
