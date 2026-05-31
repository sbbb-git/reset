#!/usr/bin/env python3
"""Padel Île-de-France — scrape multi-clubs via Anybuddy (59 clubs).

Architecture optimisée pour 59 clubs : un seul script, un seul fichier de
sortie agrégé `padel_idf_data.json` (structure {slug: {meta, sessions}}),
et un dashboard agrégé `padel_idf.html` (rankings, heatmap, comparaison
inter-clubs). Pas de HTML par club.

Stratégie identique au scraper Anybuddy mono-club :
- Anybuddy renvoie uniquement les créneaux DISPONIBLES (slug + jour fixés).
- On accumule chaque relevé, et un créneau qui DISPARAÎT alors qu'il n'est
  pas encore passé = réservé (statut "booked").
- Permet de reconstruire le taux d'occupation par jour / créneau / terrain.

Source clubs : padel_idf_clubs.json (généré par le notebook de recon ;
classification par code postal via /api/v1/clubs/{slug}).
"""
import csv
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

PARIS = ZoneInfo("Europe/Paris")
ORIGIN = "https://www.anybuddyapp.com"
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
HORIZON_JOURS = 7
STORE = "padel_idf_data.json"
CSV_PATH = "padel_idf_creneaux.csv"
CLUBS_FILE = "padel_idf_clubs.json"
MAX_WORKERS = 8  # parallélisme HTTP raisonnable


def _get(url, retries=4):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "application/json",
                "Referer": f"{ORIGIN}/fr/club/"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 ** attempt)
    raise RuntimeError(f"echec {url}: {last}")


def fetch_day(slug, day, activity="padel"):
    params = urllib.parse.urlencode({
        "clubSlug": slug,
        "dateFrom": f"{day.isoformat()}T00:00",
        "dateTo": f"{day.isoformat()}T23:59",
        "activity": activity,
    })
    return _get(f"{ORIGIN}/api/v1/availabilities?{params}")


def fetch_club(slug):
    """Renvoie toutes les offres dispos sur l'horizon, pour padel."""
    today = dt.date.today()
    all_offers = []
    for off in range(HORIZON_JOURS):
        d = today + dt.timedelta(days=off)
        try:
            data = fetch_day(slug, d, "padel")
        except Exception as e:  # noqa: BLE001
            print(f"  ({slug} jour {d} indispo : {e})", file=sys.stderr)
            continue
        for entry in data.get("data") or []:
            sdt = entry.get("startDateTime")
            for svc in entry.get("services") or []:
                all_offers.append({
                    "start": sdt,
                    "duration": svc.get("duration") or 60,
                    "court_id": svc.get("id") or svc.get("uuid"),
                    "court_name": svc.get("name") or svc.get("courtName") or "",
                    "price": (svc.get("price") or 0) / 100.0 if svc.get("price") else None,
                    "available": svc.get("availablePlaces") or svc.get("totalCapacity") or 4,
                    "capacity": svc.get("totalCapacity") or 4,
                })
    return all_offers


def capture_club(slug, club_meta, store):
    """Capture les créneaux d'un club et fusionne dans store[slug]['sessions']."""
    now = dt.datetime.now(PARIS)
    try:
        offers = fetch_club(slug)
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ {slug} : {e}", file=sys.stderr)
        return 0, 0
    bucket = store.setdefault(slug, {"meta": club_meta, "sessions": {}})
    bucket["meta"] = club_meta
    sessions = bucket["sessions"]
    today_iso = now.date().isoformat()
    # Marque tous les créneaux non-passés comme "potentiellement réservés" par défaut
    for sid, v in sessions.items():
        if v.get("finie"):
            continue
        try:
            sdt = dt.datetime.fromisoformat(v["start"]).replace(tzinfo=PARIS)
        except Exception:
            continue
        if sdt > now:
            v["vu_dispo_ce_passage"] = False
    # Maintenant on met à true ceux qui sont visibles dans l'API
    seen = 0
    for o in offers:
        sdt_raw = o["start"]
        try:
            sdt = dt.datetime.fromisoformat(sdt_raw).replace(tzinfo=PARIS) if "T" in sdt_raw else None
        except Exception:
            continue
        if not sdt:
            continue
        court_id = o["court_id"] or "noid"
        sid = f"{sdt_raw}|{court_id}|{o['duration']}"
        edt = sdt + dt.timedelta(minutes=int(o["duration"]))
        prev = sessions.get(sid, {})
        sessions[sid] = {
            "id": sid,
            "start": sdt_raw,
            "date": sdt.date().isoformat(),
            "jour": JOURS_FR[sdt.weekday()],
            "heure": sdt.strftime("%H:%M"),
            "fin": edt.strftime("%H:%M"),
            "terrain": o["court_name"] or f"Terrain {court_id[:8]}",
            "court_id": court_id,
            "duree": o["duration"],
            "prix": o["price"] or prev.get("prix"),
            "vu_dispo": True,
            "vu_dispo_ce_passage": True,
            "premier_vu": prev.get("premier_vu") or now.strftime("%Y-%m-%d %H:%M"),
            "dernier_vu": now.strftime("%Y-%m-%d %H:%M"),
            "finie": now >= edt,
            "statut": prev.get("statut") or "disponible",
            "releve": now.strftime("%Y-%m-%d %H:%M"),
        }
        seen += 1
    # Statut final : si vu_dispo_ce_passage=false et créneau pas encore passé et qu'on l'avait
    # déjà vu dispo avant -> "reserve" (puis figé).
    booked = 0
    for sid, v in sessions.items():
        try:
            sdt = dt.datetime.fromisoformat(v["start"]).replace(tzinfo=PARIS)
            edt = sdt + dt.timedelta(minutes=int(v.get("duree") or 60))
        except Exception:
            continue
        if now >= edt:
            v["finie"] = True
        # disparition = réservé
        if not v.get("finie") and not v.get("vu_dispo_ce_passage") and v.get("vu_dispo"):
            if v.get("statut") == "disponible":
                v["statut"] = "reserve"
                booked += 1
    return seen, booked


def main():
    # Lecture du catalogue clubs IDF
    if not os.path.exists(CLUBS_FILE):
        print(f"❌ {CLUBS_FILE} introuvable", file=sys.stderr)
        sys.exit(1)
    clubs = json.load(open(CLUBS_FILE, encoding="utf-8"))
    store = safestore.load(STORE)
    print(f"Scrape padel IDF : {len(clubs)} clubs, parallélisme x{MAX_WORKERS}")
    t0 = time.time()
    total_seen = total_booked = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(capture_club, c["slug"], c, store): c for c in clubs}
        for f in as_completed(futs):
            c = futs[f]
            try:
                seen, booked = f.result()
                total_seen += seen
                total_booked += booked
                if seen:
                    print(f"  ✅ {c['slug']:<55} {seen:>3} créneaux, {booked:>2} disparus (réservés)")
            except Exception as e:  # noqa: BLE001
                print(f"  ❌ {c['slug']} : {e}", file=sys.stderr)
    safestore.save(store, STORE)
    dur = time.time() - t0
    n_clubs = len(store)
    n_total_sessions = sum(len(v.get("sessions", {})) for v in store.values())
    n_finies = sum(1 for v in store.values() for s in v.get("sessions", {}).values() if s.get("finie"))
    print(f"\n{dt.datetime.now(PARIS):%Y-%m-%d %H:%M} : {total_seen} créneaux vus ({total_booked} disparus) "
          f"sur {len(clubs)} clubs en {dur:.1f}s")
    print(f"Store : {n_clubs} clubs, {n_total_sessions} sessions ({n_finies} figées).")

    # CSV aggrégé
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["club_slug", "club_nom", "cp", "date", "jour", "heure", "fin",
                    "terrain", "duree", "prix", "statut", "finie", "premier_vu", "dernier_vu"])
        for slug, bucket in sorted(store.items()):
            meta = bucket.get("meta", {})
            cp = meta.get("cp", "")
            nom = slug.replace("-", " ").title()
            for s in sorted(bucket.get("sessions", {}).values(), key=lambda x: (x.get("date",""), x.get("heure",""))):
                w.writerow([slug, nom, cp, s.get("date",""), s.get("jour",""),
                            s.get("heure",""), s.get("fin",""), s.get("terrain",""),
                            s.get("duree",""), s.get("prix",""), s.get("statut",""),
                            "oui" if s.get("finie") else "non",
                            s.get("premier_vu",""), s.get("dernier_vu","")])
    print(f"-> {CSV_PATH}")


if __name__ == "__main__":
    main()
