#!/usr/bin/env python3
"""Engine UrbanSoccer — backend myurban.fr (groupe Soccer 5 France).

« Urban Football » n'existe plus : urbanfootball.fr est mort (certificat
invalide + 404), ce qui explique les 5 marques `urban_football_*` abandonnées
par la discovery. La marque vivante est UrbanSoccer, et son backend est
EXACTEMENT celui que urbanpadel_scrape.py exploite déjà pour le padel — seul
l'en-tête `activity` change :

    activity: 1 = Soccer      activity: 2 = Padel

    Catalogue : GET  https://myurban.fr/api/read/us/centers
    Créneaux  : POST https://myurban.fr/api/read/reservation/availabilities/search
                     multipart/form-data : centerId, periodStart, categories,
                     durations   (l'API refuse le JSON)

Cet engine est donc le pendant « foot » d'urbanpadel_scrape.py, généralisé :
la catégorie n'est plus câblée en dur ([7] = padel) mais lue dans le catalogue
du centre. C'est nécessaire — les ids de catégorie ne sont PAS universels :
« Foot à 3 » vaut 3 à Puteaux-République alors que la doc d'origine annonçait
4. Toute valeur en dur finirait par sonder la mauvaise catégorie.

MODÈLE DE DONNÉES — disponibilités, comme Anybuddy
--------------------------------------------------
Contrairement à Le Five, l'API ne rend QUE le disponible : un créneau complet
n'apparaît pas du tout. On ne peut donc pas observer l'occupation directement,
seulement la déduire — un créneau qui DISPARAÎT entre deux relevés sans être
passé a été réservé (logique vu_dispo / vu_dispo_ce_passage, portée par
pilates_extension_scrape.store_court_slots).

`count` = nombre de terrains encore libres sur le créneau.

Coût mesuré : ~1,5 s par (centre, jour). 11 centres IDF × 7 jours ≈ 2 min.

Usage :
    python3 engine_urbansoccer.py                  # catalogue IDF
    python3 engine_urbansoccer.py <clé|id> [jours] # créneaux normalisés
"""
import datetime as dt
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ORIGIN = "https://myurban.fr"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0 Safari/537.36")
CP_IDF = ("75", "77", "78", "91", "92", "93", "94", "95")

ACTIVITY_SOCCER = "1"
ACTIVITY_PADEL = "2"
DURATIONS_DEFAULT = (60,)
# Même levier d'exploitation que côté Le Five, par symétrie — mais ici le
# balayage complet coûte ~1 min pour les 11 centres : il n'y a pas de raison
# de le réduire.
HORIZON_DEFAULT = max(1, int(os.environ.get("URBANSOCCER_HORIZON_DAYS") or 7))
RETRIES = 3
WORKERS = 4
TIMEOUT = 45


class UrbanSoccerError(Exception):
    """Centre introuvable, catégorie absente, ou API muette."""


# Registre des 11 centres IDF, par clé de marque.
# Les 5 slugs `urban_football_*` du catalogue d'origine (montrouge,
# chevilly-larue, chevreuse, montigny, pierrefitte) ne correspondent à aucun
# centre UrbanSoccer existant : ce sont des adresses de l'ancienne enseigne.
SOURCES = {
    "urbansoccer_marne_la_vallee": {"center_id": 16, "sport": "Foot à 5"},
    "urbansoccer_guyancourt":      {"center_id": 14, "sport": "Foot à 5"},
    "urbansoccer_evry":            {"center_id": 10, "sport": "Foot à 5"},
    "urbansoccer_orsay":           {"center_id": 4,  "sport": "Foot à 5"},
    "urbansoccer_la_defense":      {"center_id": 12, "sport": "Foot à 5"},
    "urbansoccer_asnieres":        {"center_id": 7,  "sport": "Foot à 5"},
    "urbansoccer_meudon":          {"center_id": 3,  "sport": "Foot à 5"},
    "urbansoccer_puteaux_ile":     {"center_id": 49, "sport": "Foot à 5"},
    "urbansoccer_puteaux_republique": {"center_id": 2, "sport": "Foot à 5"},
    "urbansoccer_porte_aubervilliers": {"center_id": 9, "sport": "Foot à 5"},
    "urbansoccer_quai_ivry":       {"center_id": 13, "sport": "Foot à 5"},
}

_catalog_cache = None
_catalog_lock = threading.Lock()


# ------------------------------------------------------------------ transport
def _send(req):
    last = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise UrbanSoccerError(f"{req.full_url} : échec après {RETRIES} essais — {last}")


def _get(url):
    return _send(urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json, text/plain, */*",
        "Origin": ORIGIN, "Referer": ORIGIN + "/"}))


def _post_form(url, fields, headers):
    """multipart/form-data minimal — myurban.fr refuse le JSON."""
    b = "------WebKitFormBoundary" + str(int(time.time() * 1000))
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
             for k, v in fields.items()]
    parts.append(f"--{b}--\r\n")
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
         "Origin": ORIGIN, "Content-Type": f"multipart/form-data; boundary={b}"}
    h.update(headers)
    return _send(urllib.request.Request(
        url, data="".join(parts).encode(), headers=h, method="POST"))


# ------------------------------------------------------------------- catalogue
def catalog(idf_only=True, refresh=False):
    """Centres UrbanSoccer. Mis en cache : 11 marques = 1 seul fetch.

    Retour : [{id, name, cp, address, lat, lon, categories:{nom: id}}]
    Les ids de `categories` sont propres au centre — ne jamais les câbler.
    """
    global _catalog_cache
    with _catalog_lock:
        if _catalog_cache is None or refresh:
            d = _get(f"{ORIGIN}/api/read/us/centers")
            rows = []
            for c in d.get("data", []):
                addr = c.get("address") or ""
                m = re.search(r"\b(\d{5})\b", addr)
                cats = {}
                for rt in c.get("resourceTypes") or []:
                    for ct in rt.get("categories") or []:
                        if ct.get("name"):
                            cats[ct["name"]] = ct.get("id")
                rows.append({"id": c.get("id"), "name": c.get("name"),
                             "cp": m.group(1) if m else "", "address": addr,
                             "lat": c.get("latitude"), "lon": c.get("longitude"),
                             "categories": cats})
            _catalog_cache = rows
    rows = _catalog_cache
    if idf_only:
        rows = [c for c in rows if c["cp"][:2] in CP_IDF]
    return sorted(rows, key=lambda x: x["cp"])


def resolve(ident, sport=None):
    """(centre, sport, category_id) depuis une clé de marque, un id, ou un dict."""
    src = {}
    if isinstance(ident, dict):
        src = {k: ident[k] for k in ("center_id", "sport") if ident.get(k)}
    elif ident in SOURCES:
        src = dict(SOURCES[ident])
    else:
        try:
            src = {"center_id": int(str(ident).strip())}
        except (TypeError, ValueError):
            raise UrbanSoccerError(
                f"identifiant UrbanSoccer inconnu : {ident!r} — attendu une clé "
                f"de SOURCES ou un center_id")
    cid = src.get("center_id")
    if cid is None:
        raise UrbanSoccerError(f"pas de center_id pour {ident!r}")
    cid = int(cid)
    for c in catalog(idf_only=False):
        if c["id"] == cid:
            want = sport or src.get("sport") or "Foot à 5"
            if want not in c["categories"]:
                raise UrbanSoccerError(
                    f"centre {cid} ({c['name']}) ne propose pas « {want} » — "
                    f"catégories : {sorted(c['categories'])}")
            return c, want, c["categories"][want]
    raise UrbanSoccerError(f"center_id {cid} absent du catalogue UrbanSoccer")


# --------------------------------------------------------------------- fetch
def raw_day(center_id, day, category_id, durations=DURATIONS_DEFAULT,
            activity=ACTIVITY_SOCCER):
    """Créneaux disponibles bruts d'un (centre, jour, catégorie)."""
    d = _post_form(
        f"{ORIGIN}/api/read/reservation/availabilities/search",
        {"centerId": str(int(center_id)),
         "periodStart": f"{day}T00:00:00.000Z",
         "categories": f"[{int(category_id)}]",
         "durations": "[" + ",".join(str(int(x)) for x in durations) + "]"},
        {"Referer": f"{ORIGIN}/reserver/", "activity": activity})
    # Enveloppe maison : {"data": [200, [slots]]} — ou [412, {erreur}].
    payload = d.get("data") if isinstance(d, dict) else None
    if not isinstance(payload, list) or len(payload) < 2 or payload[0] != 200:
        return []
    return payload[1] if isinstance(payload[1], list) else []


def _normalize(center, sport, slots):
    out = []
    for s in slots:
        start = s.get("start")
        if not start:
            continue
        duration = int(s.get("duration") or 60)
        rt = s.get("resourceType")
        out.append({
            "source": "urbansoccer",
            # resourceType dans la clé : un même (début, durée) peut exister en
            # Intérieur ET en Extérieur, ce sont deux offres distinctes.
            "id": f"urbansoccer-{center['id']}-{rt}-{start}-{duration}",
            "center_id": center["id"],
            "center_name": center["name"],
            "cp": center["cp"],
            "city": None,
            "sport": sport,
            "start": start,
            "end": s.get("end"),
            "duration": duration,
            # `count` = terrains encore libres. L'API ne rend que du
            # disponible : il n'y a jamais de 0 ici (cf. en-tête).
            "free_courts": s.get("count"),
            "courts": [],
            "court_type": s.get("resourceTypeDisplay"),
            "price": s.get("price"),
        })
    return out


def fetch_slots(ident, days=HORIZON_DEFAULT, sport=None,
                durations=DURATIONS_DEFAULT, start_date=None,
                activity=ACTIVITY_SOCCER):
    """Créneaux normalisés d'un centre sur `days` jours à partir d'aujourd'hui.

    Même contrat que engine_lefive.fetch_slots : les jours en échec sont
    signalés et sautés, un échec total lève UrbanSoccerError.
    """
    center, sport, cat = resolve(ident, sport)
    d0 = start_date or dt.date.today()
    wanted = [(d0 + dt.timedelta(days=i)).isoformat() for i in range(days)]

    rows, failed = [], []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(raw_day, center["id"], day, cat, durations, activity): day
                for day in wanted}
        for fut in as_completed(futs):
            day = futs[fut]
            try:
                rows.extend(_normalize(center, sport, fut.result()))
            except UrbanSoccerError as e:
                failed.append(day)
                print(f"  ⚠️  urbansoccer {center['name']} {day} : {e}",
                      file=sys.stderr)
    if failed and len(failed) == len(wanted):
        raise UrbanSoccerError(f"{center['name']} : aucun jour récupéré "
                               f"({len(failed)} échecs)")
    rows.sort(key=lambda r: (r["start"], r["duration"]))
    return rows


# ---------------------------------------------------------------------- main
def main():
    if len(sys.argv) < 2:
        print("Catalogue UrbanSoccer IDF (GET /api/read/us/centers)\n")
        for c in catalog():
            keys = [k for k, v in SOURCES.items() if v["center_id"] == c["id"]]
            print(f"  id={c['id']:<4} {c['name'][:24]:<24} {c['cp']} "
                  f"{sorted(c['categories'])}  {'/'.join(keys) or '— pas de clé'}")
        return
    ident = sys.argv[1]
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    rows = fetch_slots(ident, days=days)
    libres = sum(r["free_courts"] or 0 for r in rows)
    print(f"{ident} : {len(rows)} créneaux sur {days} j — {libres} terrains libres")
    for r in rows[:3]:
        print("   ", json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
