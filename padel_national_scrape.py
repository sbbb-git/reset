#!/usr/bin/env python3
"""Scrape national : applique les 4 engines de scrape aux catalogues nationaux.

Lit :
- padel_national_anybuddy.json
- padel_national_doinsport.json
- padel_national_playtomic.json
- padel_national_urbanpadel.json

Réutilise les fonctions de capture des scrapers IDF existants
(anybuddy_scrape.py, urbanpadel_scrape.py, doinsport_scrape.py, playtomic_scrape.py).

Sortie : padel_national_data.json (store national, structure identique à
padel_idf_data.json mais ~700-800 clubs, ~150-200k sessions live attendues).

Lancement : workflow GitHub Action séparé (toutes les 2h) ; le workflow
IDF /30min reste séparé pour la haute fraîcheur sur les 130 clubs IDF.
"""
import datetime as dt
import importlib
import json
import os
import safestore
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

NATIONAL_STORE = "padel_national_data.json"
MAX_WORKERS = 16


def main():
    t0 = time.time()
    store = safestore.load(NATIONAL_STORE)
    print(f"📦 Store national au démarrage : {len(store)} clubs")

    # ============ ANYBUDDY ============
    print("\n=== ANYBUDDY national ===")
    if os.path.exists("padel_national_anybuddy.json"):
        ab_clubs = json.load(open("padel_national_anybuddy.json"))
        # Réutilise les fonctions de padel_idf_scrape
        import padel_idf_scrape as ab
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(ab.capture_club, c["slug"], c, store): c for c in ab_clubs}
            ok = 0
            for f in as_completed(futs):
                c = futs[f]
                try:
                    seen, booked = f.result()
                    ok += 1
                except Exception as e:
                    print(f"  ❌ {c.get('slug')}: {e}", file=sys.stderr)
                if ok % 50 == 0:
                    print(f"  Anybuddy : {ok}/{len(ab_clubs)} clubs traités")
        safestore.save(store, NATIONAL_STORE)

    # ============ DOINSPORT ============
    print("\n=== DOINSPORT national ===")
    if os.path.exists("padel_national_doinsport.json"):
        do_clubs = json.load(open("padel_national_doinsport.json"))
        import doinsport_scrape as do
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(do.capture_club, c, store): c for c in do_clubs}
            ok = 0
            for f in as_completed(futs):
                c = futs[f]
                try:
                    seen, booked = f.result()
                    ok += 1
                except Exception as e:
                    print(f"  ❌ {c.get('name')}: {e}", file=sys.stderr)
                if ok % 20 == 0:
                    print(f"  Doinsport : {ok}/{len(do_clubs)} clubs traités")
        safestore.save(store, NATIONAL_STORE)

    # ============ PLAYTOMIC ============
    print("\n=== PLAYTOMIC national ===")
    if os.path.exists("padel_national_playtomic.json"):
        pt_clubs = json.load(open("padel_national_playtomic.json"))
        import playtomic_scrape as pt
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(pt.capture_club, c, store): c for c in pt_clubs}
            ok = 0
            for f in as_completed(futs):
                c = futs[f]
                try:
                    seen, booked = f.result()
                    ok += 1
                except Exception as e:
                    print(f"  ❌ {c.get('name')}: {e}", file=sys.stderr)
        safestore.save(store, NATIONAL_STORE)

    # ============ URBANPADEL ============
    print("\n=== URBANPADEL national ===")
    if os.path.exists("padel_national_urbanpadel.json"):
        up_clubs = json.load(open("padel_national_urbanpadel.json"))
        import urbanpadel_scrape as up
        # Le scraper UrbanPadel a une liste hard-codée — on remplace par celle national
        # Adapter au format CENTERS attendu
        up.CENTERS = [{
            "id": c["id"],
            "slug": f"urbanpadel-{c['id']}",
            "name": c.get("name") or f"UrbanPadel {c['id']}",
            "cp": c.get("cp", ""),
            "address": c.get("address", ""),
        } for c in up_clubs]
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(up.capture_center, c, store): c for c in up.CENTERS}
            ok = 0
            for f in as_completed(futs):
                c = futs[f]
                try:
                    seen, booked = f.result()
                    ok += 1
                except Exception as e:
                    print(f"  ❌ {c.get('slug')}: {e}", file=sys.stderr)
        safestore.save(store, NATIONAL_STORE)

    n_total = len(store)
    n_sess = sum(len(b.get("sessions", {})) for b in store.values())
    dur = (time.time() - t0) / 60
    print(f"\n✅ Scrape national terminé en {dur:.1f} min : {n_total} clubs, {n_sess:,} sessions".replace(",", " "))


if __name__ == "__main__":
    main()
