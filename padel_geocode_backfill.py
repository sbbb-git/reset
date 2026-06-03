#!/usr/bin/env python3
"""Backfill lat/lng pour les clubs padel_national qui n'en ont pas.

Source 1 (prio) : padel_national_clubs.json (catalogue mergé qui a lat/lng
de Doinsport/Playtomic quand exposé). Match par slug.

Source 2 (fallback, optionnel) : Nominatim OSM via CP+ville+France.
Limites Nominatim : 1 req/sec, UA explicite obligatoire. Cache local
dans padel_geocode_cache.json pour idempotence.

A exécuter dans le workflow sectors-padel-national.yml après le scrape,
AVANT le sync Supabase et AVANT le prune.
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

STORE = "padel_national_data.json"
CATALOG = "padel_national_clubs.json"
CACHE = "padel_geocode_cache.json"
UA = "Mozilla/5.0 (compatible; padel-data-scraper/1.0; contact: sachabitoun17@gmail.com)"
USE_NOMINATIM = os.environ.get("USE_NOMINATIM", "0") == "1"   # opt-in (slow)


def load(path, default):
    if not os.path.exists(path):
        return default
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def index_catalog():
    cat = load(CATALOG, [])
    if not isinstance(cat, list):
        return {}
    by_slug = {}
    for c in cat:
        if not isinstance(c, dict):
            continue
        s = c.get("slug")
        if s and c.get("lat") is not None and c.get("lng") is not None:
            by_slug[s] = (c["lat"], c["lng"])
    return by_slug


def nominatim_lookup(cp, ville, cache):
    """1 req/sec, retry sur erreur transitoire."""
    key = f"{cp}|{(ville or '').lower().strip()}"
    if key in cache:
        return cache[key]
    if not cp:
        return None
    q = f"{cp} {ville or ''} France".strip()
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": 1, "countrycodes": "fr"})
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)
            time.sleep(1.1)  # Nominatim rate-limit
            if data:
                latlng = (float(data[0]["lat"]), float(data[0]["lon"]))
                cache[key] = latlng
                return latlng
            cache[key] = None
            return None
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            cache[key] = None
            return None
        except Exception:  # noqa: BLE001
            time.sleep(2)
    cache[key] = None
    return None


def main():
    store = load(STORE, {})
    if not isinstance(store, dict) or not store:
        print(f"❌ {STORE} vide ou absent")
        sys.exit(0)

    by_slug = index_catalog()
    cache = load(CACHE, {})
    cat_hits = 0
    nom_hits = 0
    miss = 0
    skip = 0

    for slug, club in store.items():
        meta = club.get("meta") if isinstance(club, dict) else None
        if not isinstance(meta, dict):
            continue
        if meta.get("lat") and meta.get("lng"):
            skip += 1
            continue
        latlng = by_slug.get(slug)
        if latlng:
            meta["lat"], meta["lng"] = latlng
            meta["geocode_source"] = "catalogue"
            cat_hits += 1
            continue
        if USE_NOMINATIM:
            cp = str(meta.get("cp") or "")
            ville = meta.get("city") or meta.get("ville")
            ll = nominatim_lookup(cp, ville, cache)
            if ll:
                meta["lat"], meta["lng"] = ll
                meta["geocode_source"] = "nominatim"
                nom_hits += 1
                continue
        miss += 1

    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, separators=(",", ":"))
    if USE_NOMINATIM:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)

    print(f"Backfill GPS : déjà géocodés {skip} | catalogue {cat_hits} | "
          f"nominatim {nom_hits} | restants {miss} ({len(store)} clubs total)")


if __name__ == "__main__":
    main()
