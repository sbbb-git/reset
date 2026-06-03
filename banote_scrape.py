#!/usr/bin/env python3
"""Banote (banoteclub.com) — fréquentation des 3 lieux (Mindbody healcode).

Plateforme : Mindbody (widgets healcode / brandedweb). Comme Sense-Club,
Mindbody n'expose PAS le nombre exact d'inscrits ni la capacité chiffrée :
on ne récupère qu'un STATUT par séance (« RESERVER »/« Book » = des places
restent ; « Liste d'attente »/« Complet » = plein). On accumule les relevés
dans banote_data.json et on fige (verrouille) le statut près du début.

Schéma banote_data.json (compatible comparateur) : dict {id: {date, jour,
heure, fin, lieu, cours, coach, capacite, presents, finie, statut, releve}}.
Limite honnête : `presents` est une ESTIMATION dérivée du statut + d'une
capacité par défaut Lagree (CAP_DEFAUT) — plein => capacité, sinon inconnu
(0). Mindbody ne donne pas le compte réel.

Génère : banote_data.json, banote_seances.csv, banote.html.
"""
import csv
import datetime as dt
import json
import os
import safestore
import sys
from zoneinfo import ZoneInfo

import banote_fetch
from template_common import (
    CSS_COMMON,
    EXPORT_CSV_BLOCK,
    HEAD_COMMON,
    HEATMAP_BLOCK_STATUT,
    HEATMAP_PANELS_HTML,
)

PARIS = ZoneInfo("Europe/Paris")
STORE = "banote_data.json"
CSV = "banote_seances.csv"
HTML = "banote.html"
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
LOCK_MIN = 15            # verrouille le statut <= 15 min avant le début
CAP_DEFAUT = 12          # capacité Lagree/Megaformer par défaut (estimation)
ACCENT = "#c9a24b"       # doré Banote
ACCENT2 = "#e3c879"

FIELDS = ["id", "date", "jour", "heure", "fin", "lieu", "cours", "coach",
          "capacite", "presents", "finie", "statut", "releve"]


def normalize_statut(cart, canceled):
    """-> ('complet'|'disponible'|'annule'|'inconnu')."""
    if canceled:
        return "annule"
    c = (cart or "").lower()
    if not c:
        return "inconnu"            # réservation fermée (séance imminente/passée)
    if "waitlist" in c or "attente" in c or "complet" in c or "full" in c:
        return "complet"
    if "reserv" in c or "book" in c or "réserv" in c:
        return "disponible"
    return "inconnu"


def estim_presents(statut, capacite):
    if statut == "complet":
        return capacite
    return 0  # dispo/inconnu : compte exact non exposé par Mindbody


def lieu_court(nom):
    n = (nom or "").upper()
    if "16E" in n or "PARIS 16" in n:
        return "Banote Club Paris 16e"
    if "94" in n or "CHARENTON" in n:
        return "Banote Club Charenton"
    if "MOORE" in n or "COLLECTIONNEUR" in n:
        return "Banote x Collectionneur"
    return (nom or "Banote").title()


def load_store():
    return safestore.load(STORE)


def save_store(store):
    safestore.save(store, STORE)


def capture():
    now = dt.datetime.now(PARIS)
    store = load_store()
    sessions = banote_fetch.fetch_all()
    locked_now = 0
    for s in sessions:
        sid = str(s["id"])
        try:
            sdt = dt.datetime.fromisoformat(s["start"]).replace(tzinfo=PARIS)
        except ValueError:
            continue
        try:
            edt = dt.datetime.fromisoformat(s["end"]).replace(tzinfo=PARIS) if s.get("end") else sdt
        except ValueError:
            edt = sdt
        prev = store.get(sid)
        if prev and prev.get("finie"):
            continue  # déjà figé/terminé -> on ne touche plus
        statut = normalize_statut(s.get("cart"), s.get("canceled"))
        # garde le dernier statut "vivant" connu si le nouveau passe à inconnu
        if statut == "inconnu" and prev and prev.get("statut") in ("disponible", "complet"):
            statut = prev["statut"]
        cap = CAP_DEFAUT
        lock = now >= sdt - dt.timedelta(minutes=LOCK_MIN)
        store[sid] = {
            "id": sid,
            "date": sdt.date().isoformat(),
            "jour": JOURS_FR[sdt.weekday()],
            "heure": sdt.strftime("%H:%M"),
            "fin": edt.strftime("%H:%M"),
            "lieu": lieu_court(s.get("lieu")),
            "cours": (s.get("cours") or "").strip(),
            "coach": (s.get("coach") or "").strip(),
            "capacite": 0 if statut == "annule" else cap,
            "presents": estim_presents(statut, cap),
            "finie": lock,
            "statut": statut,
            "releve": now.strftime("%Y-%m-%d %H:%M"),
        }
        if lock:
            locked_now += 1
    # fige les séances passées qu'on ne voit plus
    for v in store.values():
        if v.get("finie"):
            continue
        try:
            sdt = dt.datetime.fromisoformat(v["date"] + "T" + v["heure"]).replace(tzinfo=PARIS)
        except (ValueError, KeyError):
            continue
        if now >= sdt:
            v["finie"] = True
    save_store(store)
    fin = sum(1 for v in store.values() if v.get("finie"))
    print(f"{now:%Y-%m-%d %H:%M} : {len(sessions)} séances vues, "
          f"{locked_now} verrouillées ce passage, {len(store)} en base ({fin} figées).")
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
    # Injecte d'abord les blocs communs (template_common), puis les valeurs
    # dynamiques. L'ordre compte : __CHARTJS__/__ACCENT__/__ACCENT2__ vivent
    # dans HEAD_COMMON / CSS_COMMON, donc on remplace les blocs en premier.
    html = (HTML_TEMPLATE
            .replace("__HEAD_COMMON__", HEAD_COMMON)
            .replace("__CSS_COMMON__", CSS_COMMON)
            .replace("__HEATMAP_PANELS_HTML__", HEATMAP_PANELS_HTML)
            .replace("__HEATMAP_BLOCK__", HEATMAP_BLOCK_STATUT)
            .replace("__EXPORT_CSV_BLOCK__", EXPORT_CSV_BLOCK)
            .replace("__FILENAME__", "banote_seances.csv")
            .replace("__CHARTJS__", chartjs)
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
            .replace("__GENERATED__", dt.datetime.now(PARIS).strftime("%d/%m/%Y %H:%M"))
            .replace("__CAP__", str(CAP_DEFAUT))
            .replace("__ACCENT__", ACCENT)
            .replace("__ACCENT2__", ACCENT2))
    with open(HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"-> {HTML}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
__HEAD_COMMON__
<title>BANOTE - Fréquentation</title>
__CSS_COMMON__
</head>
<body>
<header>
  <h1>BANOTE</h1>
  <div class="sub">Fréquentation &middot; généré le __GENERATED__ &middot; 3 lieux (Mindbody)</div>
</header>
<div class="wrap">
  <div class="note">ℹ️ <b>Mindbody n'expose qu'un statut par séance</b> (pas le nombre exact d'inscrits ni la capacité). On déduit : <b>« complet » = __CAP__ présents</b> (capacité Lagree estimée), sinon le compte exact est inconnu. Le statut est <b>figé ~15 min avant chaque séance</b>. L'historique se construit au fil des relevés. Les présents sont donc une <b>borne basse / estimation</b>, pas une mesure exacte.</div>
  <div id="periode" style="background:var(--card2);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:11px 16px;margin:14px 0 4px;color:var(--text);font-size:13.5px;font-weight:600"></div>
  <div class="kpis" id="kpis"></div>
  <div class="filters">
    <input id="q" placeholder="Rechercher (cours, coach...)">
    <select id="fLieu"></select>
    <select id="fStatut"></select>
  </div>
  <div class="grid">
    <div class="panel"><h2>Séances complètes par jour</h2><canvas id="cDay"></canvas></div>
    <div class="panel"><h2>Répartition par lieu</h2><canvas id="cLieu"></canvas></div>
  </div>
  <div class="grid">
    <div class="panel"><h2>Créneaux les plus « complet »</h2><div id="topHour" class="ranklist"></div></div>
    <div class="panel"><h2>Taux de remplissage (complet) par lieu</h2><div id="cmpLieu" class="ranklist"></div></div>
  </div>
  <div class="panel"><h2>Statuts par type de cours</h2><canvas id="cCours"></canvas></div>
__HEATMAP_PANELS_HTML__

  <div class="panel">
    <h2>Détail des séances (statut figé)</h2>
    <div class="filters"><button id="btnExport" class="btn">Exporter en Excel</button></div>
    <div class="tablewrap"><table id="tbl"><thead></thead><tbody></tbody></table></div>
  </div>
  <div class="foot">Source : banoteclub.com (widgets Mindbody). Statut relevé près du début de chaque séance.</div>
</div>
<script>
const ALL=__DATA__;
const CAP=__CAP__;
const JOURS=["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"];
const STC={'complet':'#e07a6f','disponible':'#7bc98a','inconnu':'#b9ac8c','annule':'#6b5d44'};
const LBL={'complet':'complet','disponible':'des places','inconnu':'inconnu','annule':'annulé'};
const nf=v=>Math.round(v).toLocaleString('fr-FR');
function fmtJ(iso){const p=iso.split('-');return `${p[2]}/${p[1]}/${p[0]}`;}
Chart.defaults.color='#b9ac8c';Chart.defaults.borderColor='#352c1d';Chart.defaults.font.family=getComputedStyle(document.body).fontFamily;
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const ACC=css('--accent'),ACC2=css('--accent2');
let charts={};
const isNarrow=()=>matchMedia('(max-width:600px)').matches;
// on ne compte que les séances figées (statut définitif) et non annulées
const DATA=ALL.filter(r=>r.finie&&r.statut!=='annule'&&(r.capacite||0)>0);
const selLieu=document.getElementById('fLieu'),selStatut=document.getElementById('fStatut');
function fillSel(sel,vals,label){sel.innerHTML='';sel.add(new Option(label,''));[...new Set(vals)].filter(Boolean).sort().forEach(v=>sel.add(new Option(LBL[v]||v,v)));}
fillSel(selLieu,DATA.map(r=>r.lieu),'Tous les lieux');
fillSel(selStatut,DATA.map(r=>r.statut),'Tous les statuts');
function current(){
  const q=document.getElementById('q').value.toLowerCase();
  return DATA.filter(r=>(!selLieu.value||r.lieu===selLieu.value)
    &&(!selStatut.value||r.statut===selStatut.value)
    &&(!q||((r.cours+' '+r.coach+' '+r.lieu).toLowerCase().includes(q))));
}
function mkChart(id,cfg){if(charts[id])charts[id].destroy();charts[id]=new Chart(document.getElementById(id),cfg);}
function render(){
  const D=current();
  const nComplet=D.filter(r=>r.statut==='complet').length;
  const nLieux=new Set(D.map(r=>r.lieu)).size||1;
  const totPres=D.reduce((s,r)=>s+r.presents,0);
  const dts=[...new Set(D.map(r=>r.date))].sort(),pj=dts.length;
  document.getElementById('periode').textContent = pj
    ? `📅 Période : du ${fmtJ(dts[0])} au ${fmtJ(dts[pj-1])} · ${pj} jour${pj>1?'s':''} · ${nLieux} lieu${nLieux>1?'x':''} · ${nf(D.length)} séances figées`
    : 'Aucune séance figée sur cette sélection (l\'historique se construit au fil des relevés).';
  document.getElementById('kpis').innerHTML=[
    ['Séances figées',nf(D.length)],
    ['Lieux',nf(nLieux)],
    ['Complètes',nf(nComplet)+(D.length?` (${Math.round(100*nComplet/D.length)}%)`:'')],
    ['Présents estimés',nf(totPres)],
    ['Présents / lieu',nf(totPres/nLieux)],
    ['Compl. / lieu',nf(nComplet/nLieux)],
  ].map(k=>`<div class="kpi"><div class="v">${k[1]}</div><div class="l">${k[0]}</div></div>`).join('');

  const byDay={};D.forEach(r=>{byDay[r.date]=byDay[r.date]||{c:0,t:0};byDay[r.date].t++;if(r.statut==='complet')byDay[r.date].c++;});
  const days=Object.keys(byDay).sort();
  mkChart('cDay',{type:'bar',data:{labels:days.map(d=>d.slice(8)+'/'+d.slice(5,7)),
    datasets:[{label:'complètes',data:days.map(d=>byDay[d].c),backgroundColor:STC.complet},
              {label:'autres',data:days.map(d=>byDay[d].t-byDay[d].c),backgroundColor:ACC}]},
    options:{plugins:{legend:{display:true}},scales:{x:{stacked:true},y:{stacked:true,beginAtZero:true,ticks:{callback:v=>nf(v)}}}}});

  const byL={};D.forEach(r=>byL[r.lieu]=(byL[r.lieu]||0)+1);
  const lieux=Object.keys(byL).sort((a,b)=>byL[b]-byL[a]);
  mkChart('cLieu',{type:'doughnut',data:{labels:lieux,datasets:[{data:lieux.map(l=>byL[l]),
    backgroundColor:[ACC,ACC2,'#7bc98a','#e07a6f','#9b7ff0']}]},
    options:{plugins:{legend:{position:isNarrow()?'bottom':'right'}}}});

  const byH={};D.forEach(r=>{if(r.statut==='complet')byH[r.heure]=(byH[r.heure]||0)+1;});
  const hrs=Object.entries(byH).sort((a,b)=>b[1]-a[1]).slice(0,8);
  const mxH=hrs.length?hrs[0][1]:0;
  document.getElementById('topHour').innerHTML=hrs.length?hrs.map(([h,v])=>
    `<div class="rk"><span class="lbl">${h}</span><span class="track"><span style="width:${mxH?Math.round(100*v/mxH):0}%"></span></span><span class="val">${v}×</span></div>`).join('')
    :'<div style="color:var(--muted)">Pas encore de séance complète figée.</div>';

  const byS={};D.forEach(r=>{byS[r.lieu]=byS[r.lieu]||{c:0,n:0};byS[r.lieu].n++;if(r.statut==='complet')byS[r.lieu].c++;});
  const studios=Object.entries(byS).map(([k,o])=>[k,o.n?o.c/o.n:0,o.c,o.n]).sort((a,b)=>b[1]-a[1]);
  document.getElementById('cmpLieu').innerHTML=studios.length?studios.map(([k,t,c,n])=>
    `<div class="rk"><span class="lbl">${k}</span><span class="track"><span style="width:${Math.round(100*t)}%"></span></span><span class="val">${Math.round(100*t)}% complet &middot; ${c}/${n}</span></div>`).join('')
    :'<div style="color:var(--muted)">Pas encore de données.</div>';

  const byC={};D.forEach(r=>{const k=r.cours||'?';byC[k]=byC[k]||{complet:0,disponible:0,inconnu:0};byC[k][r.statut]=(byC[k][r.statut]||0)+1;});
  const cours=Object.keys(byC).sort((a,b)=>(byC[b].complet+byC[b].disponible)-(byC[a].complet+byC[a].disponible));
  mkChart('cCours',{type:'bar',data:{labels:cours,datasets:[
    {label:'complet',data:cours.map(c=>byC[c].complet),backgroundColor:STC.complet},
    {label:'des places',data:cours.map(c=>byC[c].disponible),backgroundColor:STC.disponible},
    {label:'inconnu',data:cours.map(c=>byC[c].inconnu),backgroundColor:STC.inconnu}]},
    options:{indexAxis:'y',plugins:{legend:{display:true}},scales:{x:{stacked:true,beginAtZero:true,ticks:{callback:v=>nf(v)}},y:{stacked:true}}}});

  renderHeatmap(D);
  renderCreneauCompare(D);
  renderTopBuckets(D);
  renderTable(D);
}

__HEATMAP_BLOCK__

const cols=[['date','Date'],['jour','Jour'],['heure','Heure'],['lieu','Lieu'],['cours','Cours'],['coach','Coach'],['statut','Statut'],['presents','Présents est.']];
document.querySelector('#tbl thead').innerHTML='<tr>'+cols.map(c=>`<th>${c[1]}</th>`).join('')+'</tr>';
let currentRows=[];
function renderTable(D){
  let rows=[...D].sort((a,b)=>(a.date+a.heure)<(b.date+b.heure)?1:-1);
  currentRows=rows;
  document.querySelector('#tbl tbody').innerHTML=rows.map(r=>
    `<tr><td>${fmtJ(r.date)}</td><td>${r.jour}</td><td>${r.heure}</td><td>${r.lieu}</td><td>${r.cours}</td><td>${r.coach||'—'}</td>`
    +`<td><span class="pill" style="background:${STC[r.statut]}33;color:${STC[r.statut]}">${LBL[r.statut]||r.statut}</span></td>`
    +`<td>${r.statut==='complet'?r.presents+' / '+CAP:'—'}</td></tr>`).join('');
}
['q'].forEach(id=>document.getElementById(id).addEventListener('input',render));
[selLieu,selStatut].forEach(s=>s.addEventListener('change',render));
__EXPORT_CSV_BLOCK__
render();
</script>
</body>
</html>"""


def main():
    store = capture()
    rows = sorted(store.values(), key=lambda r: (r["date"], r["heure"], r.get("lieu", "")))
    write_csv(rows)
    write_html(rows)
    fin = [r for r in rows if r.get("finie")]
    lieux = sorted({r["lieu"] for r in rows})
    print(f"OK [banote]: {len(rows)} séances en base, {len(fin)} figées. Lieux: {lieux}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
