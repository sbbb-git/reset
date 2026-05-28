#!/usr/bin/env python3
"""Scrape le taux de remplissage des seances Re-SET (booking bsport).

Recupere, jour par jour et seance par seance, le nombre de personnes
presentes / la capacite de chaque seance, et ecrit le tout dans un CSV.

Relancer le script reactualise les donnees (il reecrit le CSV).

Usage:
    python3 reset_scrape.py                         # depuis 2026-04-22 jusqu'a aujourd'hui
    python3 reset_scrape.py --start 2026-04-22      # date de debut
    python3 reset_scrape.py --start 2026-04-22 --end 2026-06-30
    python3 reset_scrape.py --out mes_donnees.csv

Source: https://www.re-set.club/reservation  (widget bsport, company 5181)
"""
import argparse
import csv
import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request

API = "https://api.production.bsport.io"
COMPANY = 5181
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def _get(path, params, retries=4):
    url = f"{API}{path}?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE401
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"echec requete {url}: {last}")


def fetch_coaches():
    """Table associated_coach_id -> nom complet (best effort)."""
    mapping = {}
    try:
        data = _get("/book/v1/associated_coach/", {"company": COMPANY, "page_size": 500})
        for c in data.get("results", []):
            name = c.get("name") or f"{c.get('firstname','')} {c.get('lastname','')}".strip()
            for key in ("associated_coach_id", "id"):
                if c.get(key):
                    mapping[c[key]] = name
            for aid in c.get("associatedcoach_set", []) or []:
                mapping.setdefault(aid, name)
    except Exception as e:  # noqa: BLE401
        print(f"  (avertissement: noms des coachs indisponibles: {e})", file=sys.stderr)
    return mapping


def fetch_offers(start, end):
    """Toutes les seances entre start et end (dates incluses)."""
    offers = []
    page = 1
    while True:
        data = _get("/book/v1/offer/", {
            "company": COMPANY,
            "only_future_strict": "false",
            "min_date": start.isoformat(),
            "max_date": end.isoformat(),
            "page_size": 300,
            "page": page,
        })
        results = data.get("results", [])
        offers.extend(results)
        if not data.get("next_page") or not results:
            break
        page += 1
    return offers


def main():
    ap = argparse.ArgumentParser(description="Scrape taux de remplissage des seances Re-SET")
    ap.add_argument("--start", default="2026-04-22", help="date de debut AAAA-MM-JJ (defaut 2026-04-22)")
    ap.add_argument("--end", default=None, help="date de fin AAAA-MM-JJ (defaut: aujourd'hui)")
    ap.add_argument("--out", default="reset_seances.csv", help="fichier CSV de sortie")
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()

    print(f"Recuperation des seances du {start} au {end} ...")
    coaches = fetch_coaches()
    offers = fetch_offers(start, end)
    print(f"{len(offers)} seances recuperees.")

    rows = []
    for o in offers:
        ds = o.get("date_start", "")
        local = ds[:16].replace("T", " ")  # 'AAAA-MM-JJ HH:MM' heure locale Paris
        date_part = local[:10]
        heure = local[11:16]
        try:
            d = dt.date.fromisoformat(date_part)
            jour = JOURS_FR[d.weekday()]
        except ValueError:
            jour = ""
        present = o.get("validated_booking_count", 0)
        capacite = o.get("effectif", 0)
        taux = round(100 * present / capacite, 1) if capacite else ""
        coach_id = o.get("coach")
        rows.append({
            "date": date_part,
            "jour": jour,
            "heure": heure,
            "activite": o.get("activity_name", ""),
            "coach": coaches.get(coach_id, coach_id if coach_id else ""),
            "presents": present,
            "capacite": capacite,
            "remplissage": f"{present}/{capacite}",
            "taux_%": taux,
            "complet": "oui" if o.get("full") else "non",
            "session_id": o.get("id"),
        })

    rows.sort(key=lambda r: (r["date"], r["heure"]))

    fields = ["date", "jour", "heure", "activite", "coach", "presents",
              "capacite", "remplissage", "taux_%", "complet", "session_id"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    if rows:
        jours = sorted({r["date"] for r in rows})
        tot_p = sum(r["presents"] for r in rows)
        tot_c = sum(r["capacite"] for r in rows)
        moy = round(100 * tot_p / tot_c, 1) if tot_c else 0
        print(f"-> {args.out} : {len(rows)} seances sur {len(jours)} jours "
              f"({jours[0]} -> {jours[-1]}), {tot_p} presents / {tot_c} places, "
              f"taux moyen {moy}%.")
    else:
        print("Aucune seance trouvee sur cette periode.")


if __name__ == "__main__":
    main()
