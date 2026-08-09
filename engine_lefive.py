#!/usr/bin/env python3
"""Engine Le Five — backend propriétaire « SPLF » (groupe Players).

Le Five N'EST PAS sur Doinsport, contrairement à ce que supposait
urbanfoot_extension_brands.json (`platform_guess: doinsport`). Le groupe a son
propre backend, api-front.lefive.fr, et il expose ses créneaux SANS AUCUNE
AUTHENTIFICATION :

    Catalogue : GET  https://www.lefive.fr/content/centers.json
    Créneaux  : POST https://api-front.lefive.fr/splf/v1/bookingrules/allFields
                     ?appId=1&isChannelWeb=true

(À noter : GET /splf/v1/centers, lui, exige un Bearer — 401. C'est
`bookingrules/allFields` qui est ouvert, pas toute l'API.)

MODÈLE DE DONNÉES — terrain réservable, PAS cours collectif
-----------------------------------------------------------
La réponse est la GRILLE COMPLÈTE du jour : un créneau dont plus aucun terrain
n'est libre est renvoyé quand même, avec `fields: []`. C'est plus riche que
UrbanSoccer / Anybuddy, qui n'annoncent que le disponible :

  · `fields` non vide → terrains encore libres, avec leur nom et leur prix ;
  · `fields` vide     → créneau COMPLET, observé directement (pas déduit d'une
                        disparition entre deux relevés).

On remonte donc les deux, `free_courts=0` inclus : c'est le seul signal
d'occupation réellement mesuré de tout le lot foot/basket.

DEUX PIÈGES VÉRIFIÉS SUR LE FIL
-------------------------------
1. `startingDate` / `endingDate` sont des heures LOCALES Paris suffixées d'un
   "Z" mensonger. Sur le même créneau l'API renvoie startingDate=10:00Z et
   startingDateZuluTime=08:00Z — c'est 10h00 à Paris. On retire donc le Z et on
   traite la valeur comme un ISO naïf local, homogène avec UrbanSoccer.
2. La réponse embarque l'objet `center` complet dans CHAQUE terrain de CHAQUE
   créneau. D'où le poids : 281 Ko par (centre, jour) avec durations=60,90,120,
   contre 108 Ko avec durations=60 seul (mesuré sur Paris 17). D'où
   DURATIONS_DEFAULT = "60" : la grille 60 min au pas de 30 min suffit à
   mesurer l'occupation, et les prix 90/120 min sont proportionnels.

Coût mesuré : ~5,5 s par (centre, jour) en durations=60. 13 centres IDF × 7
jours = 91 requêtes ; d'où le parallélisme volontairement bas (LE FIVE tousse
— 502/503 — quand on balaie ses 31 centres trop vite) et le retry.

Usage :
    python3 engine_lefive.py              # catalogue IDF + sports
    python3 engine_lefive.py <clé|id> [jours]   # créneaux normalisés
"""
import datetime as dt
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

API = "https://api-front.lefive.fr/splf/v1"
CATALOG_URL = "https://www.lefive.fr/content/centers.json"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0 Safari/537.36")
CP_IDF = ("75", "77", "78", "91", "92", "93", "94", "95")

# Grille 60 min au pas de 30 min : suffisante pour l'occupation, 2,6× plus
# légère que 60,90,120 (cf. en-tête).
DURATIONS_DEFAULT = "60"
# 13 centres IDF × 7 jours = 12 à 14 min de run mesurés : l'API ralentit
# nettement sous balayage soutenu (5,5 s pour un appel isolé, ~17 s en série).
# C'est le poste le plus lourd de extensions-scrape.yml, d'où ce levier
# d'exploitation : LEFIVE_HORIZON_DAYS=4 ramène le run sous 8 min sans toucher
# au code, si le budget du workflow devient trop juste.
HORIZON_DEFAULT = max(1, int(os.environ.get("LEFIVE_HORIZON_DAYS") or 7))
RETRIES = 3
WORKERS = 2          # Le Five renvoie des 502/503 si on le balaie trop vite
TIMEOUT = 90


class LeFiveError(Exception):
    """Le centre est introuvable, ou l'API n'a rien rendu d'exploitable."""


# Registre des centres IDF réellement existants, par clé de marque.
# Établi depuis le catalogue officiel (GET /content/centers.json) — les slugs
# du catalogue d'origine (bercy, clichy, arcueil, thiais, villepinte,
# porte-de-saint-cloud, marne-la-vallee) ne correspondent à AUCUN centre ouvert.
SOURCES = {
    # --- foot à 5 (sportType 1)
    "le_five_paris_13":            {"center_id": 51,  "sport": "Foot"},
    "le_five_paris_17":            {"center_id": 63,  "sport": "Foot"},
    "le_five_paris_18":            {"center_id": 69,  "sport": "Foot"},
    "le_five_carrieres_sous_poissy": {"center_id": 4, "sport": "Foot"},
    "le_five_morangis":            {"center_id": 59,  "sport": "Foot"},
    "le_five_colombes":            {"center_id": 107, "sport": "Foot"},
    "le_five_bobigny":             {"center_id": 19,  "sport": "Foot"},
    "le_five_montreuil":           {"center_id": 73,  "sport": "Foot"},
    "le_five_marville":            {"center_id": 65,  "sport": "Foot"},
    # « Le Five Villette » est l'enseigne du centre situé 25 rue Sadi Carnot,
    # 93300 Aubervilliers : c'est bien la marque `le_five_aubervilliers` du
    # catalogue d'origine (même CP, même commune), sous son nom réel.
    "le_five_aubervilliers":       {"center_id": 39,  "sport": "Foot"},
    "le_five_creteil":             {"center_id": 25,  "sport": "Foot"},
    "le_five_champigny":           {"center_id": 5,   "sport": "Foot"},
    "le_five_bezons":              {"center_id": 18,  "sport": "Foot"},
    # --- basket (sportType 17) : un seul centre en IDF
    "le_five_basket_carrieres":    {"center_id": 4,   "sport": "Basket"},
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
    raise LeFiveError(f"{req.full_url} : échec après {RETRIES} essais — {last}")


def _get(url, headers=None):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    return _send(urllib.request.Request(url, headers=h))


def _post(url, body):
    h = {"User-Agent": UA, "Content-Type": "application/json",
         "Accept": "application/json", "Origin": "https://www.lefive.fr",
         "Referer": "https://www.lefive.fr/"}
    return _send(urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=h, method="POST"))


# ------------------------------------------------------------------- catalogue
def catalog(idf_only=True, refresh=False):
    """Centres Le Five actifs. Mis en cache : 24 marques = 1 seul fetch.

    Retour : [{id, name, cp, city, street, lat, lon, sports:{nom: sportType_id}}]
    """
    global _catalog_cache
    with _catalog_lock:
        if _catalog_cache is None or refresh:
            d = _get(CATALOG_URL)
            rows = []
            for c in d.get("centers", []):
                if not c.get("isActive"):
                    continue
                a = c.get("address") or {}
                sports = {}
                for s in c.get("center_sportTypes") or []:
                    t = s.get("sportType") or {}
                    if t.get("name") and t.get("isActive", True):
                        sports[t["name"]] = t.get("id")
                rows.append({
                    "id": c.get("id"), "name": c.get("name"),
                    "cp": str(a.get("postalCode") or ""), "city": a.get("city"),
                    "street": a.get("street"), "lat": c.get("latitude"),
                    "lon": c.get("longitude"), "sports": sports,
                })
            _catalog_cache = rows
    rows = _catalog_cache
    if idf_only:
        rows = [c for c in rows if c["cp"][:2] in CP_IDF]
    return sorted(rows, key=lambda x: x["cp"])


def resolve(ident, sport=None):
    """(centre, sport) depuis une clé de marque, un center_id, ou un dict resolved.

    Accepte aussi bien "le_five_creteil" que 25, que {"center_id": 25}.
    """
    src = {}
    if isinstance(ident, dict):
        src = {k: ident[k] for k in ("center_id", "sport") if ident.get(k)}
    elif ident in SOURCES:
        src = dict(SOURCES[ident])
    else:
        try:
            src = {"center_id": int(str(ident).strip())}
        except (TypeError, ValueError):
            raise LeFiveError(f"identifiant Le Five inconnu : {ident!r} — "
                              f"attendu une clé de SOURCES ou un center_id")
    cid = src.get("center_id")
    if cid is None:
        raise LeFiveError(f"pas de center_id pour {ident!r}")
    cid = int(cid)
    for c in catalog(idf_only=False):
        if c["id"] == cid:
            want = sport or src.get("sport") or "Foot"
            if want not in c["sports"]:
                raise LeFiveError(
                    f"centre {cid} ({c['name']}) ne propose pas « {want} » — "
                    f"sports disponibles : {sorted(c['sports'])}")
            return c, want
    raise LeFiveError(f"center_id {cid} absent du catalogue Le Five "
                      f"(centre fermé ou identifiant erroné)")


# --------------------------------------------------------------------- fetch
def _local_iso(value):
    """Heure locale Paris : l'API la suffixe d'un « Z » mensonger (cf. en-tête)."""
    return (value or "").rstrip("Z") or None


def raw_day(center_id, day, sport_id, durations=DURATIONS_DEFAULT, capacity=10):
    """Grille brute d'un (centre, jour) telle que rendue par l'API."""
    body = {
        "startingDateZuluTime": f"{day}T00:00:00Z",
        "endingDateZuluTime": f"{day}T23:59:00Z",
        "durations": durations,
        "capacity": capacity,
        "center_id": int(center_id),
        "bookingType_id": "1",
        "sportType_id": str(sport_id),
        "isChannelWeb": True,
        "computePriceWithDefaultCapaIfNoCapa": True,
    }
    d = _post(f"{API}/bookingrules/allFields?appId=1&isChannelWeb=true", body)
    return d if isinstance(d, list) else []


def _normalize(center, sport, sport_id, slots):
    out = []
    for s in slots:
        start = _local_iso(s.get("startingDate"))
        if not start:
            continue
        fields = s.get("fields") or []
        prices = [f.get("webPrice") for f in fields
                  if isinstance(f.get("webPrice"), (int, float))]
        types = sorted({(f.get("fieldType") or {}).get("name")
                        for f in fields if (f.get("fieldType") or {}).get("name")})
        duration = int(s.get("duration") or 60)
        out.append({
            "source": "lefive",
            "id": f"lefive-{center['id']}-{sport_id}-{start}-{duration}",
            "center_id": center["id"],
            "center_name": center["name"],
            "cp": center["cp"],
            "city": center["city"],
            "sport": sport,
            "start": start,
            "end": _local_iso(s.get("endingDate")),
            "duration": duration,
            # 0 = créneau COMPLET, directement observé (la grille renvoie le
            # créneau même quand plus aucun terrain n'est libre).
            "free_courts": len(fields),
            "courts": [f.get("name") for f in fields if f.get("name")],
            "court_type": " / ".join(types) if types else None,
            "price": min(prices) if prices else None,
        })
    return out


def fetch_slots(ident, days=HORIZON_DEFAULT, sport=None,
                durations=DURATIONS_DEFAULT, start_date=None):
    """Créneaux normalisés d'un centre sur `days` jours à partir d'aujourd'hui.

    Les jours en échec sont signalés sur stderr et sautés ; si TOUS les jours
    échouent, on lève LeFiveError plutôt que de rendre une grille vide qui
    passerait pour « centre fermé ».
    """
    center, sport = resolve(ident, sport)
    sport_id = center["sports"][sport]
    d0 = start_date or dt.date.today()
    wanted = [(d0 + dt.timedelta(days=i)).isoformat() for i in range(days)]

    rows, failed = [], []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(raw_day, center["id"], day, sport_id, durations): day
                for day in wanted}
        for fut in as_completed(futs):
            day = futs[fut]
            try:
                rows.extend(_normalize(center, sport, sport_id, fut.result()))
            except LeFiveError as e:
                failed.append(day)
                print(f"  ⚠️  lefive {center['name']} {day} : {e}", file=sys.stderr)
    if failed and len(failed) == len(wanted):
        raise LeFiveError(f"{center['name']} : aucun jour récupéré "
                          f"({len(failed)} échecs)")
    rows.sort(key=lambda r: (r["start"], r["duration"]))
    return rows


# ---------------------------------------------------------------------- main
def main():
    if len(sys.argv) < 2:
        print("Catalogue Le Five IDF (GET /content/centers.json)\n")
        for c in catalog():
            keys = [k for k, v in SOURCES.items() if v["center_id"] == c["id"]]
            print(f"  id={c['id']:<4} {c['name'][:24]:<24} {c['cp']} "
                  f"{(c['city'] or '')[:20]:<20} {sorted(c['sports'])}  "
                  f"{'/'.join(keys) or '— pas de clé de marque'}")
        return
    ident = sys.argv[1]
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    rows = fetch_slots(ident, days=days)
    libres = sum(r["free_courts"] for r in rows)
    complets = sum(1 for r in rows if r["free_courts"] == 0)
    print(f"{ident} : {len(rows)} créneaux sur {days} j — {libres} terrains "
          f"libres, {complets} créneaux complets")
    for r in rows[:5]:
        print("   ", json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
