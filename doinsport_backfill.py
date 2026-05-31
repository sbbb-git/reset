#!/usr/bin/env python3
"""Backfill historique Doinsport : 24-36 mois de réservations sur 54 clubs IDF.

Doinsport est la seule plateforme à exposer les bookings PASSÉS via son
endpoint /clubs/bookings/plannings filtré par club.id. On récupère par
tranches mensuelles depuis janvier 2023 pour chaque club IDF, post-filtré
sur les playgrounds padel uniquement.

Sortie :
  - padel_idf_history.json : store JSON par club (séparé du store live pour
    ne pas surcharger les commits du workflow live).
  - Optionnel : sync vers Supabase via service key si dispo (idempotent).

Note : c'est un script one-shot pensé pour être lancé via workflow GitHub
Action manuel (workflow_dispatch).
"""
import datetime as dt
import json
import os
import safestore
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

# Réutilise la même base de code que doinsport_scrape.py
from doinsport_scrape import (
    API, UA, JOURS_FR, fetch_playgrounds, _get_json
)

PARIS = ZoneInfo("Europe/Paris")
STORE = "padel_idf_history.json"
CLUBS_FILE = "doinsport_idf_clubs.json"
START_YEAR = 2023
START_MONTH = 1
MAX_WORKERS = 6


def fetch_club_month(club_uuid, year, month, allowed_pg_ids):
    """Récupère les bookings d'un club pour un mois donné, post-filtre padel."""
    start = dt.datetime(year, month, 1)
    # fin du mois
    if month == 12:
        end = dt.datetime(year + 1, 1, 1) - dt.timedelta(seconds=1)
    else:
        end = dt.datetime(year, month + 1, 1) - dt.timedelta(seconds=1)
    params = urllib.parse.urlencode({
        "club.id": club_uuid,
        "startAt[after]": start.isoformat(),
        "startAt[before]": end.isoformat(),
        "itemsPerPage": "1000",
    }, safe="[]")
    page = 1
    out = []
    while True:
        try:
            d = _get_json(f"{API}/clubs/bookings/plannings?{params}&page={page}")
        except Exception as e:  # noqa: BLE001
            print(f"  ❌ {club_uuid} {year}-{month:02d} page {page}: {e}", file=sys.stderr)
            break
        items = d.get("hydra:member") if isinstance(d, dict) else (d if isinstance(d, list) else [])
        if not items:
            break
        for b in items:
            pgs = b.get("playgrounds") or []
            matched_pg = None
            for pg in pgs:
                pid = (pg.get("id") or "").split("/")[-1] if isinstance(pg, dict) else ""
                if pid in allowed_pg_ids:
                    matched_pg = pid
                    break
            if matched_pg:
                out.append((b, matched_pg))
        if len(items) < 1000:
            break
        page += 1
        if page > 50:  # safety
            print(f"  ⚠️ {club_uuid} {year}-{month:02d}: arrêt à page 50", file=sys.stderr)
            break
    return out


def capture_club_history(club, store, since_year, since_month):
    """Récupère tout l'historique d'un club Doinsport depuis (since_year, since_month)."""
    now = dt.datetime.now(PARIS)
    slug = f"doinsport-{club['id']}"
    bucket = store.setdefault(slug, {
        "meta": {"slug": slug, "name": club["name"], "cp": club.get("cp", ""),
                 "city": club.get("city"), "club_uuid": club["id"], "source": "doinsport_history"},
        "sessions": {},
    })
    bucket["meta"]["source"] = "doinsport_history"
    pgs = fetch_playgrounds(club["id"])
    pg_padel_ids = {p["id"] for p in pgs}
    if not pg_padel_ids:
        return 0
    pg_names = {p["id"]: p["name"] for p in pgs}
    seen = 0
    cursor = dt.date(since_year, since_month, 1)
    today = now.date()
    while cursor < today:
        bks = fetch_club_month(club["id"], cursor.year, cursor.month, pg_padel_ids)
        for b, matched_pg in bks:
            start_raw = b.get("startAt")
            end_raw = b.get("endAt")
            if not start_raw: continue
            try:
                sdt = dt.datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(PARIS)
                edt = dt.datetime.fromisoformat(end_raw.replace("Z", "+00:00")).astimezone(PARIS) if end_raw else sdt
            except Exception:
                continue
            duration = int((edt - sdt).total_seconds() / 60) if end_raw else 60
            price_cents = b.get("price") or 0
            price = round(price_cents / 100.0, 2)
            sid = f"{start_raw}|{matched_pg}"
            bucket["sessions"][sid] = {
                "id": sid, "start": sdt.replace(tzinfo=None).isoformat(),
                "date": sdt.date().isoformat(),
                "jour": JOURS_FR[sdt.weekday()],
                "heure": sdt.strftime("%H:%M"),
                "fin": edt.strftime("%H:%M"),
                "terrain": pg_names.get(matched_pg) or "Terrain padel",
                "court_id": matched_pg,
                "duree": duration,
                "prix": price if price else None,
                "vu_dispo": False, "vu_dispo_ce_passage": False,
                "premier_vu": now.strftime("%Y-%m-%d %H:%M"),
                "dernier_vu": now.strftime("%Y-%m-%d %H:%M"),
                "finie": True,                       # tout est passé
                "statut": "reserve",
                "releve": now.strftime("%Y-%m-%d %H:%M"),
                "source": "doinsport_history",
                "booking_uuid": b.get("id") or sid,
            }
            seen += 1
        # mois suivant
        cursor = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return seen


def main():
    if not os.path.exists(CLUBS_FILE):
        print(f"❌ {CLUBS_FILE} introuvable", file=sys.stderr); sys.exit(1)
    clubs = json.load(open(CLUBS_FILE, encoding="utf-8"))
    store = safestore.load(STORE)
    print(f"Backfill historique Doinsport : {len(clubs)} clubs IDF, depuis {START_YEAR}-{START_MONTH:02d}")
    t0 = time.time()
    total = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(capture_club_history, c, store, START_YEAR, START_MONTH): c for c in clubs}
        for f in as_completed(futs):
            c = futs[f]
            try:
                n = f.result()
                total += n
                if n:
                    print(f"  ✅ {c['name'][:50]:<50} {n:>6} bookings historiques")
                # save intermédiaire toutes les 5 clubs pour éviter perte
                safestore.save(store, STORE)
            except Exception as e:  # noqa: BLE001
                print(f"  ❌ {c.get('name')}: {e}", file=sys.stderr)
    safestore.save(store, STORE)
    dur = time.time() - t0
    print(f"\n{dt.datetime.now(PARIS):%Y-%m-%d %H:%M} : {total} bookings historiques sur "
          f"{len(clubs)} clubs en {dur:.1f}s")


if __name__ == "__main__":
    main()
