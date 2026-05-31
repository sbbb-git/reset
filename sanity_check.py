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
STALE_HOURS_FAST = 6      # live-status / live-senseclub doivent bouger souvent
STALE_HOURS_SLOW = 30     # bsport / daily peuvent attendre plus

# rythme de MAJ attendu par marque (en heures)
FAST = {"barrys", "episod", "anybuddy", "banote", "dna", "le33foch", "senseclub",
        "burningbar", "santroch"}


def check(path):
    name = os.path.basename(path).replace("_data.json", "")
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"brand": name, "status": "ERROR", "issues": [f"lecture impossible : {e}"]}
    if not isinstance(d, dict):
        return {"brand": name, "status": "ERROR", "issues": ["format inattendu (pas un dict)"]}
    rows = list(d.values())
    n = len(rows)
    issues = []

    # 1. overbook : présents > capacité
    overbook = [r for r in rows if (r.get("presents") or 0) > (r.get("capacite") or 0) > 0]
    if overbook:
        issues.append(f"{len(overbook)} séances avec présents > capacité")

    # 2. capacités aberrantes
    bigcap = [r for r in rows if (r.get("capacite") or 0) > CAP_MAX]
    if bigcap:
        issues.append(f"{len(bigcap)} séances avec capacité > {CAP_MAX} (livestream ?)")

    # 3. last update : trouve le releve le plus récent dans le store
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
