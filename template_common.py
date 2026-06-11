"""Blocs HTML/CSS/JS communs aux dashboards de scrapers fitness.

Centralise les fragments dupliqués dans bsport/studio/reset/banote/dna/le33foch/
burningbar/senseclub/barrys/santroch/episod/snakeandtwist_scrape.py :

- HEAD_COMMON          : balises <meta>, slot Chart.js inline.
- CSS_COMMON           : bloc <style> commun (palette via vars CSS).
- HEATMAP_BLOCK_*      : HTML + JS heatmap pondérée + comparateur + top 20
                         buckets. 3 variantes selon la sémantique "complet" :
   * HEATMAP_BLOCK_STATUT     : data Mindbody (r.statut === 'complet').
   * HEATMAP_BLOCK_PRESENTS   : data bsport/Mariana (r.presents / r.capacite).
   * HEATMAP_BLOCK_OCCUPATION : data anybuddy (r.statut === 'reserve').
- EXPORT_CSV_BLOCK     : JS du bouton "Exporter en Excel".

Usage : voir banote_scrape.py — concat + .replace() de placeholders.
Les variables CSS (__ACCENT__, __ACCENT2__, palette --green/--red...) sont
résolues par le scraper appelant via .replace() avant écriture du HTML.
"""

# ---------------------------------------------------------------------------
# HEAD : meta + slot Chart.js (le scraper injecte vendor_chartjs.min.js).
# ---------------------------------------------------------------------------
HEAD_COMMON = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>__CHARTJS__</script>"""


# ---------------------------------------------------------------------------
# CSS : palette + layout. Les couleurs d'accent sont paramétrées via les
# placeholders __ACCENT__ et __ACCENT2__ (string .replace() côté scraper).
# Les valeurs --bg/--card/--text peuvent être surchargées par le scraper
# en ajoutant son propre <style> APRES ce bloc commun (cascade CSS).
# ---------------------------------------------------------------------------
CSS_COMMON = r"""<style>
  :root{--bg:#0d0b07;--card:#181410;--card2:#221c14;--line:#352c1d;
        --text:#f5efe2;--muted:#b9ac8c;--accent:__ACCENT__;--accent2:__ACCENT2__;
        --green:#7bc98a;--yellow:#e6c14d;--red:#e07a6f;}
  *{box-sizing:border-box;}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);}
  header{padding:28px 32px 12px;} h1{margin:0;font-size:26px;font-weight:800;letter-spacing:3px;color:var(--accent2);}
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
  th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--card);}
  tbody tr:hover{background:var(--card2);}
  .pill{padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;}
  .ranklist{display:flex;flex-direction:column;gap:9px;}
  .rk{display:grid;grid-template-columns:170px 1fr auto;align-items:center;gap:10px;font-size:13px;}
  .rk .lbl{font-weight:600;overflow:hidden;text-overflow:ellipsis;} .rk .track{height:9px;background:var(--line);border-radius:5px;overflow:hidden;}
  .rk .track>span{display:block;height:100%;background:var(--accent);border-radius:5px;} .rk .val{color:var(--muted);}
  .btn{background:var(--accent);color:#0d0b07;border:none;border-radius:9px;padding:9px 16px;font-size:13px;font-weight:700;cursor:pointer;}
  .tablewrap{max-height:600px;overflow:auto;border:1px solid var(--line);border-radius:14px;}
  .foot{color:var(--muted);font-size:12px;margin-top:18px;}
  @media(max-width:600px){header{padding:18px 14px 6px;}h1{font-size:20px;}.wrap{padding:0 12px 32px;}.kpis{grid-template-columns:1fr 1fr;gap:10px;}.kpi .v{font-size:21px;}.filters input,.filters select,.btn{font-size:15px;width:100%;}.rk{grid-template-columns:120px 1fr auto;}}
  @media(max-width:600px){canvas{max-height:200px!important}th,td{padding:7px 6px;font-size:12px}.panel{padding:15px 14px}.kpi .v{font-size:20px}.note{font-size:12px}.ctrl{font-size:12px;gap:7px}.pinp{width:64px}}
</style>"""


# ---------------------------------------------------------------------------
# HEATMAP — HTML (les 3 panels : heatmap + comparateur + top buckets).
# Le scraper insère ce bloc dans <div class="wrap">. Le contenu HTML est
# identique pour les 3 variantes ; seule la fonction JS associée diffère.
# ---------------------------------------------------------------------------
_HEATMAP_PANELS_HTML = r"""
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
"""


# JS commun aux 3 variantes : TRANCHES, fillColor, renderCreneauCompare,
# renderTopBuckets. La seule chose qui change est `_isComplet(r)` : la
# fonction qui décide si une séance compte comme "complète".
def _heatmap_js(is_complet_expr, cap_expr):
    """Construit le bloc JS heatmap+comparateur+top buckets.

    is_complet_expr : expression JS bool sur `r` (ex. "r.statut==='complet'").
    cap_expr        : expression JS pour la capacité unitaire affichée dans
                      le KPI "cap. cumulée" du comparateur (ex. "CAP" ou
                      "r.capacite", peut être "1" si non pertinent).
    """
    return (r"""
// ============== HEATMAP / CRÉNEAUX (% complet pondéré) ==============
const TRANCHES={matin:{label:'Matin (7-12h)',hours:[7,8,9,10,11]},
  midi:{label:'Midi (12-14h)',hours:[12,13]},
  aprem:{label:'Après-midi (14-18h)',hours:[14,15,16,17]},
  soiree:{label:'Soirée (18-22h)',hours:[18,19,20,21]},
  fin:{label:'Fin soirée (22h+)',hours:[22,23]}};
let CRENEAU_A={jour:'Mardi',tranche:'aprem'};
let CRENEAU_B={jour:'Samedi',tranche:'matin'};
const fillColor=t=>t>=0.75?'#5fcf8a':t>=0.5?'#e6c14d':'#e07a6f';
const _isComplet=r=>(""" + is_complet_expr + r""");
function renderHeatmap(D){
  const hm=document.getElementById('heatmap');if(!hm)return;
  const hours=Array.from({length:17},(_,i)=>i+7);
  const heat={};let max=0;
  D.forEach(r=>{const h=parseInt((r.heure||'').slice(0,2));if(isNaN(h))return;
    const k=r.jour+'|'+h;heat[k]=heat[k]||{c:0,n:0};
    heat[k].n++;if(_isComplet(r))heat[k].c++;});
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
  const c=f.filter(r=>_isComplet(r)).length;
  const rate=f.length?c/f.length:0;
  const byCours={};f.forEach(r=>{if(!r.cours)return;byCours[r.cours]=byCours[r.cours]||{c:0,n:0};byCours[r.cours].n++;if(_isComplet(r))byCours[r.cours].c++;});
  const topCours=Object.entries(byCours).map(([k,o])=>[k,o.n?o.c/o.n:0,o.c,o.n]).filter(x=>x[3]>=2).sort((a,b)=>b[1]-a[1]).slice(0,5);
  return {n:f.length,c,rate,topCours};
}
function renderCreneauCompare(D){
  const wrap=document.getElementById('creneauCompare');if(!wrap)return;
  const box=(side,sel)=>{const c=computeCreneau(D,sel.jour,sel.tranche);
    const color=side==='A'?'var(--accent)':'var(--accent2)';
    return `<div style="background:var(--card2);padding:14px 16px;border-radius:10px;border-left:3px solid ${color}">
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <select data-side="${side}" data-field="jour" style="flex:1;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:13px">${JOURS.map(j=>`<option value="${j}" ${j===sel.jour?'selected':''}>${j}</option>`).join('')}</select>
        <select data-side="${side}" data-field="tranche" style="flex:1;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:13px">${Object.entries(TRANCHES).map(([k,v])=>`<option value="${k}" ${k===sel.tranche?'selected':''}>${v.label}</option>`).join('')}</select>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">
        <div><div style="font-size:22px;font-weight:800;color:var(--accent2)">${nf(c.n)}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Séances observées</div></div>
        <div><div style="font-size:22px;font-weight:800;color:${fillColor(c.rate)}">${Math.round(100*c.rate)}%</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Taux complet</div></div>
        <div><div style="font-size:22px;font-weight:800;color:var(--accent2)">${nf(c.c)}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Complètes</div></div>
        <div><div style="font-size:22px;font-weight:800;color:var(--accent2)">${nf(c.n*(""" + cap_expr + r"""))}</div><div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px">Cap. cumulée</div></div>
      </div>
      <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px">Top cours (taux complet)</div>
      <div style="font-size:11.5px">${c.topCours.length?c.topCours.map(([k,t,cc,n],i)=>`<div style="padding:3px 7px;background:var(--bg);border-radius:5px;margin-bottom:3px"><span style="color:var(--muted)">${i+1}.</span> ${k.slice(0,28)} <span style="color:${fillColor(t)};font-weight:700;float:right">${Math.round(100*t)}% (${cc}/${n})</span></div>`).join(''):'<div style="color:var(--muted);font-style:italic">—</div>'}</div>
    </div>`;};
  const cA=computeCreneau(D,CRENEAU_A.jour,CRENEAU_A.tranche),cB=computeCreneau(D,CRENEAU_B.jour,CRENEAU_B.tranche);
  const dR=Math.round(100*(cA.rate-cB.rate));
  wrap.innerHTML=`${box('A',CRENEAU_A)}${box('B',CRENEAU_B)}
    <div style="grid-column:span 2;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px 16px;text-align:center;font-size:13px;color:var(--muted)">
      <b style="color:var(--accent)">${CRENEAU_A.jour} ${TRANCHES[CRENEAU_A.tranche].label}</b> vs <b style="color:var(--accent2)">${CRENEAU_B.jour} ${TRANCHES[CRENEAU_B.tranche].label}</b> ·
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
    const c=f.filter(r=>_isComplet(r)).length;
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
""")


# Variante STATUT (Mindbody : banote, dna, le33foch, burningbar, senseclub).
# Le scraper doit avoir défini la const JS `CAP` avant d'inclure ce bloc.
HEATMAP_PANELS_HTML = _HEATMAP_PANELS_HTML
HEATMAP_BLOCK_STATUT = _heatmap_js("r.statut==='complet'", "CAP")

# Variante PRESENTS (bsport/Mariana/resamania : presents/capacite chiffrés).
# "Complet" = presents >= capacite. `cap` côté JS prend la capacité de la
# séance elle-même (r.capacite) pour le KPI cumulé.
HEATMAP_BLOCK_PRESENTS = _heatmap_js(
    "(r.capacite||0)>0 && (r.presents||0)>=(r.capacite||0)",
    "r.capacite||0",
)

# Variante OCCUPATION (anybuddy : terrain reservé = "complet").
HEATMAP_BLOCK_OCCUPATION = _heatmap_js("r.statut==='reserve'", "1")


# ---------------------------------------------------------------------------
# EXPORT CSV — bouton "Exporter en Excel". Le scraper doit définir `cols`
# (array de [field, label]) et `currentRows` (array filtré actuel) avant.
# Placeholder __FILENAME__ : nom du .csv téléchargé (ex. "banote_seances.csv").
# ---------------------------------------------------------------------------
EXPORT_CSV_BLOCK = r"""document.getElementById('btnExport').addEventListener('click',()=>{
  const esc=v=>{v=(''+v).replace(/"/g,'""');return /[";\n]/.test(v)?`"${v}"`:v;};
  const lines=[cols.map(c=>c[1]).join(';')];
  currentRows.forEach(r=>lines.push(cols.map(c=>esc(c[0]==='date'?fmtJ(r.date):(r[c[0]]==null?'':r[c[0]]))).join(';')));
  const blob=new Blob(['﻿'+lines.join('\r\n')],{type:'text/csv;charset=utf-8;'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='__FILENAME__';document.body.appendChild(a);a.click();a.remove();
});"""


# ---------------------------------------------------------------------------
# META PANEL — Méthode / Risque / État scraping (date dernier scrape + nb rows
# + freshness calculée côté client). À insérer juste après le bloc <header>.
# Placeholders : __META_METHOD__, __META_RISK__, __META_FREQ__,
#                __META_LAST_ISO__, __META_ROWS__.
# Le scraper appelle meta_panel_html(method, risk, freq, last_iso, n_rows).
# ---------------------------------------------------------------------------
META_PANEL_HTML = r"""<div class="meta-panel" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin:18px 0 4px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 18px">
  <div>
    <div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">⚙️ Méthode</div>
    <div style="font-size:13px;color:var(--text);line-height:1.4">__META_METHOD__</div>
  </div>
  <div>
    <div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">⚠️ Limites &middot; risques</div>
    <div style="font-size:13px;color:var(--text);line-height:1.4">__META_RISK__</div>
  </div>
  <div>
    <div style="font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">📡 État du scrap</div>
    <div style="font-size:13px;color:var(--text);line-height:1.4">
      <span id="meta-freshness" style="font-weight:700">…</span> &middot; <b>__META_ROWS__</b> entrées
      <div style="font-size:11.5px;color:var(--muted);margin-top:2px">Fréquence : __META_FREQ__</div>
    </div>
  </div>
</div>
<script>
(function(){
  const last = "__META_LAST_ISO__";
  if(!last) return;
  const el = document.getElementById('meta-freshness'); if(!el) return;
  const ago = (Date.now() - new Date(last).getTime()) / 36e5;
  let label, color;
  if (ago < 1) { label = Math.round(ago*60)+' min'; color = '#5fcf8a'; }
  else if (ago < 24) { label = ago.toFixed(1)+' h'; color = ago<6?'#5fcf8a':'#e6c14d'; }
  else { label = Math.round(ago/24)+' j'; color = '#e07a6f'; }
  el.textContent = 'Dernier scrap il y a ' + label;
  el.style.color = color;
})();
</script>"""


def meta_panel_html(method, risk, freq, last_iso, n_rows):
    """Rendu HTML du panneau META. last_iso au format ISO 8601."""
    return (META_PANEL_HTML
            .replace("__META_METHOD__", method)
            .replace("__META_RISK__", risk)
            .replace("__META_FREQ__", freq)
            .replace("__META_LAST_ISO__", last_iso or "")
            .replace("__META_ROWS__", str(n_rows)))


# ---------------------------------------------------------------------------
# PRICE LOADER — fetch brand_prices.json (ou Supabase) au chargement et set
# l'input #prix à la valeur officielle (drop_in). Garde le localStorage user
# comme override si présent.
#
# Placeholders : __BRAND_KEY__ (clé marque, ex. "barrys").
# À insérer juste APRÈS le bloc qui définit l'input #prix dans le HTML.
# ---------------------------------------------------------------------------
PRICE_LOADER_BLOCK = r"""<script>
// Prix READ-ONLY depuis brand_prices.json (source unique = prix.html).
// Pas de modification locale possible : pour changer un prix, passer par
// la grille de prix (prix.html) puis re-générer brand_prices.json côté
// scraper (prices_scrape.py).
(async function(){
  const BK = "__BRAND_KEY__";
  const input = document.getElementById("prix");
  if (!input) return;

  // Verrouille l'input
  input.readOnly = true;
  input.style.opacity = '0.85';
  input.style.cursor = 'not-allowed';
  input.title = 'Prix figé sur la grille — modifier dans prix.html';

  // Badge "prix officiel scrapé" + lien vers la grille
  const badge = document.createElement('span');
  badge.id = 'prix-badge';
  badge.style.cssText = 'margin-left:8px;font-size:11px;padding:2px 8px;border-radius:12px;font-weight:600';
  input.parentNode.insertBefore(badge, input.nextSibling);

  const link = document.createElement('a');
  link.href = 'prix.html';
  link.target = '_blank';
  link.textContent = '↗ éditer dans prix.html';
  link.style.cssText = 'margin-left:8px;font-size:11px;color:var(--accent2);text-decoration:none;font-weight:600';
  input.parentNode.insertBefore(link, badge.nextSibling);

  // Récupère le prix officiel depuis brand_prices.json
  let official = null;
  try {
    const r = await fetch("brand_prices.json", {cache:"no-store"});
    const d = await r.json();
    const b = d[BK];
    if (b) official = b.drop_in || (b.packs && b.packs[0] && b.packs[0].prix_unitaire) || null;
  } catch(e) {}

  if (official != null) {
    input.value = official;
    badge.textContent = `✓ prix officiel scrapé (${official}€)`;
    badge.style.background = 'rgba(95,207,138,0.18)';
    badge.style.color = '#5fcf8a';
  } else {
    badge.textContent = '? prix non trouvé dans brand_prices.json';
    badge.style.background = 'rgba(160,160,160,0.15)';
    badge.style.color = '#999';
  }
  // Déclenche le render pour mettre à jour le CA (les scrapers écoutent 'input' sur prix)
  input.dispatchEvent(new Event('input', {bubbles:true}));
  document.dispatchEvent(new Event('priceloaded'));
})();
</script>"""


def price_loader_html(brand_key):
    return PRICE_LOADER_BLOCK.replace("__BRAND_KEY__", brand_key)
