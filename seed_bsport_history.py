#!/usr/bin/env python3
"""Amorce l'historique d'une marque bsport à partir de l'API (qui rend le passé).

Le scrape courant ne lit qu'une fenêtre de J-3 à J+2 : une marque ajoutée
aujourd'hui n'aurait d'historique que dans quelques semaines. Ce script
remplit son store sur N jours, au format exact de bsport_scrape.capture,
sans écraser ce qui existe déjà.

Usage :
  python3 seed_bsport_history.py 90 culte kind_yoga …      (extensions)
  python3 seed_bsport_history.py 90 kore:3604 belly:6902   (une company précise
                                                            d'une marque suivie)
"""
import datetime as dt
import glob
import json
import sys
from zoneinfo import ZoneInfo

import bsport_scrape as b
import safestore

PARIS = ZoneInfo("Europe/Paris")
JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def companies_de(key):
    for f in glob.glob("*_extension_resolved.json"):
        r = json.load(open(f, encoding="utf-8")).get(key)
        if r and r.get("company_id"):
            return [int(c) for c in str(r["company_id"]).split(",") if c.strip()]
    raise SystemExit(f"{key} : company bsport introuvable")


def amorcer(key, companies, jours):
    now = dt.datetime.now(PARIS)
    store_path = f"{key}_data.json"
    store = safestore.load(store_path)
    avant = len(store)
    d1 = now.date() - dt.timedelta(days=1)
    for co in companies:
        est, _ = b.fetch_establishments(co)
        coaches = b.fetch_coaches(co)
        coaches = coaches[0] if isinstance(coaches, tuple) else coaches
        d0 = now.date() - dt.timedelta(days=jours)
        while d0 <= d1:
            z = min(d0 + dt.timedelta(days=29), d1)
            for o in b.fetch_offers(co, d0, z):
                sid = str(o.get("id"))
                ds = o.get("date_start") or ""
                if not ds or sid in store:
                    continue
                sdt = dt.datetime.fromisoformat(ds)
                edt = sdt + dt.timedelta(minutes=o.get("duration_minute") or 0)
                cid = o.get("coach")
                store[sid] = {
                    "id": sid, "date": ds[:10], "jour": JOURS[sdt.weekday()],
                    "heure": ds[11:16], "fin": edt.strftime("%H:%M"),
                    "lieu": est.get(o.get("establishment"), f"Studio {o.get('establishment')}"),
                    "cours": (o.get("activity_name") or o.get("name_override") or "").strip(),
                    "coach": coaches.get(cid, "") if cid else "",
                    "capacite": o.get("effectif") or 0,
                    "presents": o.get("validated_booking_count") or 0,
                    "finie": now >= edt.astimezone(PARIS),
                    "releve": now.strftime("%Y-%m-%d %H:%M"),
                }
            d0 = z + dt.timedelta(days=1)
    safestore.save(store, store_path)
    print(f"  {key:32} {avant:>6} -> {len(store):>6} séances")


def main():
    jours = int(sys.argv[1])
    for arg in sys.argv[2:]:
        key, _, co = arg.partition(":")
        try:
            amorcer(key, [int(c) for c in co.split(",")] if co else companies_de(key), jours)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ {key} : {type(e).__name__} {str(e)[:120]}")


if __name__ == "__main__":
    main()
