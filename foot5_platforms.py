#!/usr/bin/env python3
"""Plateformes de booking des centres Foot 5 / Basket indoor IDF.

Résultat du reverse-engineering des 44 centres 'gave_up' de
urbanfoot_extension_brands.json et hoops_extension_brands.json.

CE QUE LA DISCOVERY CHERCHAIT AU MAUVAIS ENDROIT
------------------------------------------------
Les détecteurs de pilates_extension_discover.py (bsport / mindbody / arketa /
clubready / resamania / doinsport) ne matchaient pas — mais ce n'est pas la
faute des détecteurs : 34 des 44 URLs du catalogue sont mortes (404, domaine
expiré, DNS inexistant) ou pointent vers des centres qui n'existent pas.
Aucun détecteur ne peut signer une page 404.

Les 2 chaînes réellement scrapables tournent sur leur backend PROPRIÉTAIRE,
et les deux exposent leurs créneaux SANS AUCUNE AUTHENTIFICATION :

1) LE FIVE  (13 centres IDF)  — backend maison "SPLF" (groupe Players)
   Catalogue : GET  https://www.lefive.fr/content/centers.json
   Créneaux  : POST https://api-front.lefive.fr/splf/v1/bookingrules/allFields
                    ?appId=1&isChannelWeb=true

2) URBANSOCCER (11 centres IDF) — backend myurban.fr
   /!\\ "Urban Football" n'existe plus : le domaine urbanfootball.fr est mort
   (certificat invalide + 404). La marque est UrbanSoccer, groupe Soccer 5
   France (Compagnie des Alpes). C'est EXACTEMENT le backend déjà utilisé par
   urbanpadel_scrape.py — seul le header `activity` change (1=Soccer, 2=Padel).
   Catalogue : GET  https://myurban.fr/api/read/us/centers
   Créneaux  : POST https://myurban.fr/api/read/reservation/availabilities/search
                    header `activity: 1`, form-data categories=[5] (Foot à 5)

HOOPS FACTORY est identifié mais NON scrapable — voir hoops_factory_status().

ÉTAT VÉRIFIÉ LE 2026-08-09 00:0x UTC (contre-test indépendant)
--------------------------------------------------------------
  · les 2 catalogues répondent : 13 centres IDF Le Five, 11 UrbanSoccer ;
  · UrbanSoccer /availabilities/search : OK, créneaux réels (heure, durée,
    prix, nombre de terrains libres) — la chaîne est exploitable ;
  · Le Five /bookingrules/allFields : 502 puis 503 sur 4 essais espacés, alors
    que le même appel passait une heure plus tôt. Panne côté Le Five ou
    limitation déclenchée par le balayage des 31 centres — à reconfirmer
    avant de bâtir un scraper dessus, et à espacer davantage le cas échéant.

Les deux APIs renvoient les créneaux DISPONIBLES (modèle Anybuddy/UrbanPadel :
un créneau qui disparaît entre 2 relevés = réservé), pas les réservations
confirmées (modèle Doinsport).

Usage :
    python3 foot5_platforms.py            # auto-test sur J+2, résumé par centre
    python3 foot5_platforms.py --json     # dump normalisé sur stdout
"""
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0 Safari/537.36")

CP_IDF = ("75", "77", "78", "91", "92", "93", "94", "95")

LEFIVE_API = "https://api-front.lefive.fr/splf/v1"
LEFIVE_CATALOG = "https://www.lefive.fr/content/centers.json"
URBAN = "https://myurban.fr"

# sportType_id observés côté Le Five (GET centers.json → center_sportTypes)
LEFIVE_SPORTS = {"Foot": 1, "Squash": 2, "Padel": 3, "Badminton": 4,
                 "Salons": 13, "Basket": 17, "Pickleball": 23}

# categories observées côté UrbanSoccer (GET /api/read/us/centers → resourceTypes)
URBAN_CATEGORIES = {"Foot à 3": 4, "Foot à 5": 5, "Foot à 7": 6, "Padel": 7}


# --------------------------------------------------------------- transport
# L'API Le Five renvoie ~600 Ko par centre et par jour ; sur un run complet
# 1 requête sur 8 environ dépasse le délai. Sans réessai, on perd des centres
# au hasard à chaque passage — d'où le retry systématique.
RETRIES = 3


def _send(req, timeout):
    last = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"échec après {RETRIES} essais : {req.full_url} — {last}")


def _get_json(url, timeout=60, headers=None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    return _send(urllib.request.Request(url, headers=h), timeout)


def _post_json(url, body, timeout=120, headers=None):
    h = {"User-Agent": UA, "Content-Type": "application/json",
         "Accept": "application/json"}
    if headers:
        h.update(headers)
    return _send(urllib.request.Request(url, data=json.dumps(body).encode(),
                                        headers=h, method="POST"), timeout)


def _post_form(url, fields, timeout=60, headers=None):
    """multipart/form-data minimal — myurban.fr refuse le JSON."""
    b = "------WebKitFormBoundary" + str(int(time.time() * 1000))
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
             for k, v in fields.items()]
    parts.append(f"--{b}--\r\n")
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
         "Content-Type": f"multipart/form-data; boundary={b}"}
    if headers:
        h.update(headers)
    return _send(urllib.request.Request(url, data="".join(parts).encode(),
                                        headers=h, method="POST"), timeout)


# ------------------------------------------------------------------ LE FIVE
def lefive_centers(idf_only=True):
    """Catalogue Le Five. Public, sans auth, sert aussi le référentiel sports.

    Retour : [{id, name, cp, city, lat, lon, sports:{nom: sportType_id}}]
    """
    d = _get_json(LEFIVE_CATALOG, headers={"Accept": "*/*"})
    out = []
    for c in d.get("centers", []):
        if not c.get("isActive"):
            continue
        a = c.get("address") or {}
        cp = str(a.get("postalCode") or "")
        if idf_only and cp[:2] not in CP_IDF:
            continue
        sports = {}
        for s in c.get("center_sportTypes") or []:
            t = s.get("sportType") or {}
            if t.get("name") and t.get("isActive", True):
                sports[t["name"]] = t.get("id")
        out.append({"id": c.get("id"), "name": c.get("name"), "cp": cp,
                    "city": a.get("city"), "street": a.get("street"),
                    "lat": c.get("latitude"), "lon": c.get("longitude"),
                    "sports": sports})
    return sorted(out, key=lambda x: x["cp"])


def lefive_slots(center_id, day, sport_id=1, capacity=10):
    """Créneaux Le Five d'un centre pour un jour (date ISO 'YYYY-MM-DD').

    POST /splf/v1/bookingrules/allFields?appId=1&isChannelWeb=true
    Body : startingDateZuluTime / endingDateZuluTime / durations / capacity /
           center_id / bookingType_id / sportType_id / isChannelWeb /
           computePriceWithDefaultCapaIfNoCapa

    Réponse : liste de créneaux, chacun avec `fields` = terrains ENCORE LIBRES.
    Un créneau sans terrain libre n'est pas renvoyé du tout.
    """
    body = {
        "startingDateZuluTime": f"{day}T00:00:00Z",
        "endingDateZuluTime": f"{day}T23:59:00Z",
        "durations": "60,90,120",
        "capacity": capacity,
        "center_id": center_id,
        "bookingType_id": "1",
        "sportType_id": str(sport_id),
        "isChannelWeb": True,
        "computePriceWithDefaultCapaIfNoCapa": True,
    }
    return _post_json(
        f"{LEFIVE_API}/bookingrules/allFields?appId=1&isChannelWeb=true",
        body, headers={"Origin": "https://www.lefive.fr",
                       "Referer": "https://www.lefive.fr/"})


def lefive_normalized(center, day, sport="Foot"):
    """Créneaux Le Five normalisés (même forme que urbansoccer_normalized)."""
    sid = center["sports"].get(sport)
    if not sid:
        return []
    out = []
    for s in lefive_slots(center["id"], day, sid):
        fields = s.get("fields") or []
        if not fields:
            continue
        prices = [f.get("webPrice") for f in fields
                  if f.get("webPrice") is not None]
        out.append({
            "source": "lefive",
            "center_id": center["id"],
            "center_name": center["name"],
            "cp": center["cp"],
            "sport": sport,
            # PIÈGE : `startingDate` est l'heure LOCALE Paris mais l'API la
            # suffixe quand même d'un "Z" mensonger (10:00Z pour 10h00 locale,
            # alors que `startingDateZuluTime` dit 08:00Z). On retire le Z pour
            # sortir un ISO naïf local, homogène avec UrbanSoccer.
            "start": (s.get("startingDate") or "").rstrip("Z"),
            "end": (s.get("endingDate") or "").rstrip("Z"),
            "duration": s.get("duration"),
            "free_courts": len(fields),
            "court_names": [f.get("name") for f in fields],
            "price": min(prices) if prices else None,
        })
    return out


# --------------------------------------------------------------- URBANSOCCER
def urbansoccer_centers(idf_only=True):
    """Catalogue UrbanSoccer (35 centres FR). Public, sans auth."""
    d = _get_json(f"{URBAN}/api/read/us/centers",
                  headers={"Origin": URBAN, "Referer": URBAN + "/"})
    out = []
    for c in d.get("data", []):
        addr = c.get("address") or ""
        m = re.search(r"\b(\d{5})\b", addr)
        cp = m.group(1) if m else ""
        if idf_only and cp[:2] not in CP_IDF:
            continue
        cats = {}
        for rt in c.get("resourceTypes") or []:
            for ct in rt.get("categories") or []:
                if ct.get("name"):
                    cats[ct["name"]] = ct.get("id")
        out.append({"id": c.get("id"), "name": c.get("name"), "cp": cp,
                    "address": addr, "lat": c.get("latitude"),
                    "lon": c.get("longitude"), "categories": cats})
    return sorted(out, key=lambda x: x["cp"])


def urbansoccer_slots(center_id, day, activity="1", categories="[5]"):
    """Créneaux UrbanSoccer d'un centre pour un jour.

    POST /api/read/reservation/availabilities/search
    header `activity` : 1=Soccer, 2=Padel (cf. urbanpadel_scrape.py)
    form-data : centerId, periodStart (ISO Zulu), categories, durations

    Réponse : {"data":[200, [slots]]} — chaque slot porte `count` = nombre de
    terrains encore libres sur ce créneau.
    """
    d = _post_form(
        f"{URBAN}/api/read/reservation/availabilities/search",
        {"centerId": str(center_id), "periodStart": f"{day}T00:00:00.000Z",
         "categories": categories, "durations": "[60,90,120]"},
        headers={"Origin": URBAN, "Referer": URBAN + "/reserver/",
                 "activity": activity})
    p = d.get("data") if isinstance(d, dict) else None
    if not isinstance(p, list) or len(p) < 2 or p[0] != 200:
        return []
    return p[1] if isinstance(p[1], list) else []


def urbansoccer_normalized(center, day, sport="Foot à 5"):
    cat = center["categories"].get(sport)
    if not cat:
        return []
    out = []
    for s in urbansoccer_slots(center["id"], day, "1", f"[{cat}]"):
        out.append({
            "source": "urbansoccer",
            "center_id": center["id"],
            "center_name": center["name"],
            "cp": center["cp"],
            "sport": sport,
            "start": s.get("start"),
            "end": s.get("end"),
            "duration": s.get("duration"),
            "free_courts": s.get("count"),
            "court_names": [s.get("resourceTypeDisplay")],
            "price": s.get("price"),
        })
    return out


# ------------------------------------------------------------ HOOPS FACTORY
def hoops_factory_status():
    """Hoops Factory : plateforme identifiée, mais créneaux NON accessibles.

    Front  : PWA Framework7 servie par www.hoopsfactory.com. Les 8 URLs du
             catalogue (/paris-15/, /courbevoie/, /thiais/…) renvoient toutes
             le MÊME shell de 10 148 octets — ce ne sont pas des pages de
             centre, d'où l'échec des détecteurs.
    Contenu : EventFactory  — GET https://eventfactory.ovh/api/centers
              (public ; c'est la source du catalogue réel ci-dessous)
    Booking : Extraclub — GET https://hoopsfactory-<ville>.extraclub.fr/api
                              /reservation/new/search
              params : person_id, date_from, date_to, types_ids[], page,
                       limit, with_estimated_costs
              headers : X-WSSE: <token>, Version: 4

    BLOQUÉ, pour deux raisons cumulatives :
    - `person_id` est l'ID Extraclub d'un COMPTE CLIENT enregistré. La route
      /booking du front est derrière `getIsConnected()`. Sans compte, pas de
      créneaux.
    - l'appel exige un header X-WSSE dérivé d'identifiants de service
      (ex_login / ex_password) codés en dur dans /assets/custom/js/hoops.min.js.
      Ce sont les identifiants d'intégration Hoops Factory ↔ Extraclub, pas des
      données publiques : on ne les rejoue pas et on ne les stocke pas ici.
      (À signaler au client : ces secrets fuitent dans un JS public.)

    Vérifié : un appel sans credentials renvoie 200 avec le corps
    "Connexion avec C_PROJECT_NAME [DEFAULT] inconnue." — aucune donnée.

    De plus le catalogue est faux : Hoops Factory n'a QU'UN centre en IDF
    (HF Paris, 3 rue Pierre Larousse, 93300 Aubervilliers). Les 7 autres
    entrées (paris-15, courbevoie, montigny, orly, thiais, villepinte,
    argenteuil) ne correspondent à aucun centre existant.
    """
    return {
        "platform": "extraclub",
        "content_api": "https://eventfactory.ovh/api/centers",
        "booking_api": ("https://hoopsfactory-paris.extraclub.fr/api"
                        "/reservation/new/search"),
        "scrapable": False,
        "blocker": "person_id (compte client) + header X-WSSE (secret de service)",
        "centres_idf_reels": 1,
    }


def hoops_factory_centers():
    """Catalogue réel Hoops Factory via l'API de contenu publique EventFactory.

    Le token d'accès de cette route est un token de CONTENU public (il sert à
    afficher la page 'nos centres' à un visiteur anonyme), pas un secret de
    booking. On le lit tel quel depuis la config publique du site.
    """
    cfg = urllib.request.Request(
        "https://www.hoopsfactory.com/assets/custom/js/hoops.min.js",
        headers={"User-Agent": UA})
    with urllib.request.urlopen(cfg, timeout=30) as r:
        js = r.read().decode("utf-8", "ignore")
    m = re.search(r'contact:"(https://eventfactory\.ovh/api/centers\?[^"]+)"', js)
    if not m:
        return []
    d = _get_json(m.group(1), headers={"Origin": "https://www.hoopsfactory.com"})
    out = []
    for c in d if isinstance(d, list) else []:
        addr = re.sub(r"<[^>]+>", " ", c.get("address") or "").strip()
        mm = re.search(r"\b(\d{5})\b", addr)
        out.append({"instance_id": c.get("instance_id"),
                    "name": c.get("center_name"), "address": addr,
                    "cp": mm.group(1) if mm else "", "online": c.get("online")})
    return out


# --------------------------------------------------------------- dead brands
DEAD = {
    "urbanfootball.fr": "domaine mort (certificat invalide, HTTP 404) — "
                        "marque absorbée, voir UrbanSoccer",
    "foot5.fr": "domaine parké en vente (Dovendi)",
    "soccerpark.com": "DNS inexistant",
    "soccerinside.fr": "DNS inexistant",
    "citysport-foot.com": "DNS inexistant",
    "indoorsport-palaiseau.com": "DNS inexistant",
    "kappafoot.fr": "DNS inexistant",
    "hoopscity.fr": "DNS inexistant",
    "basketballfactory.fr": "DNS inexistant",
    "funbasket.fr": "DNS inexistant",
    "5tonik.fr": "DNS inexistant",
    "ballin.paris": "DNS inexistant",
    "footcenter.fr": "HTTP 403 (WAF)",
    "myfoot.com": "certificat invalide",
    "footpark.fr": "redirige vers ospot16.fr — l'activité foot a disparu",
    "powerleague.fr": "association, billetterie HelloAsso — pas de terrain en ligne",
    "shootagain.fr": "WordPress vitrine, aucune réservation en ligne",
    "wearebasket.com": "page de 114 octets, site vide",
}


# ---------------------------------------------------------------------- main
def _line(center, rows):
    """Ligne de résumé d'un centre pour l'auto-test."""
    free = sum(r["free_courts"] or 0 for r in rows)
    pr = [r["price"] for r in rows if r["price"]]
    prix = f"  prix {min(pr):.0f}-{max(pr):.0f}EUR" if pr else ""
    return (f"  id={center['id']:<4} {center['name'][:24]:<24} {center['cp']}  "
            f"{len(rows):>3} créneaux / {free:>4} terrains libres{prix}")


def main():
    as_json = "--json" in sys.argv
    day = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    dump = {"day": day, "lefive": [], "urbansoccer": [],
            "hoops_factory": hoops_factory_status(), "dead": DEAD}

    if not as_json:
        print(f"### Créneaux du {day}\n")
        print("=== LE FIVE — POST /splf/v1/bookingrules/allFields")
    for c in lefive_centers():
        if "Foot" not in c["sports"]:
            continue
        try:
            rows = lefive_normalized(c, day, "Foot")
        except Exception as e:  # noqa: BLE001
            print(f"  ERR {c['name']}: {e}", file=sys.stderr)
            continue
        dump["lefive"].extend(rows)
        if not as_json:
            print(_line(c, rows))
        time.sleep(0.3)

    if not as_json:
        print("\n=== URBANSOCCER — POST /api/read/reservation/availabilities/search")
    for c in urbansoccer_centers():
        if "Foot à 5" not in c["categories"]:
            continue
        try:
            rows = urbansoccer_normalized(c, day, "Foot à 5")
        except Exception as e:  # noqa: BLE001
            print(f"  ERR {c['name']}: {e}", file=sys.stderr)
            continue
        dump["urbansoccer"].extend(rows)
        if not as_json:
            print(_line(c, rows))
        time.sleep(0.3)

    if as_json:
        json.dump(dump, sys.stdout, ensure_ascii=False, indent=1)
    else:
        hf = hoops_factory_centers()
        print(f"\n=== HOOPS FACTORY — {hoops_factory_status()['platform']} "
              f"(NON scrapable : "
              f"{hoops_factory_status()['blocker']})")
        for c in hf:
            print(f"  instance={c['instance_id']} {c['name']:<14} {c['cp']} "
                  f"{c['address']}")
        print(f"\nTOTAL : {len(dump['lefive'])} créneaux Le Five, "
              f"{len(dump['urbansoccer'])} créneaux UrbanSoccer")


if __name__ == "__main__":
    main()
