#!/usr/bin/env python3
"""Sanity checks sur les stores : détecte les anomalies (overbook, capacité
aberrante, last_update vieux, volume effondré).

Sortie :
- sanity_report.json (statut par marque + détails des anomalies)
- console
- exit code 1 si au moins une anomalie GRAVE détectée (utilisable pour
  faire échouer un workflow et déclencher l'alerte healthcheck).

Comparaison de volume vs un baseline persistant (sanity_baseline.json) :
si le nb d'entrées chute de >50% vs le baseline, on alerte. Le baseline est
remis à jour APRÈS chaque run sain (jamais à la baisse via safestore).
"""
import datetime as dt
import glob
import json
import os
import sys

import safestore

BASELINE = "sanity_baseline.json"
REPORT = "sanity_report.json"
DROP_THRESHOLD = 0.5      # alerte si volume < 50 % du baseline
CAP_MAX = 500             # capacité par séance plausible (au-delà = bug)
STALE_HOURS_FAST = 16     # live-status / live-senseclub : tolère la nuit (studios fermés) + fenêtre de verrouillage
STALE_HOURS_SLOW = 30     # bsport / daily peuvent attendre plus

# rythme de MAJ attendu par marque (en heures)
FAST = {"barrys", "episod", "anybuddy", "banote", "dna", "le33foch", "senseclub",
        "burningbar", "santroch"}


def _check_padel(name, d):
    """Sanity checks dédiés aux stores padel.
    Structure typique : {slug: {meta:{nom,lat,lng,cp,ville,...}, sessions:[...]}}
    pour padel_idf / padel_national. Variantes pour history/insights.
    """
    issues = []
    n = len(d)

    if name in ("padel_idf", "padel_national"):
        no_geo = 0
        no_cp = 0
        bad_price = 0
        bad_slot = 0
        # CP attendus selon le scope
        cp_prefixes_idf = {"75", "77", "78", "91", "92", "93", "94", "95"}
        out_of_scope = 0

        for slug, club in d.items():
            if not isinstance(club, dict):
                continue
            meta = club.get("meta") or club.get("club") or {}
            sessions = club.get("sessions") or club.get("slots") or []
            lat, lng = meta.get("lat"), meta.get("lng")
            if not (isinstance(lat, (int, float)) and isinstance(lng, (int, float))):
                no_geo += 1
            cp = str(meta.get("cp") or meta.get("postal_code") or "")
            if not cp:
                no_cp += 1
            elif name == "padel_idf" and cp[:2] not in cp_prefixes_idf:
                out_of_scope += 1
            # Sessions : prix aberrants + créneaux < 30min ou > 180min
            for s in sessions if isinstance(sessions, list) else []:
                if not isinstance(s, dict):
                    continue
                p = s.get("prix") or s.get("price")
                if isinstance(p, (int, float)) and (p < 0 or p > 200):
                    bad_price += 1
                d_min = s.get("duree") or s.get("duration_min") or s.get("duree_min")
                if isinstance(d_min, (int, float)) and (d_min < 30 or d_min > 240):
                    bad_slot += 1

        if no_geo:
            issues.append(f"{no_geo} clubs sans coordonnées GPS")
        if no_cp:
            issues.append(f"{no_cp} clubs sans code postal")
        if out_of_scope:
            issues.append(f"{out_of_scope} clubs hors IDF (CP non 75/77/78/91/92/93/94/95)")
        if bad_price:
            issues.append(f"{bad_price} créneaux avec prix aberrant (<0 ou >200€)")
        if bad_slot:
            issues.append(f"{bad_slot} créneaux avec durée aberrante (<30min ou >240min)")

    elif name == "brand_prices":
        # liste de prix par marque, on regarde juste qu'il y en a et qu'ils sont sains
        if isinstance(d, dict):
            for brand, price in d.items():
                if isinstance(price, (int, float)) and (price < 5 or price > 100):
                    issues.append(f"prix marque {brand} aberrant : {price}€")

    status = "OK" if not issues else "WARN"
    return {"brand": name, "status": status, "count": n, "issues": issues,
            "note": "structure padel"}


def check(path):
    name = os.path.basename(path).replace("_data.json", "")
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"brand": name, "status": "ERROR", "issues": [f"lecture impossible : {e}"]}
    if not isinstance(d, dict):
        return {"brand": name, "status": "ERROR", "issues": ["format inattendu (pas un dict)"]}
    # Les stores padel ont une structure {slug:{meta,sessions}} incompatible avec les
    # stores fitness {id:{date,presents,capacite,...}}. On les check séparément, pas ici.
    PADEL_STORES = {"padel_idf", "padel_idf_history", "padel_national", "padel_insights",
                    "padel_etude_kpis", "brand_prices"}
    if name in PADEL_STORES:
        return _check_padel(name, d)
    rows = [v for v in d.values() if isinstance(v, dict)]
    n = len(rows)
    issues = []

    # NOTE : overbook (présents > capacité) volontairement retiré — la plupart
    # des plateformes booking acceptent les sur-bookings (liste d'attente, places
    # walk-in, comptage différé). Ce n'est pas un signal de qualité fiable.

    # 1. capacités aberrantes (livestream / cours géants visibles via API)
    bigcap = [r for r in rows if (r.get("capacite") or 0) > CAP_MAX]
    if bigcap:
        issues.append(f"{len(bigcap)} séances avec capacité > {CAP_MAX} (livestream ?)")

    # 2. last update : trouve le releve le plus récent dans le store
    last = max((r.get("releve") or "" for r in rows), default="")
    if last:
        try:
            last_dt = dt.datetime.strptime(last[:16], "%Y-%m-%d %H:%M")
            hours = (dt.datetime.now() - last_dt).total_seconds() / 3600
            limit = STALE_HOURS_FAST if name in FAST else STALE_HOURS_SLOW
            if hours > limit:
                issues.append(f"dernier relevé il y a {hours:.0f}h (> {limit}h attendu)")
        except ValueError:
            pass

    return {"brand": name, "status": "OK" if not issues else "WARN",
            "count": n, "issues": issues}


def main():
    baseline = safestore.load(BASELINE) if os.path.exists(BASELINE) else {}
    report = {"checked_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "brands": []}
    severe = False
    new_baseline = dict(baseline)
    for path in sorted(glob.glob("*_data.json")):
        r = check(path)
        # comparaison volume vs baseline
        prev = baseline.get(r["brand"], 0)
        cur = r.get("count", 0)
        if prev and cur < prev * DROP_THRESHOLD:
            r["issues"].append(f"VOLUME EFFONDRÉ : {cur} entrées vs {prev} en baseline (-{round(100*(1-cur/prev))}%)")
            r["status"] = "ALERT"
            severe = True
        elif r["status"] == "WARN" and any("dernier relevé" in i for i in r["issues"]):
            r["status"] = "ALERT"
            severe = True
        # met à jour le baseline seulement si la marque grossit ou reste stable
        if cur >= prev:
            new_baseline[r["brand"]] = cur
        report["brands"].append(r)
        marker = {"OK": "✅", "WARN": "⚠️", "ALERT": "🚨", "ERROR": "❌"}[r["status"]]
        msg = f"{marker} {r['brand']:18s} {cur:5d} entrées"
        if r["issues"]:
            msg += "  | " + " ; ".join(r["issues"])
        print(msg)

    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    # baseline jamais en rétrécissement (safestore le refusera de toute façon)
    safestore.save(new_baseline, BASELINE)

    bad = sum(1 for b in report["brands"] if b["status"] in ("ALERT", "ERROR"))
    print(f"\nrapport: {len(report['brands'])} marques, {bad} alertes graves -> sanity_report.json")
    sys.exit(1 if severe else 0)


if __name__ == "__main__":
    main()
