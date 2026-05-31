#!/usr/bin/env python3
"""UrbanPadel (4 centres IDF) — backend my.urbansoccer.fr / myurban.fr.

Architecture découverte par reverse-engineering du bundle JS :
- Endpoint centres : GET /api/read/us/centers (liste complète)
- Endpoint dispos  : POST /api/read/reservation/availabilities/search
   - header `activity: 2` (=Padel ; 1=Soccer, 3/4 autres sports)
   - form-data : centerId, periodStart (ISO), categories=[7], durations=[60,90,120]
- Renvoie une liste de créneaux avec price (€), resourceType, start/end, duration.

Centres IDF UrbanPadel :
- ID 10 : Evry-Courcouronnes        (91080)
- ID 16 : Marne la Vallée (Lognes)  (77185)
- ID 9  : Porte d'Aubervilliers     (93300)
- ID 49 : Puteaux-Île               (92800)

Comme Anybuddy, l'API renvoie uniquement les créneaux *disponibles* ; un
créneau qui DISPARAÎT entre 2 relevés (sans être passé) = réservé.

Sortie : append dans padel_idf_data.json (clés slug urbanpadel-<center>),
même structure que les clubs Anybuddy → directement consommé par
padel_idf.html.
"""
import datetime as dt
import json
import safestore
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
STORE = "padel_idf_data.json"
ORIGIN = "https://myurban.fr"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
HORIZON_JOURS = 7

# 4 centres IDF + leurs CP / addresses (extraits de /api/read/us/centers)
CENTERS = [
    {"id": 10, "slug": "urbanpadel-evry-courcouronnes", "name": "UrbanPadel Évry-Courcouronnes",
     "cp": "91080", "address": "3 avenue du bois de l'Epine, 91080 Évry-Courcouronnes"},
    {"id": 16, "slug": "urbanpadel-marne-la-vallee-lognes", "name": "UrbanPadel Marne la Vallée (Lognes)",
     "cp": "77185", "address": "29 rue de la Maison Rouge, 77185 Lognes"},
    {"id": 9, "slug": "urbanpadel-porte-aubervilliers", "name": "UrbanPadel Porte d'Aubervilliers",
     "cp": "93300", "address": "111 avenue Victor Hugo, 93300 Aubervilliers"},
    {"id": 49, "slug": "urbanpadel-puteaux-ile", "name": "UrbanPadel Puteaux-Île",
     "cp": "92800", "address": "1 allée des sports, 92800 Puteaux"},
]


def post_form(url, fields, headers):
    """POST multipart/form-data minimal (sans dépendre de requests)."""
    boundary = "------WebKitFormBoundary" + str(int(time.time() * 1000))
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n")
    parts.append(f"--{boundary}--\r\n")
    body = "".join(parts).encode("utf-8")
    h = {**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"}
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_day(center_id, day):
    """Liste les créneaux padel dispos d'un centre pour un jour donné."""
    period_start = f"{day.isoformat()}T00:00:00.000Z"
    headers = {
        "User-Agent": UA, "Accept": "application/json, text/plain, */*",
        "Origin": ORIGIN, "Referer": f"{ORIGIN}/padel/reserver/",
        "activity": "2",
    }
    fields = {
        "centerId": str(center_id),
        "periodStart": period_start,
        "categories": "[7]",
        "durations": "[60,90,120]",
    }
    try:
        data = post_form(f"{ORIGIN}/api/read/reservation/availabilities/search", fields, headers)
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} center {center_id} day {day}: {e.read()[:200].decode('utf-8','ignore')}", file=sys.stderr)
        return []
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ center {center_id} day {day}: {e}", file=sys.stderr)
        return []
    # Format: {"data":[200, [slots]]} ou {"data":[412, {error}]}
    payload = data.get("data") if isinstance(data, dict) else None
    if not payload or not isinstance(payload, list) or len(payload) < 2:
        return []
    status, content = payload[0], payload[1]
    if status != 200 or not isinstance(content, list):
        return []
    return content


def capture_center(center, store):
    """Capture les créneaux d'un centre UrbanPadel et fusionne dans store[slug]."""
    now = dt.datetime.now(PARIS)
    today = now.date()
    slots = []
    for off in range(HORIZON_JOURS):
        d = today + dt.timedelta(days=off)
        slots.extend(fetch_day(center["id"], d))

    bucket = store.setdefault(center["slug"], {
        "meta": {"slug": center["slug"], "name": center["name"], "cp": center["cp"],
                 "address": center["address"], "source": "urbanpadel"},
        "sessions": {},
    })
    bucket["meta"]["slug"] = center["slug"]
    bucket["meta"]["cp"] = center["cp"]
    bucket["meta"]["source"] = "urbanpadel"
    sessions = bucket["sessions"]

    # Marque tous les non-passés non-vus comme potentiellement réservés
    for sid, v in sessions.items():
        if v.get("finie") or v.get("source") != "urbanpadel":
            continue
        try:
            sdt = dt.datetime.fromisoformat(v["start"]).replace(tzinfo=PARIS)
        except Exception:
            continue
        if sdt > now:
            v["vu_dispo_ce_passage"] = False

    seen = 0
    for s in slots:
        start_raw = s.get("start")
        if not start_raw: continue
        try:
            sdt = dt.datetime.fromisoformat(start_raw).replace(tzinfo=PARIS)
        except Exception:
            continue
        duration = int(s.get("duration") or 60)
        edt = sdt + dt.timedelta(minutes=duration)
        rt = s.get("resourceType")
        rt_display = s.get("resourceTypeDisplay") or f"Padel {rt}"
        sid = f"{start_raw}|{rt}|{duration}"
        prev = sessions.get(sid, {})
        sessions[sid] = {
            "id": sid,
            "start": start_raw,
            "date": sdt.date().isoformat(),
            "jour": JOURS_FR[sdt.weekday()],
            "heure": sdt.strftime("%H:%M"),
            "fin": edt.strftime("%H:%M"),
            "terrain": rt_display,
            "court_id": str(rt),
            "duree": duration,
            "prix": s.get("price") or prev.get("prix"),
            "vu_dispo": True,
            "vu_dispo_ce_passage": True,
            "premier_vu": prev.get("premier_vu") or now.strftime("%Y-%m-%d %H:%M"),
            "dernier_vu": now.strftime("%Y-%m-%d %H:%M"),
            "finie": now >= edt,
            "statut": prev.get("statut") or "disponible",
            "releve": now.strftime("%Y-%m-%d %H:%M"),
            "source": "urbanpadel",
        }
        seen += 1

    booked = 0
    for sid, v in sessions.items():
        if v.get("source") != "urbanpadel": continue
        try:
            sdt = dt.datetime.fromisoformat(v["start"]).replace(tzinfo=PARIS)
            edt = sdt + dt.timedelta(minutes=int(v.get("duree") or 60))
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
    store = safestore.load(STORE)
    print(f"Scrape UrbanPadel : {len(CENTERS)} centres IDF, horizon {HORIZON_JOURS}j")
    t0 = time.time()
    total_seen = total_booked = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(capture_center, c, store): c for c in CENTERS}
        for f in as_completed(futs):
            c = futs[f]
            try:
                seen, booked = f.result()
                total_seen += seen
                total_booked += booked
                print(f"  ✅ {c['slug']:<45} {seen:>4} créneaux, {booked:>2} disparus")
            except Exception as e:  # noqa: BLE001
                print(f"  ❌ {c['slug']} : {e}", file=sys.stderr)
    safestore.save(store, STORE)
    dur = time.time() - t0
    print(f"\n{dt.datetime.now(PARIS):%Y-%m-%d %H:%M} : {total_seen} créneaux vus ({total_booked} disparus) "
          f"sur {len(CENTERS)} centres UrbanPadel en {dur:.1f}s")


if __name__ == "__main__":
    main()
