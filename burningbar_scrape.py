#!/usr/bin/env python3
"""Burning Bar (Mindbody) — capture du STATUT, comme Sense-Club.

Burning Bar utilise le widget Mindbody standard (pas de proxy maison comme
Punch), qui n'expose QUE le statut (Réserver / Complet / Il reste X places),
jamais le nombre exact. On rend le widget des 2 adresses (Playwright via
burningbar_fetch.cjs) et on fige le statut ~10 min avant chaque séance.

Génère : burningbar_seances.csv et burningbar.html

MISE À JOUR 2026-08-09
----------------------
Le studio est passé de 2 salles (Hot Room / Reformer Room) à 2 adresses
(Paris 16 / Paris 7) : les widgets ont changé d'id, dont un qui n'existait
plus du tout côté site — voir l'en-tête de burningbar_fetch.cjs. Les lignes
déjà en base gardent leurs anciens libellés de salle, aucune n'est réécrite.

On ajoute aussi un garde-fou : un passage à 0 séance n'est plus muet. Le
2026-08-08, la journée entière est passée à 0 après 16 jours à 11-25 séances
et personne n'a rien vu, parce que le scraper imprimait « OK » et sortait 0.
Désormais on compare aux derniers même-jours-de-semaine et on annote en
::error:: si l'écart n'est pas explicable.
"""
import collections
import csv
import dashboard_meta
from template_common import meta_panel_html
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
    # Le .cjs raconte sur stderr ce qu'il a vu widget par widget (nb de
    # séances, ou échantillon du texte rendu quand il n'en trouve aucune).
    # Sans ce relais, « page vide » et « gabarit changé » sont indiscernables
    # dans les logs Actions.
    if res.stderr.strip():
        print(res.stderr.strip(), file=sys.stderr)
    if res.returncode != 0 or not res.stdout.strip():
        print("  (fetch Mindbody vide/erreur)", file=sys.stderr)
        return []
    try:
        return json.loads(res.stdout)
    except ValueError:
        return []


def attendu_ce_jour(store, jour, today_iso, n=3):
    """Nb de séances relevées les derniers même-jours-de-semaine passés.

    Sert à qualifier un 0 : un samedi vide alors que les 3 samedis précédents
    tenaient 15-21 séances est une panne, pas un planning creux.
    """
    par_date = collections.Counter()
    for v in store.values():
        d = v.get("date") or ""
        if v.get("jour") == jour and d < today_iso:
            par_date[d] += 1
    return [par_date[d] for d in sorted(par_date, reverse=True)[:n]]


def alerte_si_muet(store, sessions, jour, today_iso):
    """Annonce franchement un passage à 0 séance. Renvoie True si c'est louche."""
    if sessions:
        return False
    hist = attendu_ce_jour(store, jour, today_iso)
    ref = sorted(hist)[len(hist) // 2] if hist else 0     # médiane
    if ref >= 3:
        print(f"::error::[burningbar] 0 séance capturée ce {jour} alors que les "
              f"{len(hist)} derniers {jour.lower()}s en comptaient {hist} "
              f"(médiane {ref}). Causes à départager : widget déplacé sur "
              f"burningbar.fr/planning/, gabarit du bouton modifié, ou "
              f"fermeture du studio — le détail par widget est juste au-dessus.",
              file=sys.stderr)
        return True
    print(f"::warning::[burningbar] 0 séance capturée ce {jour} "
          f"(historique des {jour.lower()}s : {hist or 'aucun'}) — "
          f"planning probablement vide.", file=sys.stderr)
    return False


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
    muet = alerte_si_muet(store, sessions, jour, today.isoformat())
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
        # Capacité par défaut : 25 (cours collectifs Burning Bar / The Hot Room ~20-30 places)
        cap = 25
        if statut == "complet":
            presents = cap
        elif statut == "presque complet" and isinstance(places, int):
            presents = max(0, cap - places)
        else:
            presents = 0
        store[key] = {
            "date": today.isoformat(), "jour": jour, "heure": heure, "salle": salle,
            "lieu": salle,    # alias unifié avec les autres scrapers
            "cours": s.get("cours", ""), "statut": statut, "places_restantes": places,
            "capacite": cap, "presents": presents,
            "locked": lock, "finie": lock,    # alias finie = locked (terminologie unifiée)
            "releve": now.strftime("%Y-%m-%d %H:%M"),
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
    return store, muet


FIELDS = ["date", "jour", "heure", "salle", "lieu", "cours", "statut",
          "places_restantes", "capacite", "presents", "locked", "finie", "releve"]


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
    last_iso = ""
    _releves = [r.get("releve") or "" for r in (rows if isinstance(rows,list) else rows.values()) if isinstance(r, dict)]
    _last = max(_releves) if _releves else ""
    if _last:
        try:
            last_iso = dt.datetime.strptime(_last[:16], "%Y-%m-%d %H:%M").replace(tzinfo=PARIS).isoformat()
        except ValueError:
            pass
    _n_rows = len(rows) if isinstance(rows, (list, dict)) else 0
    _m = dashboard_meta.get("burningbar")
    _meta_html = meta_panel_html(_m["method"], _m["risk"], _m["freq"], last_iso, _n_rows)

    html = (HTML_TEMPLATE
            .replace("__CHARTJS__", chartjs)
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
            .replace("__GENERATED__", dt.datetime.now(PARIS).strftime("%d/%m/%Y %H:%M")).replace("__META_PANEL__", _meta_html))
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
  __META_PANEL__
  <div class="note">ℹ️ <b>Burning Bar &middot; plateforme Mindbody (widget standard).</b> Comme Sense-Club, Mindbody n'expose <b>que le statut</b> (Réserver / Il reste X places / Complet), <b>pas le nombre exact</b>. Statut lu sur les widgets des 2 salles (The Hot Room, The Reformer Room) et <b>figé ~10 min avant chaque séance</b>. L'historique s'accumule. MAJ toutes les 10 min.</div>
  <div id="emptywrap"></div>
  <div class="kpis" id="kpis"></div>
  <div class="panel">
    <h2>Séances complètes par jour</h2><canvas id="cDay" style="max-height:260px"></canvas>
  </div>

  <div class="panel">
    <h2>📅 Heatmap jour &times; heure <span style="font-size:11px;color:var(--muted);font-weight:400;text-transform:none;letter-spacing:0">(% complet, pondéré par nb séances observées)</span></h2>
    <p style="color:var(--muted);font-size:12.5px;margin:-4px 0 12px"><b>Taux de complétude</b> par bucket jour × heure (séances complètes / séances figées). Évite le biais "Mardi 10× vs Samedi 5×". Plus foncé = bucket plus systématiquement complet.</p>
    <div id="heatmap" style="display:grid;grid-template-columns:48px repeat(17,1fr);gap:2px;font-size:10px"></div>
  </div>

  <div class="panel">
    <h2>⚖️ Comparateur de créneaux</h2>
    <p style="color:var(--muted);font-size:12.5px;margin:-4px 0 14px">Compare 2 créneaux jour × tranche horaire : volume observé, taux de complétude, top cours.</p>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px" id="creneauCompare"></div>
  </div>

  <div class="panel">
    <h2>🏆 Top 20 créneaux jour × tranche horaire</h2>
    <p style="color:var(--muted);font-size:12.5px;margin:-4px 0 14px">7 jours × 5 tranches (matin 7-12h · midi 12-14h · aprem 14-18h · soirée 18-22h · fin 22h+) = 35 buckets, classés par <b>taux de complétude</b> (min. 3 séances observées).</p>
    <div id="topBuckets"></div>
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
const JOURS=["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"];
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
const totPres=DATA.reduce((s,r)=>s+(r.presents||0),0);
const avgPres=DATA.length?totPres/DATA.length:0;
document.getElementById('kpis').innerHTML=[
  ['Séances suivies',nf(DATA.length)],
  ['Complètes',nf(nComplet)+(DATA.length?` (${Math.round(100*nComplet/DATA.length)}%)`:'')],
  ['Presque complètes',nf(nPresque)+(DATA.length?` (${Math.round(100*nPresque/DATA.length)}%)`:'')],
  ['Présents estimés',nf(totPres)],
  ['Moy. présents / séance',DATA.length?avgPres.toFixed(1):'—'],
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

// ============== HEATMAP / CRÉNEAUX (% complet pondéré) ==============
const TRANCHES={matin:{label:'Matin (7-12h)',hours:[7,8,9,10,11]},
  midi:{label:'Midi (12-14h)',hours:[12,13]},
  aprem:{label:'Après-midi (14-18h)',hours:[14,15,16,17]},
  soiree:{label:'Soirée (18-22h)',hours:[18,19,20,21]},
  fin:{label:'Fin soirée (22h+)',hours:[22,23]}};
let CRENEAU_A={jour:'Mardi',tranche:'aprem'};
let CRENEAU_B={jour:'Samedi',tranche:'matin'};
const fillColor=t=>t>=0.75?'#5fcf8a':t>=0.5?'#e6c14d':'#e07a6f';
function renderHeatmap(){
  const hm=document.getElementById('heatmap');if(!hm)return;
  const hours=Array.from({length:17},(_,i)=>i+7);
  const heat={};let max=0;
  DATA.forEach(r=>{const h=parseInt((r.heure||'').slice(0,2));if(isNaN(h))return;
    const k=r.jour+'|'+h;heat[k]=heat[k]||{c:0,n:0};
    heat[k].n++;if(r.statut==='complet')heat[k].c++;});
  Object.values(heat).forEach(x=>{x.rate=x.n?x.c/x.n:0;if(x.rate>max)max=x.rate;});
  let html='<div></div>';
  hours.forEach(h=>html+=`<div style="text-align:center;color:var(--muted);font-weight:600;padding:3px 0">${h}h</div>`);
  JOURS.forEach(j=>{
    html+=`<div style="color:var(--muted);text-align:right;padding:0 6px;font-weight:600">${j.slice(0,3)}</div>`;
    hours.forEach(h=>{const cell=heat[j+'|'+h]||{rate:0,n:0,c:0};const v=cell.rate;const t=max?v/max:0;
      const r=Math.round(44+(255-44)*t),g=Math.round(223-(223-100)*t),b=Math.round(98-(98-50)*t);
      const op=cell.n?0.2+0.75*t:0.05;
      const label=cell.n?Math.round(100*v)+'%':'';
      html+=`<div style="aspect-ratio:1;background:rgba(${r},${g},${b},${op});border-radius:3px;display:flex;align-items:center;justify-content:center;font-weight:700;color:#fff;font-size:9.5px;cursor:default" title="${j} ${h}h : ${Math.round(100*v)}% complet (${cell.c}/${cell.n} séances observées)">${label}</div>`;});
  });
  hm.innerHTML=html;
}
function computeCreneau(jour,tranche){
  const hours=new Set(TRANCHES[tranche].hours);
  const f=DATA.filter(r=>{const h=parseInt((r.heure||'').slice(0,2));return r.jour===jour&&hours.has(h);});
  const c=f.filter(r=>r.statut==='complet').length;
  const rate=f.length?c/f.length:0;
  const byCours={};f.forEach(r=>{if(!r.cours)return;byCours[r.cours]=byCours[r.cours]||{c:0,n:0};byCours[r.cours].n++;if(r.statut==='complet')byCours[r.cours].c++;});
  const topCours=Object.entries(byCours).map(([k,o])=>[k,o.n?o.c/o.n:0,o.c,o.n]).filter(x=>x[3]>=2).sort((a,b)=>b[1]-a[1]).slice(0,5);
  return {n:f.length,c,rate,topCours};
}
function renderCreneauCompare(){
  const wrap=document.getElementById('creneauCompare');if(!wrap)return;
  const box=(side,sel)=>{const c=computeCreneau(sel.jour,sel.tranche);
    const color=side==='A'?'#e07a6f':'#e6c14d';
    return `<div style="background:var(--card2);padding:14px 16px;border-radius:10px;border-left:3px solid ${color}">
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <select data-side="${side}" data-field="jour" style="flex:1;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:13px">${JOURS.map(j=>`<option value="${j}" ${j===sel.jour?'selected':''}>${j}</option>`).join('')}</select>
        <select data-side="${side}" data-field="tranche" style="flex:1;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:13px">${Object.entries(TRANCHES).map(([k,v])=>`<option value="${k}" ${k===sel.tranche?'selected':''}>${v.label}</option>`).join('')}</select>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">
        <div><div style="font-size:22px;font-weight:800;color:#ff5a1f">${nf(c.n)}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Séances observées</div></div>
        <div><div style="font-size:22px;font-weight:800;color:${fillColor(c.rate)}">${Math.round(100*c.rate)}%</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Taux complet</div></div>
      </div>
      <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px">Top cours (taux complet)</div>
      <div style="font-size:11.5px">${c.topCours.length?c.topCours.map(([k,t,cc,n],i)=>`<div style="padding:3px 7px;background:var(--bg);border-radius:5px;margin-bottom:3px"><span style="color:var(--muted)">${i+1}.</span> ${k.slice(0,28)} <span style="color:${fillColor(t)};font-weight:700;float:right">${Math.round(100*t)}% (${cc}/${n})</span></div>`).join(''):'<div style="color:var(--muted);font-style:italic">—</div>'}</div>
    </div>`;};
  const cA=computeCreneau(CRENEAU_A.jour,CRENEAU_A.tranche),cB=computeCreneau(CRENEAU_B.jour,CRENEAU_B.tranche);
  const dR=Math.round(100*(cA.rate-cB.rate));
  wrap.innerHTML=`${box('A',CRENEAU_A)}${box('B',CRENEAU_B)}
    <div style="grid-column:span 2;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px 16px;text-align:center;font-size:13px;color:var(--muted)">
      <b style="color:#e07a6f">${CRENEAU_A.jour} ${TRANCHES[CRENEAU_A.tranche].label}</b> vs <b style="color:#e6c14d">${CRENEAU_B.jour} ${TRANCHES[CRENEAU_B.tranche].label}</b> ·
      Écart taux complet : <span style="color:${dR>0?'#5fcf8a':'#e07a6f'};font-weight:700">${dR>0?'+':''}${dR} pts</span>
    </div>`;
  wrap.querySelectorAll('select[data-side]').forEach(s=>s.addEventListener('change',e=>{
    const sel=e.target.dataset.side==='A'?CRENEAU_A:CRENEAU_B;sel[e.target.dataset.field]=e.target.value;
    renderCreneauCompare();
  }));
}
function renderTopBuckets(){
  const buckets=[];
  for(const jour of JOURS){for(const[tk,tv]of Object.entries(TRANCHES)){
    const hours=new Set(tv.hours);
    const f=DATA.filter(r=>{const h=parseInt((r.heure||'').slice(0,2));return r.jour===jour&&hours.has(h);});
    if(f.length<3)continue;
    const c=f.filter(r=>r.statut==='complet').length;
    buckets.push({label:jour+' '+tv.label,n:f.length,c,r:f.length?c/f.length:0});
  }}
  buckets.sort((a,b)=>b.r-a.r);
  const w=document.getElementById('topBuckets');if(!w)return;
  w.innerHTML=buckets.slice(0,20).map((b,i)=>`<div style="display:grid;grid-template-columns:32px 280px 1fr auto;gap:10px;align-items:center;font-size:13px;padding:5px 0;border-bottom:1px solid var(--line)">
    <span style="color:var(--muted);font-weight:700;text-align:right">${i+1}.</span>
    <span style="font-weight:600">${b.label}</span>
    <span style="height:8px;background:var(--line);border-radius:4px;overflow:hidden"><span style="display:block;height:100%;background:${fillColor(b.r)};width:${Math.round(100*b.r)}%"></span></span>
    <span style="color:var(--muted);font-variant-numeric:tabular-nums;min-width:160px;text-align:right">${Math.round(100*b.r)}% complet · ${b.c}/${b.n} séances</span>
  </div>`).join('')||'<div style="color:var(--muted)">Pas encore assez de séances observées par bucket (min. 3).</div>';
}
renderHeatmap();renderCreneauCompare();renderTopBuckets();

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
    store, muet = capture()
    rows = sorted(store.values(), key=lambda r: (r["date"], r["heure"], r.get("salle", "")))
    # On écrit CSV/HTML même en cas de passage muet : le store n'a pas bougé,
    # les livrables restent cohérents avec lui, et rien n'est perdu.
    write_csv(rows, "burningbar_seances.csv")
    write_html(rows, "burningbar.html")
    locked = [r for r in rows if r.get("locked")]
    print(f"OK [burningbar]: {len(rows)} séances en base, {len(locked)} figées.")
    # Sortie non nulle sur un 0 inexpliqué : le step est marqué en échec dans
    # l'UI Actions. Il garde son continue-on-error, sinon l'échec sauterait
    # aussi le commit de Sense-Club, qui partage ce job et n'y est pour rien.
    return 1 if muet else 0


if __name__ == "__main__":
    sys.exit(main())
