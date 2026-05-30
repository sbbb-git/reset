#!/usr/bin/env python3
"""Le 33 Foch (le33foch.fr) — fréquentation (Mindbody healcode).

Plateforme : Mindbody (widget healcode `071882500180` intégré sur
https://le33foch.fr/accueil/planning-et-reservations). Comme Banote /
Sense-Club, Mindbody n'expose PAS le nombre exact d'inscrits ni la
capacité chiffrée : on ne récupère qu'un STATUT par séance (« RÉSERVER »
= places dispo ; « Liste d'attente »/« Join Waitlist » = plein). On
accumule les relevés dans le33foch_data.json et on fige (verrouille) le
statut près du début.

Schéma le33foch_data.json (compatible comparateur) :
  dict {id: {date, jour, heure, fin, lieu, cours, coach, capacite,
             presents, finie, statut, releve}}.

Limite honnête : `presents` est une ESTIMATION dérivée du statut + d'une
capacité par défaut (CAP_DEFAUT) — plein => capacité, sinon inconnu (0).
Mindbody ne donne pas le compte réel.

Génère : le33foch_data.json, le33foch_seances.csv, le33foch.html.
"""
import csv
import datetime as dt
import json
import os
import re
import safestore
import sys
from zoneinfo import ZoneInfo

import le33foch_fetch

PARIS = ZoneInfo("Europe/Paris")
STORE = "le33foch_data.json"
CSV = "le33foch_seances.csv"
HTML = "le33foch.html"
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
LOCK_MIN = 15            # verrouille le statut <= 15 min avant le début
CAP_DEFAUT = 12          # capacité par défaut (club privé, petits cours)
ACCENT = "#c6a26f"       # doré (couleur du site / club privé)
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


def lieu_court(nom, cours):
    """Lieu lisible. Certains cours sont au 32 Tilsitt (sous-adresse du même
    club) — on les attribue à cette annexe pour la lisibilité."""
    if cours and re.match(r"^\s*32\s*TILSITT", cours, re.I):
        return "Le 33 Foch — 32 Tilsitt"
    n = (nom or "").upper()
    if "33" in n or "FOCH" in n or "CERCLE" in n:
        return "Le 33 Foch"
    return (nom or "Le 33 Foch").title()


def clean_cours(name):
    """Le widget renvoie souvent 'CATEGORIE - NOM' ; on garde le nom.
    Exemples : 'COURS CARDIO - BOOTCAMP' -> 'BOOTCAMP',
    'PILATES REFORMER - PILATES REFORMER' -> 'PILATES REFORMER',
    '32 TILSITT - HOUSE DANCE (au 32 Rue Tilsitt 75017 PARIS)' -> 'HOUSE DANCE'."""
    n = (name or "").strip()
    if not n:
        return n
    # retire un éventuel suffixe "(au ... PARIS)"
    n = re.sub(r"\s*\(au [^)]+\)\s*$", "", n, flags=re.I).strip()
    # CATEGORIE - NOM  (cat = lettres/chiffres/espaces/&/slash, au moins un mot)
    m = re.match(r"^([A-Za-zÀ-ÿ0-9&/ ]{2,40})\s*[-–]\s*(.+)$", n)
    if m:
        tail = m.group(2).strip()
        if tail:
            return tail
    return n


def load_store():
    return safestore.load(STORE)


def save_store(store):
    safestore.save(store, STORE)


def capture():
    now = dt.datetime.now(PARIS)
    store = load_store()
    sessions = le33foch_fetch.fetch_all()
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
        if statut == "inconnu" and prev and prev.get("statut") in ("disponible", "complet"):
            statut = prev["statut"]
        cap = CAP_DEFAUT
        lock = now >= sdt - dt.timedelta(minutes=LOCK_MIN)
        cours_raw = (s.get("cours") or "").strip()
        store[sid] = {
            "id": sid,
            "date": sdt.date().isoformat(),
            "jour": JOURS_FR[sdt.weekday()],
            "heure": sdt.strftime("%H:%M"),
            "fin": edt.strftime("%H:%M"),
            "lieu": lieu_court(s.get("lieu"), cours_raw),
            "cours": clean_cours(cours_raw),
            "coach": (s.get("coach") or "").strip(),
            "capacite": cap if statut in ("complet", "presque complet") else 0,
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
    # garde-fou : jamais presents > capacite
    for v in store.values():
        if v.get("presents", 0) > v.get("capacite", 0):
            v["presents"] = v["capacite"]
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
    html = (HTML_TEMPLATE
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
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LE 33 FOCH - Fréquentation</title>
<script>__CHARTJS__</script>
<style>
  :root{--bg:#0a0a0a;--card:#141210;--card2:#1f1c17;--line:#2e2920;
        --text:#f5efe2;--muted:#b9ac8c;--accent:__ACCENT__;--accent2:__ACCENT2__;
        --green:#7bc98a;--yellow:#e6c14d;--red:#e07a6f;}
  *{box-sizing:border-box;}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);}
  header{padding:28px 32px 12px;border-bottom:1px solid var(--line);}
  h1{margin:0;font-size:28px;font-weight:800;letter-spacing:4px;color:var(--accent2);text-transform:uppercase;}
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
  .panel h2{margin:0 0 14px;font-size:14px;color:var(--accent2);font-weight:700;letter-spacing:1px;text-transform:uppercase;}
  canvas{max-height:280px;}
  .filters{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:8px 0 16px;}
  .filters input,.filters select{background:var(--card2);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:9px 12px;font-size:13px;}
  .filters input{flex:1;min-width:160px;}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap;}
  th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--card);}
  tbody tr:hover{background:var(--card2);}
  .pill{padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;}
  .ranklist{display:flex;flex-direction:column;gap:9px;}
  .rk{display:grid;grid-template-columns:170px 1fr auto;align-items:center;gap:10px;font-size:13px;}
  .rk .lbl{font-weight:600;overflow:hidden;text-overflow:ellipsis;} .rk .track{height:9px;background:var(--line);border-radius:5px;overflow:hidden;}
  .rk .track>span{display:block;height:100%;background:var(--accent);border-radius:5px;} .rk .val{color:var(--muted);}
  .btn{background:var(--accent);color:#0a0a0a;border:none;border-radius:9px;padding:9px 16px;font-size:13px;font-weight:700;cursor:pointer;}
  .tablewrap{max-height:600px;overflow:auto;border:1px solid var(--line);border-radius:14px;}
  .foot{color:var(--muted);font-size:12px;margin-top:18px;}
  @media(max-width:600px){header{padding:18px 14px 6px;}h1{font-size:20px;}.wrap{padding:0 12px 32px;}.kpis{grid-template-columns:1fr 1fr;gap:10px;}.kpi .v{font-size:21px;}.filters input,.filters select,.btn{font-size:15px;width:100%;}.rk{grid-template-columns:120px 1fr auto;}}
  @media(max-width:600px){canvas{max-height:200px!important}th,td{padding:7px 6px;font-size:12px}.panel{padding:15px 14px}.kpi .v{font-size:20px}.note{font-size:12px}.ctrl{font-size:12px;gap:7px}}
</style>
</head>
<body>
<header>
  <h1>Le 33 Foch</h1>
  <div class="sub">Fréquentation &middot; généré le __GENERATED__ &middot; Mindbody (statut seulement)</div>
</header>
<div class="wrap">
  <div class="note">ℹ️ <b>Mindbody n'expose qu'un statut par séance</b> (pas le nombre exact d'inscrits ni la capacité). On déduit : <b>« complet » = __CAP__ présents</b> (capacité par défaut), sinon le compte exact est inconnu. Le statut est <b>figé ~15 min avant chaque séance</b>. L'historique se construit au fil des relevés. Les présents sont donc une <b>borne basse / estimation</b>, pas une mesure exacte.</div>
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
  <div class="panel">
    <h2>Détail des séances (statut figé)</h2>
    <div class="filters"><button id="btnExport" class="btn">Exporter en Excel</button></div>
    <div class="tablewrap"><table id="tbl"><thead></thead><tbody></tbody></table></div>
  </div>
  <div class="foot">Source : le33foch.fr (widget Mindbody). Statut relevé près du début de chaque séance.</div>
</div>
<script>
const ALL=__DATA__;
const CAP=__CAP__;
const JOURS=["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"];
const STC={'complet':'#e07a6f','disponible':'#7bc98a','inconnu':'#b9ac8c','annule':'#6b5d44'};
const LBL={'complet':'complet','disponible':'des places','inconnu':'inconnu','annule':'annulé'};
const nf=v=>Math.round(v).toLocaleString('fr-FR');
function fmtJ(iso){const p=iso.split('-');return `${p[2]}/${p[1]}/${p[0]}`;}
Chart.defaults.color='#b9ac8c';Chart.defaults.borderColor='#2e2920';Chart.defaults.font.family=getComputedStyle(document.body).fontFamily;
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

  renderTable(D);
}
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
document.getElementById('btnExport').addEventListener('click',()=>{
  const esc=v=>{v=(''+v).replace(/"/g,'""');return /[";\n]/.test(v)?`"${v}"`:v;};
  const lines=[cols.map(c=>c[1]).join(';')];
  currentRows.forEach(r=>lines.push(cols.map(c=>esc(c[0]==='date'?fmtJ(r.date):(r[c[0]]==null?'':r[c[0]]))).join(';')));
  const blob=new Blob(['﻿'+lines.join('\r\n')],{type:'text/csv;charset=utf-8;'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='le33foch_seances.csv';document.body.appendChild(a);a.click();a.remove();
});
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
    print(f"OK [le33foch]: {len(rows)} séances en base, {len(fin)} figées. Lieux: {lieux}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
