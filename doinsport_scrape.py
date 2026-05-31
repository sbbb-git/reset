#!/usr/bin/env python3
"""Doinsport (54 clubs IDF padel) — backend api-v3.doinsport.club.

Reverse-engineering du bundle main.js de l'app SPA :
- Liste clubs FR padel : GET /clubs?activities[]=padel&country=FR (Hydra paginated)
- Playgrounds (terrains) d'un club : GET /clubs/{club_uuid}/playgrounds
- Réservations d'un playground : GET /clubs/bookings/plannings
    ?playgrounds[]={pg_uuid}&startAt[after]=ISO&startAt[before]=ISO&itemsPerPage=500

Différence majeure avec Anybuddy/UrbanPadel : Doinsport expose directement
les RÉSERVATIONS CONFIRMÉES (avec prix réel en cents, créneau, terrain),
pas les créneaux disponibles. C'est plus simple — pas besoin de détecter
des disparitions, on a la donnée d'occupation tout de suite.

Source clubs : doinsport_idf_clubs.json (CP filtrés 5 chiffres + IDF).

Sortie : padel_idf_data.json — append avec source "doinsport", structure
compatible avec celle d'Anybuddy et UrbanPadel (mêmes champs : id, date,
heure, fin, terrain, duree, prix, statut). Tous les bookings sont marqués
statut="reserve" puisque ce sont par définition des réservations confirmées.
"""
import datetime as dt
import json
import safestore
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
STORE = "padel_idf_data.json"
CLUBS_FILE = "doinsport_idf_clubs.json"
API = "https://api-v3.doinsport.club"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
HORIZON_JOURS = 7
MAX_WORKERS = 8


def _get_json(url, retries=3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "application/json",
                "Origin": "https://app.doinsport.club",
                "Referer": "https://app.doinsport.club/"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.0 + 0.7 * attempt)
    raise RuntimeError(f"echec {url}: {last}")


def fetch_playgrounds(club_uuid):
    """Renvoie la liste des playgrounds (terrains) d'un club Doinsport."""
    try:
        d = _get_json(f"{API}/clubs/{club_uuid}/playgrounds")
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ playgrounds {club_uuid}: {e}", file=sys.stderr)
        return []
    # API renvoie soit liste directe, soit objet Hydra avec hydra:member
    if isinstance(d, list):
        items = d
    else:
        items = d.get("hydra:member") or []
    out = []
    for p in items:
        if not isinstance(p, dict):
            continue
        # Filtre padel : check activities pour le mot 'padel'
        activities = p.get("activities") or []
        names = []
        for a in activities:
            if isinstance(a, dict):
                names.append((a.get("name") or "").lower())
            else:
                names.append(str(a).lower())
        is_padel = any("padel" in n for n in names) if names else True
        out.append({
            "id": p.get("id"),
            "name": p.get("name") or f"Terrain {p.get('id','')[:8]}",
            "indoor": p.get("indoor"),
            "is_padel": is_padel,
        })
    return out


def fetch_bookings(playground_uuid, start, end):
    """Renvoie les bookings d'un playground sur la fenêtre [start, end]."""
    params = urllib.parse.urlencode({
        "playgrounds[]": playground_uuid,
        "startAt[after]": start.isoformat(),
        "startAt[before]": end.isoformat(),
        "itemsPerPage": "200",
    }, safe="[]")
    try:
        d = _get_json(f"{API}/clubs/bookings/plannings?{params}")
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ bookings {playground_uuid}: {e}", file=sys.stderr)
        return []
    items = d.get("hydra:member") if isinstance(d, dict) else (d if isinstance(d, list) else [])
    return items or []


def capture_club(club, store):
    """Récupère les playgrounds + bookings du club et fusionne dans store[slug]."""
    now = dt.datetime.now(PARIS)
    today = now.date()
    end = today + dt.timedelta(days=HORIZON_JOURS)
    slug = f"doinsport-{club['id']}"
    bucket = store.setdefault(slug, {
        "meta": {"slug": slug, "name": club["name"], "cp": club.get("cp", ""),
                 "city": club.get("city"), "club_uuid": club["id"],
                 "source": "doinsport", "also_on_anybuddy": club.get("also_on_anybuddy", False)},
        "sessions": {},
    })
    bucket["meta"]["source"] = "doinsport"
    pgs = fetch_playgrounds(club["id"])
    pg_padel = [p for p in pgs if p["is_padel"]]
    if not pg_padel:
        # certains clubs déclarent padel sans avoir de playground padel actif
        return 0, 0
    seen = booked = 0
    pg_names = {p["id"]: p["name"] for p in pg_padel}
    for pg in pg_padel:
        bks = fetch_bookings(pg["id"], dt.datetime.combine(today, dt.time(0, 0)),
                             dt.datetime.combine(end, dt.time(23, 59)))
        for b in bks:
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
            booking_uuid = b.get("id") or f"{start_raw}|{pg['id']}|{duration}"
            sid = f"{start_raw}|{pg['id']}"
            bucket["sessions"][sid] = {
                "id": sid,
                "start": sdt.replace(tzinfo=None).isoformat(),
                "date": sdt.date().isoformat(),
                "jour": JOURS_FR[sdt.weekday()],
                "heure": sdt.strftime("%H:%M"),
                "fin": edt.strftime("%H:%M"),
                "terrain": pg_names.get(pg["id"]) or "Terrain padel",
                "court_id": pg["id"],
                "duree": duration,
                "prix": price if price else None,
                "vu_dispo": False,           # par défaut : c'est une réservation, donc non-dispo
                "vu_dispo_ce_passage": False,
                "premier_vu": bucket["sessions"].get(sid, {}).get("premier_vu") or now.strftime("%Y-%m-%d %H:%M"),
                "dernier_vu": now.strftime("%Y-%m-%d %H:%M"),
                "finie": now >= edt,
                "statut": "reserve",
                "releve": now.strftime("%Y-%m-%d %H:%M"),
                "source": "doinsport",
                "booking_uuid": booking_uuid,
            }
            seen += 1
            if not (now >= edt):
                booked += 1
    return seen, booked


def main():
    if not __import__("os").path.exists(CLUBS_FILE):
        print(f"❌ {CLUBS_FILE} introuvable — lance d'abord le script de catalogue.", file=sys.stderr)
        sys.exit(1)
    clubs = json.load(open(CLUBS_FILE, encoding="utf-8"))
    store = safestore.load(STORE)
    print(f"Scrape Doinsport : {len(clubs)} clubs IDF, parallélisme x{MAX_WORKERS}")
    t0 = time.time()
    total_seen = total_booked = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(capture_club, c, store): c for c in clubs}
        for f in as_completed(futs):
            c = futs[f]
            try:
                seen, booked = f.result()
                total_seen += seen
                total_booked += booked
                if seen:
                    print(f"  ✅ {c['name'][:50]:<50} {seen:>4} réservations")
            except Exception as e:  # noqa: BLE001
                print(f"  ❌ {c['name']}: {e}", file=sys.stderr)
    safestore.save(store, STORE)
    dur = time.time() - t0
    print(f"\n{dt.datetime.now(PARIS):%Y-%m-%d %H:%M} : {total_seen} réservations vues ({total_booked} à venir) "
          f"sur {len(clubs)} clubs Doinsport en {dur:.1f}s")


if __name__ == "__main__":
    main()
