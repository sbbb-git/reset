#!/usr/bin/env python3
"""DNA Pilates Paris (dnapilatesparis.com) — fréquentation des 2 studios.

Plateforme : Mindbody healcode (widget unique). Le booking est intégré via
`widgets.mindbodyonline.com/javascripts/healcode.js` avec un seul widget
`712164001d62` qui couvre les 2 lieux (location 1 = Yvon Villarceau, 2 = Victor
Hugo). Comme pour Banote / Sense-Club, Mindbody n'expose PAS le nombre exact
d'inscrits ni la capacité chiffrée — on n'a qu'un STATUT par séance :
  - bouton « Book » / « Réserver »   -> il reste des places (disponible)
  - bouton « Join Waitlist »          -> séance complète

On accumule les relevés dans dna_data.json (anti-perte via safestore) et on
fige (verrouille) le statut près du début de chaque séance. Cap par défaut = 7
(typique reformer studio Paris) — c'est une ESTIMATION, pas une mesure exacte.

Schéma dna_data.json (compatible comparateur) :
  dict {id: {date, jour, heure, fin, lieu, cours, coach, capacite, presents,
             finie, statut, releve}}.

Génère : dna_data.json, dna_seances.csv, dna.html.
"""
import csv
import dashboard_meta
from template_common import meta_panel_html
import datetime as dt
import html as _html
import json
import os
import re
import safestore
import ssl
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
STORE = "dna_data.json"
CSV = "dna_seances.csv"
HTML = "dna.html"
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
LOCK_MIN = 15            # verrouille le statut <= 15 min avant le début
CAP_DEFAUT = 7           # capacité reformer typique (estimation honnête)
ACCENT = "#1a1a1a"       # noir DNA
ACCENT2 = "#bfa46f"      # accent doré subtil

WIDGET_ID = "712164001d62"
BASE = "https://widgets.mindbodyonline.com/widgets/schedules"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"

# Mapping location id -> libellé court
LIEU_MAP = {
    "1": "DNA Yvon Villarceau",
    "2": "DNA Victor Hugo",
}

_LAX_SSL = ssl.create_default_context()
_LAX_SSL.check_hostname = False
_LAX_SSL.verify_mode = ssl.CERT_NONE

FIELDS = ["id", "date", "jour", "heure", "fin", "lieu", "cours", "coach",
          "capacite", "presents", "finie", "statut", "releve"]


def load_markup(start_date, retries=5):
    params = {"options[start_date]": start_date, "preview": "false"}
    url = f"{BASE}/{WIDGET_ID}/load_markup?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "application/json",
                "Referer": "https://dnapilatesparis.com/"})
            ctx = _LAX_SSL if attempt else None
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            # Backoff exponentiel 3s,6s,12s,24s,48s. Mindbody throttle parfois
            # sur l'IP runner GitHub quand on enchaîne plusieurs scrapers Mindbody.
            time.sleep(3 * (2 ** attempt))
    raise last


def _clean(m):
    if not m:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()


def parse_sessions(html_str):
    html_str = _html.unescape(html_str or "")
    out = []
    for block in re.split(r'(?=<div class="bw-session" )', html_str):
        if 'class="bw-session"' not in block:
            continue
        sid = re.search(r'data-bw-widget-id="(\d+)"', block)
        sdt = re.search(r'<time class="hc_starttime" datetime="([^"]+)"', block)
        edt = re.search(r'<time class="hc_endtime" datetime="([^"]+)"', block)
        if not sid or not sdt:
            continue
        loc_id = re.search(r'data-bw-widget-location="(\d+)"', block)
        nm = re.search(r'<div class="bw-session__name">(.*?)</div>', block, re.S)
        staff = re.search(r'<div class="bw-session__staff"[^>]*>(.*?)</div>', block, re.S)
        locn = re.search(r'<div class="bw-session__location"[^>]*>(.*?)</div>', block, re.S)
        cart = _clean(re.search(r'<span class="bw-widget__cart_button">(.*?)</span>', block, re.S))
        cours = _clean(nm)
        cours = re.sub(r"^[A-Za-z]+\s*[-–]\s*", "", cours).strip() or cours
        lieu_raw = _clean(locn)
        lieu = LIEU_MAP.get(loc_id.group(1) if loc_id else "", lieu_raw or "DNA")
        out.append({
            "id": sid.group(1),
            "start": sdt.group(1),
            "end": edt.group(1) if edt else "",
            "cours": cours,
            "coach": _clean(staff),
            "lieu": lieu,
            "cart": cart,
        })
    return out


def fetch_all():
    today = dt.date.today()
    seen, sessions = set(), []
    # le widget renvoie ~2 semaines à partir de start_date ; on couvre 3
    # ancres pour balayer 2-3 semaines complètes.
    for off in (0, 7, 14):
        d = (today + dt.timedelta(days=off)).isoformat()
        try:
            data = load_markup(d)
        except Exception as e:  # noqa: BLE001
            print(f"  (semaine {d} indispo : {e})", file=sys.stderr)
            continue
        for s in parse_sessions(data.get("class_sessions") or ""):
            if s["id"] in seen:
                continue
            seen.add(s["id"])
            sessions.append(s)
    return sessions


def normalize_statut(cart):
    c = (cart or "").lower()
    if not c:
        return "inconnu"
    if "waitlist" in c or "attente" in c or "complet" in c or "full" in c:
        return "complet"
    if "reserv" in c or "book" in c or "réserv" in c:
        return "disponible"
    return "inconnu"


def estim_presents(statut, capacite):
    if statut == "complet":
        return capacite
    return 0


def load_store():
    return safestore.load(STORE)


def save_store(store):
    safestore.save(store, STORE)


def capture():
    now = dt.datetime.now(PARIS)
    store = load_store()
    try:
        sessions = fetch_all()
    except Exception as e:  # noqa: BLE001
        print(f"  (fetch DNA échoué : {e})", file=sys.stderr)
        return store
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
            continue
        statut = normalize_statut(s.get("cart"))
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
            "lieu": s.get("lieu") or "DNA",
            "cours": (s.get("cours") or "").strip(),
            "coach": (s.get("coach") or "").strip(),
            "capacite": (cap if statut in ("complet","presque complet") else 0),
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
    last_iso = ""
    _releves = [r.get("releve") or "" for r in (rows if isinstance(rows,list) else rows.values()) if isinstance(r, dict)]
    _last = max(_releves) if _releves else ""
    if _last:
        try:
            last_iso = dt.datetime.strptime(_last[:16], "%Y-%m-%d %H:%M").replace(tzinfo=PARIS).isoformat()
        except ValueError:
            pass
    _n_rows = len(rows) if isinstance(rows, (list, dict)) else 0
    _m = dashboard_meta.get("dna")
    _meta_html = meta_panel_html(_m["method"], _m["risk"], _m["freq"], last_iso, _n_rows)

    html = (HTML_TEMPLATE
            .replace("__CHARTJS__", chartjs)
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
            .replace("__GENERATED__", dt.datetime.now(PARIS).strftime("%d/%m/%Y %H:%M")).replace("__META_PANEL__", _meta_html)
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
<title>DNA PILATES - Fréquentation</title>
<script>__CHARTJS__</script>
<style>
  :root{--bg:#0a0a0a;--card:#141414;--card2:#1c1c1c;--line:#2a2a2a;
        --text:#f5f3ee;--muted:#9a9489;--accent:__ACCENT__;--accent2:__ACCENT2__;
        --green:#7bc98a;--yellow:#e6c14d;--red:#e07a6f;}
  *{box-sizing:border-box;}
  body{margin:0;font-family:Georgia,'Cormorant Garamond','Times New Roman',serif;background:var(--bg);color:var(--text);}
  header{padding:32px 32px 14px;text-align:center;border-bottom:1px solid var(--line);}
  h1{margin:0;font-size:30px;font-weight:400;letter-spacing:14px;color:var(--text);font-family:Georgia,serif;}
  .tag{display:block;margin-top:8px;font-family:-apple-system,sans-serif;font-size:10px;color:var(--accent2);letter-spacing:6px;text-transform:uppercase;}
  .sub{color:var(--muted);font-size:13px;margin-top:10px;font-family:-apple-system,sans-serif;}
  .wrap{padding:0 32px 48px;max-width:1180px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}
  .note{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px 18px;margin:22px 0 4px;color:var(--muted);font-size:13px;}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:16px;margin:22px 0;}
  .kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;}
  .kpi .v{font-size:28px;font-weight:700;color:var(--accent2);font-family:Georgia,serif;}
  .kpi .l{color:var(--muted);font-size:11px;margin-top:4px;text-transform:uppercase;letter-spacing:1.2px;}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:22px;}
  @media(max-width:880px){.grid{grid-template-columns:1fr;}}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;}
  .panel h2{margin:0 0 14px;font-size:13px;color:var(--accent2);font-weight:600;letter-spacing:2px;text-transform:uppercase;}
  canvas{max-height:280px;}
  .filters{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:8px 0 16px;}
  .filters input,.filters select{background:var(--card2);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:9px 12px;font-size:13px;}
  .filters input{flex:1;min-width:160px;}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap;}
  th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--card);text-transform:uppercase;letter-spacing:1px;font-size:11px;}
  tbody tr:hover{background:var(--card2);}
  .pill{padding:3px 10px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.5px;}
  .ranklist{display:flex;flex-direction:column;gap:9px;}
  .rk{display:grid;grid-template-columns:180px 1fr auto;align-items:center;gap:10px;font-size:13px;}
  .rk .lbl{font-weight:600;overflow:hidden;text-overflow:ellipsis;} .rk .track{height:9px;background:var(--line);border-radius:5px;overflow:hidden;}
  .rk .track>span{display:block;height:100%;background:var(--accent2);border-radius:5px;} .rk .val{color:var(--muted);}
  .btn{background:var(--accent2);color:#0a0a0a;border:none;border-radius:9px;padding:9px 16px;font-size:12px;font-weight:700;cursor:pointer;letter-spacing:1px;text-transform:uppercase;}
  .tablewrap{max-height:600px;overflow:auto;border:1px solid var(--line);border-radius:14px;}
  .foot{color:var(--muted);font-size:12px;margin-top:18px;text-align:center;}
  @media(max-width:600px){header{padding:18px 14px 8px;}h1{font-size:18px;letter-spacing:8px;}.wrap{padding:0 12px 32px;}.kpis{grid-template-columns:1fr 1fr;gap:10px;}.kpi .v{font-size:20px;}.filters input,.filters select,.btn{font-size:14px;width:100%;}.rk{grid-template-columns:130px 1fr auto;}}
  @media(max-width:600px){canvas{max-height:200px!important}th,td{padding:7px 6px;font-size:12px}.panel{padding:15px 14px}.kpi .v{font-size:20px}.note{font-size:12px}.ctrl{font-size:12px;gap:7px}}
</style>
</head>
<body>
<header>
  <h1>DNA PILATES</h1>
  <span class="tag">Performance &middot; Excellence &middot; Persévérance</span>
  <div class="sub">Fréquentation des 2 studios &middot; généré le __GENERATED__</div>
</header>
<div class="wrap">
  __META_PANEL__
  <div class="note">ℹ️ <b>Mindbody n'expose qu'un statut par séance</b> (pas le nombre exact d'inscrits ni la capacité chiffrée). Lecture du widget public : bouton « Book » = il reste des places, bouton « Join Waitlist » = séance complète. On déduit : <b>« complet » = __CAP__ présents</b> (capacité reformer estimée). Le statut est <b>figé ~15 min avant chaque séance</b>. L'historique se construit au fil des relevés — c'est une <b>estimation honnête</b>, pas une mesure exacte.</div>
  <div id="periode" style="background:var(--card2);border:1px solid var(--line);border-left:4px solid var(--accent2);border-radius:10px;padding:11px 16px;margin:14px 0 4px;color:var(--text);font-size:13.5px;font-weight:600"></div>
  <div class="kpis" id="kpis"></div>
  <div class="filters">
    <input id="q" placeholder="Rechercher (cours, coach...)">
    <select id="fLieu"></select>
    <select id="fStatut"></select>
  </div>
  <div class="grid">
    <div class="panel"><h2>Séances complètes par jour</h2><canvas id="cDay"></canvas></div>
    <div class="panel"><h2>Répartition par studio</h2><canvas id="cLieu"></canvas></div>
  </div>
  <div class="grid">
    <div class="panel"><h2>Créneaux les plus « complet »</h2><div id="topHour" class="ranklist"></div></div>
    <div class="panel"><h2>Taux de remplissage (complet) par studio</h2><div id="cmpLieu" class="ranklist"></div></div>
  </div>
  <div class="panel"><h2>Coachs &laquo; stars &raquo; (séances complètes)</h2><div id="topCoach" class="ranklist"></div></div>
  <div class="panel"><h2>Statuts par type de cours</h2><canvas id="cCours"></canvas></div>

  <div class="panel">
    <h2>📅 Heatmap jour &times; heure <span style="font-size:11px;color:var(--muted);font-weight:400;text-transform:none;letter-spacing:0">(% complet, pondéré par nb séances observées)</span></h2>
    <p style="color:var(--muted);font-size:12.5px;margin:-4px 0 12px"><b>Taux de complétude</b> par bucket jour × heure (séances complètes / séances figées observées). Évite le biais "Mardi 10× vs Samedi 5×" : on compare des <b>taux</b>, pas des cumuls. Plus foncé = bucket plus systématiquement complet.</p>
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
    <div class="filters"><button id="btnExport" class="btn">Exporter en Excel</button></div>
    <div class="tablewrap"><table id="tbl"><thead></thead><tbody></tbody></table></div>
  </div>
  <div class="foot">Source : dnapilatesparis.com (widget Mindbody) &middot; relevés réguliers, statut figé près du début de chaque séance.</div>
</div>
<script>
const ALL=__DATA__;
const CAP=__CAP__;
const JOURS=["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"];
const STC={'complet':'#e07a6f','disponible':'#7bc98a','inconnu':'#9a9489'};
const LBL={'complet':'complet','disponible':'des places','inconnu':'inconnu'};
const nf=v=>Math.round(v).toLocaleString('fr-FR');
function fmtJ(iso){const p=iso.split('-');return `${p[2]}/${p[1]}/${p[0]}`;}
Chart.defaults.color='#9a9489';Chart.defaults.borderColor='#2a2a2a';Chart.defaults.font.family=getComputedStyle(document.body).fontFamily;
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const ACC=css('--accent2'),ACC2='#7bc98a';
let charts={};
const isNarrow=()=>matchMedia('(max-width:600px)').matches;
const DATA=ALL.filter(r=>r.finie&&(r.capacite||0)>0);
const selLieu=document.getElementById('fLieu'),selStatut=document.getElementById('fStatut');
function fillSel(sel,vals,label){sel.innerHTML='';sel.add(new Option(label,''));[...new Set(vals)].filter(Boolean).sort().forEach(v=>sel.add(new Option(LBL[v]||v,v)));}
fillSel(selLieu,DATA.map(r=>r.lieu),'Tous les studios');
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
    ? `📅 Période : du ${fmtJ(dts[0])} au ${fmtJ(dts[pj-1])} · ${pj} jour${pj>1?'s':''} · ${nLieux} studio${nLieux>1?'s':''} · ${nf(D.length)} séances figées`
    : 'Aucune séance figée sur cette sélection (l\'historique se construit au fil des relevés).';
  document.getElementById('kpis').innerHTML=[
    ['Séances figées',nf(D.length)],
    ['Studios',nf(nLieux)],
    ['Complètes',nf(nComplet)+(D.length?` (${Math.round(100*nComplet/D.length)}%)`:'')],
    ['Présents estimés',nf(totPres)],
    ['Présents / studio',nf(totPres/nLieux)],
    ['Compl. / studio',nf(nComplet/nLieux)],
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
    backgroundColor:[ACC,ACC2,'#e07a6f','#9b7ff0','#6fd0e0']}]},
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

  const byCo={};D.forEach(r=>{if(!r.coach)return;byCo[r.coach]=byCo[r.coach]||{c:0,n:0};byCo[r.coach].n++;if(r.statut==='complet')byCo[r.coach].c++;});
  const coachs=Object.entries(byCo).map(([k,o])=>[k,o.n?o.c/o.n:0,o.c,o.n]).filter(x=>x[3]>=2).sort((a,b)=>b[1]-a[1]).slice(0,10);
  const mxCo=coachs.length?coachs[0][1]:0;
  document.getElementById('topCoach').innerHTML=coachs.length?coachs.map(([k,t,c,n])=>
    `<div class="rk"><span class="lbl">${k}</span><span class="track"><span style="width:${mxCo?Math.round(100*t/mxCo):0}%"></span></span><span class="val">${Math.round(100*t)}% complet &middot; ${c}/${n}</span></div>`).join('')
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

// ============== HEATMAP / CRÉNEAUX (% complet pondéré) ==============
const TRANCHES={matin:{label:'Matin (7-12h)',hours:[7,8,9,10,11]},
  midi:{label:'Midi (12-14h)',hours:[12,13]},
  aprem:{label:'Après-midi (14-18h)',hours:[14,15,16,17]},
  soiree:{label:'Soirée (18-22h)',hours:[18,19,20,21]},
  fin:{label:'Fin soirée (22h+)',hours:[22,23]}};
let CRENEAU_A={jour:'Mardi',tranche:'aprem'};
let CRENEAU_B={jour:'Samedi',tranche:'matin'};
const fillColor=t=>t>=0.75?'#5fcf8a':t>=0.5?'#e6c14d':'#e07a6f';
function renderHeatmap(D){
  const hm=document.getElementById('heatmap');if(!hm)return;
  const hours=Array.from({length:17},(_,i)=>i+7);
  const heat={};let max=0;
  D.forEach(r=>{const h=parseInt((r.heure||'').slice(0,2));if(isNaN(h))return;
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
function computeCreneau(D,jour,tranche){
  const hours=new Set(TRANCHES[tranche].hours);
  const f=D.filter(r=>{const h=parseInt((r.heure||'').slice(0,2));return r.jour===jour&&hours.has(h);});
  const c=f.filter(r=>r.statut==='complet').length;
  const rate=f.length?c/f.length:0;
  const byCours={};f.forEach(r=>{if(!r.cours)return;byCours[r.cours]=byCours[r.cours]||{c:0,n:0};byCours[r.cours].n++;if(r.statut==='complet')byCours[r.cours].c++;});
  const topCours=Object.entries(byCours).map(([k,o])=>[k,o.n?o.c/o.n:0,o.c,o.n]).filter(x=>x[3]>=2).sort((a,b)=>b[1]-a[1]).slice(0,5);
  return {n:f.length,c,rate,topCours};
}
function renderCreneauCompare(D){
  const wrap=document.getElementById('creneauCompare');if(!wrap)return;
  const box=(side,sel)=>{const c=computeCreneau(D,sel.jour,sel.tranche);
    const color=side==='A'?'var(--accent2)':'#7bc98a';
    return `<div style="background:var(--card2);padding:14px 16px;border-radius:10px;border-left:3px solid ${color}">
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <select data-side="${side}" data-field="jour" style="flex:1;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:13px">${JOURS.map(j=>`<option value="${j}" ${j===sel.jour?'selected':''}>${j}</option>`).join('')}</select>
        <select data-side="${side}" data-field="tranche" style="flex:1;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:13px">${Object.entries(TRANCHES).map(([k,v])=>`<option value="${k}" ${k===sel.tranche?'selected':''}>${v.label}</option>`).join('')}</select>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">
        <div><div style="font-size:22px;font-weight:800;color:var(--accent2)">${nf(c.n)}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Séances observées</div></div>
        <div><div style="font-size:22px;font-weight:800;color:${fillColor(c.rate)}">${Math.round(100*c.rate)}%</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Taux complet</div></div>
        <div><div style="font-size:22px;font-weight:800;color:var(--accent2)">${nf(c.c)}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Complètes</div></div>
        <div><div style="font-size:22px;font-weight:800;color:var(--accent2)">${nf(c.n*CAP)}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Cap. cumulée</div></div>
      </div>
      <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px">Top cours (taux complet)</div>
      <div style="font-size:11.5px">${c.topCours.length?c.topCours.map(([k,t,cc,n],i)=>`<div style="padding:3px 7px;background:var(--bg);border-radius:5px;margin-bottom:3px"><span style="color:var(--muted)">${i+1}.</span> ${k.slice(0,28)} <span style="color:${fillColor(t)};font-weight:700;float:right">${Math.round(100*t)}% (${cc}/${n})</span></div>`).join(''):'<div style="color:var(--muted);font-style:italic">—</div>'}</div>
    </div>`;};
  const cA=computeCreneau(D,CRENEAU_A.jour,CRENEAU_A.tranche),cB=computeCreneau(D,CRENEAU_B.jour,CRENEAU_B.tranche);
  const dR=Math.round(100*(cA.rate-cB.rate));
  wrap.innerHTML=`${box('A',CRENEAU_A)}${box('B',CRENEAU_B)}
    <div style="grid-column:span 2;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px 16px;text-align:center;font-size:13px;color:var(--muted)">
      <b style="color:var(--accent2)">${CRENEAU_A.jour} ${TRANCHES[CRENEAU_A.tranche].label}</b> vs <b style="color:#7bc98a">${CRENEAU_B.jour} ${TRANCHES[CRENEAU_B.tranche].label}</b> ·
      Écart taux complet : <span style="color:${dR>0?'#5fcf8a':'#e07a6f'};font-weight:700">${dR>0?'+':''}${dR} pts</span>
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

const cols=[['date','Date'],['jour','Jour'],['heure','Heure'],['lieu','Studio'],['cours','Cours'],['coach','Coach'],['statut','Statut'],['presents','Présents est.']];
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
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='dna_seances.csv';document.body.appendChild(a);a.click();a.remove();
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
    print(f"OK [dna]: {len(rows)} séances en base, {len(fin)} figées. Studios: {lieux}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
