#!/usr/bin/env python3
"""Calcule les 7 insights padel IDF demandés et exporte → padel_insights_data.json.

Sources :
  - padel_idf_data.json (live, 143 clubs)
  - padel_idf_history.json.gz (146k bookings 2024-2026)
  - padel_idf_clubs.json / doinsport_idf_clubs.json / playtomic_idf_clubs.json
  - padel_club_unified.json (19 clusters cross-plateforme)
"""
import datetime as dt
import gzip
import json
import re
from collections import defaultdict, Counter

# ============= Charger toutes les sources =============
live = json.load(open("padel_idf_data.json"))
with gzip.open("padel_idf_history.json.gz", "rt") as f:
    history = json.load(f)
unified = json.load(open("padel_club_unified.json"))

JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def get_source(slug, meta):
    return meta.get("source") or (
        "urbanpadel" if slug.startswith("urbanpadel-") else
        "doinsport" if slug.startswith("doinsport-") else
        "playtomic" if slug.startswith("playtomic-") else "anybuddy")


# Détection enseignes (15 chaînes)
CHAIN_RULES = [
    ("4PADEL", re.compile(r"\b4[- ]?padel\b", re.I)),
    ("Casa Padel", re.compile(r"casa[- ]padel", re.I)),
    ("Sportfield", re.compile(r"sportfield", re.I)),
    ("Sanctuary", re.compile(r"sanctuary", re.I)),
    ("Forest Hill", re.compile(r"forest[- ]hill", re.I)),
    ("PadelShot", re.compile(r"padel[- ]?shot", re.I)),
    ("UrbanPadel", re.compile(r"urban[- ]?padel|urbansoccer", re.I)),
    ("UCPA", re.compile(r"ucpa", re.I)),
    ("Aldea", re.compile(r"aldea", re.I)),
    ("Trinquet", re.compile(r"trinquet", re.I)),
    ("Le Five", re.compile(r"le[- ]five|five[- ]arena", re.I)),
    ("Carre Padel", re.compile(r"carre[- ]padel", re.I)),
    ("Play Padel", re.compile(r"play[- ]padel", re.I)),
    ("Big Five", re.compile(r"big[- ]five", re.I)),
    ("Padel Camp", re.compile(r"padel[- ]camp", re.I)),
]


def detect_chain(name, slug):
    s = f"{name} {slug}"
    for k, r in CHAIN_RULES:
        if r.search(s):
            return k
    return None


# ============= #1 HEATMAP HORAIRE jour × heure =============
# Sources : on combine bookings historiques Doinsport + live (réservés + dispos = volume offert)
heat_demand = defaultdict(int)   # (jour, heure) → nb créneaux
heat_offered = defaultdict(int)
for store in (live, history):
    for slug, b in store.items():
        for s in (b.get("sessions") or {}).values():
            j = s.get("jour")
            h = s.get("heure", "")
            if not j or not h or len(h) < 2:
                continue
            try:
                heure = int(h[:2])
            except ValueError:
                continue
            key = (j, heure)
            heat_offered[key] += 1
            statut = s.get("statut")
            # Compte demande = réservés (Doinsport history) ou statut reserve (live)
            if statut == "reserve" or s.get("source") == "doinsport_history":
                heat_demand[key] += 1

heatmap = {}
for j in JOURS:
    heatmap[j] = []
    for h in range(7, 24):
        offered = heat_offered.get((j, h), 0)
        demand = heat_demand.get((j, h), 0)
        rate = round(100 * demand / offered) if offered else 0
        heatmap[j].append({"heure": h, "offered": offered, "demand": demand, "occupation_pct": rate})

# Top 20 créneaux les plus demandés (par volume)
flat = [(j, h, heat_demand[(j, h)]) for j in JOURS for h in range(7, 24)]
top_creneaux = sorted(flat, key=lambda x: -x[2])[:20]

# ============= #3 ÉLASTICITÉ PRIX (préliminaire) =============
# Pour chaque club : prix médian 90min vs volume bookings (proxy de demande)
import statistics

elast = []
for slug, b in {**live, **history}.items():
    sess = b.get("sessions") or {}
    prices_90 = [s["prix"] for s in sess.values() if s.get("prix") and s.get("duree") == 90]
    meta = b.get("meta") or {}
    src = get_source(slug, meta)
    if len(prices_90) < 5:
        continue
    elast.append({
        "slug": slug,
        "name": meta.get("name") or slug,
        "source": src,
        "prix_median_90": round(statistics.median(prices_90), 1),
        "n_bookings": len(sess),
        "cp": meta.get("cp", ""),
    })
# Régression simple log-log
import math
log_pairs = [(math.log(e["prix_median_90"]), math.log(e["n_bookings"]))
             for e in elast if e["prix_median_90"] > 0 and e["n_bookings"] > 0]
if len(log_pairs) > 5:
    n = len(log_pairs)
    sx = sum(p[0] for p in log_pairs)
    sy = sum(p[1] for p in log_pairs)
    sxy = sum(p[0] * p[1] for p in log_pairs)
    sx2 = sum(p[0] ** 2 for p in log_pairs)
    slope = (n * sxy - sx * sy) / (n * sx2 - sx ** 2) if (n * sx2 - sx ** 2) else 0
    intercept = (sy - slope * sx) / n
else:
    slope = intercept = 0
elasticity = {"slope": round(slope, 3), "intercept": round(intercept, 3),
              "interpretation": "élasticité prix demande estimée (log-log)",
              "data": elast,
              "warning": "Préliminaire : 1 seul point dans le temps, le club avec le plus de bookings est aussi celui le plus ancien sur la plateforme → confound. À surveiller sur 2026 quand on aura ≥6 mois de suivi pour tous les clubs."}

# ============= #4 DÉSERTS PADEL =============
# Per commune : croiser pop + nb clubs dans rayon 5km
# On utilise les CP comme proxy commune (simple). Pop INSEE déjà chargée.
insee = {
    '75': {'nom':'Paris','population':2102650},
    '77': {'nom':'Seine-et-Marne','population':1452393},
    '78': {'nom':'Yvelines','population':1448625},
    '91': {'nom':'Essonne','population':1316939},
    '92': {'nom':'Hauts-de-Seine','population':1614642},
    '93': {'nom':'Seine-Saint-Denis','population':1657411},
    '94': {'nom':'Val-de-Marne','population':1410055},
    '95': {'nom':"Val-d'Oise",'population':1255945},
}
# CP → coords moyennes des clubs sur ce CP, + nb clubs
cp_data = defaultdict(lambda: {"clubs": 0, "lat_sum": 0, "lng_sum": 0, "names": []})
for slug, b in live.items():
    meta = b.get("meta") or {}
    cp = meta.get("cp", "")
    if not cp or not meta.get("lat"):
        continue
    cp_data[cp]["clubs"] += 1
    cp_data[cp]["lat_sum"] += meta["lat"]
    cp_data[cp]["lng_sum"] += meta["lng"]
    cp_data[cp]["names"].append(meta.get("name") or slug)

cp_summary = {}
for cp, d in cp_data.items():
    cp_summary[cp] = {
        "n_clubs": d["clubs"],
        "lat": round(d["lat_sum"] / d["clubs"], 5),
        "lng": round(d["lng_sum"] / d["clubs"], 5),
        "names": d["names"][:3],
    }

# Identification des "déserts" par dépt : CP IDF non encore détectés (besoin liste exhaustive)
# Pour l'instant on calcule par dépt : densité clubs / 100k hab
dept_density = {}
for code, d in insee.items():
    n_clubs = sum(1 for slug,b in live.items() if ((b.get("meta") or {}).get("cp","")[:2]) == code)
    dept_density[code] = {
        "nom": d["nom"], "pop": d["population"],
        "n_clubs": n_clubs,
        "clubs_per_100k": round(100000 * n_clubs / d["population"], 2),
        "hab_per_club": round(d["population"] / n_clubs) if n_clubs else None,
    }
# Top zones sous-équipées : départements avec hab/club > médiane
median_hab_club = statistics.median(v["hab_per_club"] for v in dept_density.values() if v["hab_per_club"])
deserts_dept = sorted([(k, v) for k, v in dept_density.items() if v["hab_per_club"] and v["hab_per_club"] > median_hab_club],
                     key=lambda x: -x[1]["hab_per_club"])

# ============= #5 ROCKET CLUBS (croissance) =============
# Pour clubs avec historique : compare volume 6 derniers mois vs 6 mois précédents
import datetime as DT
today = DT.date.today()
m6 = today.replace(day=1) - DT.timedelta(days=180)  # ~6 mois en arrière
m12 = today.replace(day=1) - DT.timedelta(days=365)

club_growth = []
for slug, b in history.items():
    sess = list((b.get("sessions") or {}).values())
    if not sess:
        continue
    meta = b.get("meta") or {}
    n_recent = sum(1 for s in sess if s.get("date", "") >= m6.isoformat())
    n_prev = sum(1 for s in sess if m12.isoformat() <= s.get("date", "") < m6.isoformat())
    n_total = len(sess)
    growth_pct = round(100 * (n_recent - n_prev) / n_prev, 1) if n_prev > 5 else None
    # Premier booking observé
    dates = sorted({s.get("date") for s in sess if s.get("date")})
    first = dates[0] if dates else None
    last = dates[-1] if dates else None
    club_growth.append({
        "slug": slug, "name": meta.get("name"), "cp": meta.get("cp"),
        "first_seen": first, "last_seen": last,
        "n_total": n_total, "n_recent_6m": n_recent, "n_prev_6m": n_prev,
        "growth_pct": growth_pct,
        "is_new_club": first and first >= m12.isoformat(),
    })

rocket_clubs = sorted([c for c in club_growth if c["growth_pct"] is not None],
                     key=lambda x: -x["growth_pct"])[:15]
stagnant_clubs = sorted([c for c in club_growth if c["growth_pct"] is not None],
                       key=lambda x: x["growth_pct"])[:10]
new_clubs = [c for c in club_growth if c["is_new_club"]]

# ============= #6 CHAÎNES vs INDÉPENDANTS =============
chain_clubs = defaultdict(list)
indep_clubs = []
for slug, b in live.items():
    meta = b.get("meta") or {}
    name = meta.get("name") or slug
    chain = detect_chain(name, slug)
    sess = list((b.get("sessions") or {}).values())
    n_sess = len(sess)
    prices = [s["prix"] for s in sess if s.get("prix") and s.get("duree") == 90]
    median_price = statistics.median(prices) if prices else None
    entry = {"slug": slug, "name": name, "n_sess": n_sess, "median_price_90": median_price}
    if chain:
        chain_clubs[chain].append(entry)
    else:
        indep_clubs.append(entry)

chain_summary = []
for chain, clubs in sorted(chain_clubs.items(), key=lambda x: -len(x[1])):
    if not clubs:
        continue
    prices = [c["median_price_90"] for c in clubs if c["median_price_90"]]
    chain_summary.append({
        "chain": chain,
        "n_clubs": len(clubs),
        "median_sessions_per_club": statistics.median(c["n_sess"] for c in clubs),
        "median_price_90": round(statistics.median(prices), 1) if prices else None,
    })
# Indé
indep_prices = [c["median_price_90"] for c in indep_clubs if c["median_price_90"]]
indep_summary = {
    "label": "Indépendants",
    "n_clubs": len(indep_clubs),
    "median_sessions_per_club": statistics.median(c["n_sess"] for c in indep_clubs) if indep_clubs else 0,
    "median_price_90": round(statistics.median(indep_prices), 1) if indep_prices else None,
}

# ============= #7 VACANCES SCOLAIRES (zone C) =============
# Vacances scolaires officielles Zone C 2024-2026 (Paris/Versailles)
VAC_C = [
    ("Toussaint 2024", "2024-10-19", "2024-11-04"),
    ("Noël 2024", "2024-12-21", "2025-01-06"),
    ("Hiver 2025", "2025-02-22", "2025-03-10"),
    ("Pâques 2025", "2025-04-19", "2025-05-05"),
    ("Été 2025", "2025-07-05", "2025-09-01"),
    ("Toussaint 2025", "2025-10-18", "2025-11-03"),
    ("Noël 2025", "2025-12-20", "2026-01-05"),
    ("Hiver 2026", "2026-02-07", "2026-02-23"),
    ("Printemps 2026", "2026-04-04", "2026-04-20"),
]

def is_vacances(date_str):
    for nom, start, end in VAC_C:
        if start <= date_str <= end:
            return nom
    return None

# Per-dept : volume bookings vacances vs non-vacances (depuis history Doinsport)
vac_impact_per_dept = defaultdict(lambda: {"vac_bookings": 0, "non_vac_bookings": 0,
                                            "vac_days": set(), "non_vac_days": set()})
for slug, b in history.items():
    meta = b.get("meta") or {}
    cp = meta.get("cp", "")
    if not cp or len(cp) != 5:
        continue
    dept = cp[:2]
    for s in (b.get("sessions") or {}).values():
        d = s.get("date")
        if not d:
            continue
        if is_vacances(d):
            vac_impact_per_dept[dept]["vac_bookings"] += 1
            vac_impact_per_dept[dept]["vac_days"].add(d)
        else:
            vac_impact_per_dept[dept]["non_vac_bookings"] += 1
            vac_impact_per_dept[dept]["non_vac_days"].add(d)

vac_output = []
for dept in sorted(vac_impact_per_dept.keys()):
    d = vac_impact_per_dept[dept]
    vac_avg = d["vac_bookings"] / len(d["vac_days"]) if d["vac_days"] else 0
    non_vac_avg = d["non_vac_bookings"] / len(d["non_vac_days"]) if d["non_vac_days"] else 0
    delta_pct = round(100 * (vac_avg - non_vac_avg) / non_vac_avg, 1) if non_vac_avg else 0
    vac_output.append({
        "dept": dept,
        "nom": insee.get(dept, {}).get("nom", "?"),
        "vac_bookings_per_day": round(vac_avg, 1),
        "non_vac_bookings_per_day": round(non_vac_avg, 1),
        "delta_pct": delta_pct,
    })

# ============= #8 MULTI-PLATEFORME vs MONO =============
# Pour les 19 clusters : volume moyen vs clubs mono-plateforme
cluster_volumes = []
for cl in unified:
    members = cl["members"]
    total = 0
    for m in members:
        b = live.get(m["slug"])
        if b:
            total += len(b.get("sessions") or {})
    cluster_volumes.append({
        "unified_id": cl["unified_id"],
        "canonical_name": cl["canonical_name"],
        "cp": cl["cp"],
        "n_platforms": len(cl["sources"]),
        "total_sessions": total,
    })

# Clubs mono = pas dans aucun cluster
cluster_slugs = set()
for cl in unified:
    for m in cl["members"]:
        cluster_slugs.add(m["slug"])

mono_volumes = []
for slug, b in live.items():
    if slug in cluster_slugs:
        continue
    mono_volumes.append({
        "slug": slug,
        "name": (b.get("meta") or {}).get("name") or slug,
        "total_sessions": len(b.get("sessions") or {}),
    })

multi_vs_mono = {
    "n_multi_platforms": len(cluster_volumes),
    "n_mono": len(mono_volumes),
    "median_sessions_multi": int(statistics.median(c["total_sessions"] for c in cluster_volumes)) if cluster_volumes else 0,
    "median_sessions_mono": int(statistics.median(c["total_sessions"] for c in mono_volumes)) if mono_volumes else 0,
    "avg_sessions_multi": int(statistics.mean(c["total_sessions"] for c in cluster_volumes)) if cluster_volumes else 0,
    "avg_sessions_mono": int(statistics.mean(c["total_sessions"] for c in mono_volumes)) if mono_volumes else 0,
    "clusters": sorted(cluster_volumes, key=lambda x: -x["total_sessions"]),
}

# ============= EXPORT =============
out = {
    "generated_at": dt.datetime.now().isoformat(),
    "heatmap": {"days": JOURS, "data": heatmap, "top_creneaux": [(j, h, v) for j, h, v in top_creneaux]},
    "elasticity": elasticity,
    "deserts": {"dept_density": dept_density, "deserts": [dict(code=k, **v) for k, v in deserts_dept], "cp_clubs": cp_summary},
    "rocket": {"new_clubs": new_clubs, "rocket": rocket_clubs, "stagnant": stagnant_clubs},
    "chains_vs_indep": {"chains": chain_summary, "indep": indep_summary},
    "vacances": vac_output,
    "multi_vs_mono": multi_vs_mono,
}
json.dump(out, open("padel_insights_data.json", "w"), indent=1, ensure_ascii=False, default=str)
print("✅ padel_insights_data.json généré")
print(f"   - heatmap : {sum(1 for j in heatmap for h in heatmap[j] if h['offered']>0)} cases avec data")
print(f"   - élasticité : {len(elast)} clubs analysés, slope log-log = {elasticity['slope']}")
print(f"   - déserts : {len(deserts_dept)} départements sous-équipés (> médiane {median_hab_club:.0f} hab/club)")
print(f"   - rocket : {len(rocket_clubs)} clubs en croissance, {len(stagnant_clubs)} stagnants, {len(new_clubs)} nouveaux")
print(f"   - chaînes : {len(chain_summary)} enseignes + {len(indep_clubs)} indépendants")
print(f"   - vacances : {len(vac_output)} dépts analysés")
print(f"   - multi-plateformes : {len(cluster_volumes)} clusters vs {len(mono_volumes)} mono")
