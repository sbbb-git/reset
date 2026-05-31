#!/usr/bin/env python3
"""Playtomic (23 clubs IDF padel) — endpoint api.playtomic.io.

API trouvée via inspection HTML des pages playtomic.com/clubs/<slug> :
- Liste tenants par pays : GET /v1/tenants?country_code=FR&sport_id=PADEL&size=2000
- Dispos par tenant       : GET /v1/availability?tenant_id=<UUID>&sport_id=PADEL
                            &local_start_min=ISO&local_start_max=ISO
- Réponse : liste {resource_id, start_date, slots:[{start_time, duration, price}]}
  où prix au format "84 EUR".

Casa Padel utilise Playtomic (info user) — confirmé : 3 sites IDF
(Asnières, Saint-Denis, Croissy-Beaubourg). Plus 20 autres clubs IDF.
Mécanisme = identique à Anybuddy : disparition d'un slot = réservé.

Sortie : padel_idf_data.json avec slugs playtomic-<short_tenant_id>.
"""
import datetime as dt
import json
import re
import safestore
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
STORE = "padel_idf_data.json"
CLUBS_FILE = "playtomic_idf_clubs.json"
API = "https://api.playtomic.io"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
HORIZON_JOURS = 7
MAX_WORKERS = 8

PRICE_RE = re.compile(r"^([\d.]+)\s*([A-Z]{3})$")


def _get_json(url, retries=3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.0 + 0.7 * attempt)
    raise RuntimeError(f"echec {url}: {last}")


def parse_price(s):
    if not s: return None
    m = PRICE_RE.match(str(s).strip())
    if m:
        try: return float(m.group(1))
        except ValueError: return None
    return None


def fetch_day(tenant_id, day):
    """Renvoie les slots dispos d'un tenant Playtomic pour un jour."""
    params = urllib.parse.urlencode({
        "user_id": "me",
        "tenant_id": tenant_id,
        "sport_id": "PADEL",
        "local_start_min": f"{day.isoformat()}T00:00:00",
        "local_start_max": f"{day.isoformat()}T23:59:59",
    })
    try:
        return _get_json(f"{API}/v1/availability?{params}")
    except Exception:
        return []


def capture_club(club, store):
    now = dt.datetime.now(PARIS)
    today = now.date()
    tid = club["tenant_id"]
    slug = f"playtomic-{tid[:8]}"
    bucket = store.setdefault(slug, {
        "meta": {"slug": slug, "name": club.get("name"), "cp": club.get("cp", ""),
                 "city": club.get("city"), "tenant_id": tid, "source": "playtomic"},
        "sessions": {},
    })
    bucket["meta"]["source"] = "playtomic"
    sessions = bucket["sessions"]

    # Marquer non-vus pour détecter disparitions = réservations
    for sid, v in sessions.items():
        if v.get("source") != "playtomic" or v.get("finie"):
            continue
        try:
            sdt = dt.datetime.fromisoformat(v["start"]).replace(tzinfo=PARIS)
        except Exception:
            continue
        if sdt > now:
            v["vu_dispo_ce_passage"] = False

    seen = 0
    for off in range(HORIZON_JOURS):
        d = today + dt.timedelta(days=off)
        try:
            entries = fetch_day(tid, d)
        except Exception as e:
            continue
        for entry in entries if isinstance(entries, list) else []:
            resource_id = entry.get("resource_id") or "?"
            sdate = entry.get("start_date")
            for slot in entry.get("slots") or []:
                start_time = slot.get("start_time")
                duration = int(slot.get("duration") or 90)
                price = parse_price(slot.get("price"))
                if not sdate or not start_time: continue
                start_raw = f"{sdate}T{start_time}"
                try:
                    sdt = dt.datetime.fromisoformat(start_raw).replace(tzinfo=PARIS)
                except Exception:
                    continue
                edt = sdt + dt.timedelta(minutes=duration)
                sid = f"{start_raw}|{resource_id}|{duration}"
                prev = sessions.get(sid, {})
                sessions[sid] = {
                    "id": sid,
                    "start": start_raw,
                    "date": sdt.date().isoformat(),
                    "jour": JOURS_FR[sdt.weekday()],
                    "heure": sdt.strftime("%H:%M"),
                    "fin": edt.strftime("%H:%M"),
                    "terrain": f"Padel {resource_id[:8]}",
                    "court_id": resource_id,
                    "duree": duration,
                    "prix": price or prev.get("prix"),
                    "vu_dispo": True,
                    "vu_dispo_ce_passage": True,
                    "premier_vu": prev.get("premier_vu") or now.strftime("%Y-%m-%d %H:%M"),
                    "dernier_vu": now.strftime("%Y-%m-%d %H:%M"),
                    "finie": now >= edt,
                    "statut": prev.get("statut") or "disponible",
                    "releve": now.strftime("%Y-%m-%d %H:%M"),
                    "source": "playtomic",
                }
                seen += 1
    # Détection disparitions
    booked = 0
    for sid, v in sessions.items():
        if v.get("source") != "playtomic": continue
        try:
            sdt = dt.datetime.fromisoformat(v["start"]).replace(tzinfo=PARIS)
            edt = sdt + dt.timedelta(minutes=int(v.get("duree") or 90))
        except Exception:
            continue
        if now >= edt:
            v["finie"] = True
        if not v.get("finie") and not v.get("vu_dispo_ce_passage") and v.get("vu_dispo"):
            if v.get("statut") == "disponible":
                v["statut"] = "reserve"
                booked += 1
    return seen, booked


def main():
    import os
    if not os.path.exists(CLUBS_FILE):
        print(f"❌ {CLUBS_FILE} introuvable", file=sys.stderr); sys.exit(1)
    clubs = json.load(open(CLUBS_FILE, encoding="utf-8"))
    store = safestore.load(STORE)
    print(f"Scrape Playtomic : {len(clubs)} clubs IDF, parallélisme x{MAX_WORKERS}")
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
                    print(f"  ✅ {(c.get('name') or '')[:50]:<50} {seen:>4} créneaux, {booked:>2} disparus")
            except Exception as e:  # noqa: BLE001
                print(f"  ❌ {c.get('name')}: {e}", file=sys.stderr)
    safestore.save(store, STORE)
    dur = time.time() - t0
    print(f"\n{dt.datetime.now(PARIS):%Y-%m-%d %H:%M} : {total_seen} créneaux vus ({total_booked} disparus) "
          f"sur {len(clubs)} clubs Playtomic en {dur:.1f}s")


if __name__ == "__main__":
    main()
