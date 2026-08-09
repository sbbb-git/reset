#!/usr/bin/env python3
"""Engine Sportigo — récupération des créneaux d'un tenant `<slug>.sportigo.club`.

CE QU'EST VRAIMENT SPORTIGO
---------------------------
Sportigo est un logiciel de gestion de club français, multi-tenant. Chaque
client dispose de deux hôtes :

    https://<slug>.sportigo.club/     front Next.js (membre + pages publiques)
    https://<slug>.sportigo.fr/       backend Symfony + API `/api/rest/v2/...`

Le front Next.js ne parle JAMAIS au backend depuis le navigateur : il passe par
ses propres routes serveur, qui rajoutent côté serveur le Basic auth de l'API
« accès public » et relaient. Deux routes nous intéressent, toutes deux
publiques (aucun compte, aucun cookie, aucun token) :

  1. GET  /api/sportigo/planningdx?room=<id>&disciplines=&StartDate=<iso>&EndDate=<iso>
     C'est la source du planning public (`/public/planning`). Les deux
     paramètres de date sont ceux que le composant Syncfusion Scheduler ajoute
     à sa requête (`StartDate` / `EndDate`, ISO-8601 en Z). Sans eux : HTTP 400.

  2. POST /api/sportigo/service   {"url":"/planningdx","method":"post",
                                   "data":{"dateStart","dateEnd","room"}}
     Proxy générique du front vers l'API REST. Même contenu, dates en
     `YYYY-MM-DD`. Sert de repli si (1) change de signature.

On n'utilise DÉLIBÉRÉMENT pas `https://<slug>.sportigo.fr/api/rest/v2/...` en
direct : cet hôte-là exige un en-tête `Authorization: Basic ...` que le bundle
JS publie en clair. Passer par le front `.club` donne exactement les mêmes
données publiques sans réutiliser d'identifiant.

CE QUE RENVOIE LE PLANNING
--------------------------
Un événement `planningdx` est complet — c'est une des rares plateformes à
publier le remplissage sans authentification :

    {"id":"1891946_2026-08-28", "discipline":"FLOW - REFORMER", "planning":
     "Studio Reformer", "startDate":"2026-08-28 08:00:00", "endDate":
     "2026-08-28 08:50:00", "reservation":0, "maxMember":6, "waitingCount":2,
     "coachName":"Coach Remplaçant", "room":3479, ...}

`startDate`/`endDate` sont des heures LOCALES du club (company.timezone,
Europe/Paris pour les tenants FR) — aucune conversion à faire.
Attention à `waitingCount` : malgré son nom ce n'est PAS le nombre de gens en
liste d'attente, c'est la TAILLE de la liste configurée sur la salle (mesuré :
identique à `company.clubs[].rooms[].waitingList`, y compris quand
`reservation` vaut 0). Il est donc exposé sous le nom `waiting_list` et n'entre
dans aucun calcul de remplissage.
`id` vaut `<idTemplate>_<date>` : il est déterministe, donc rejouable d'un run
à l'autre sans dupliquer le store.

DEUX PIÈGES MESURÉS (2026-08-09, tenant `pyra`)
-----------------------------------------------
  * La fenêtre de requête est bornée côté serveur. 60 jours passent, 91 jours
    renvoient `[]` SILENCIEUSEMENT (pas d'erreur). D'où le découpage en
    tranches de WINDOW_DAYS jours ci-dessous — ne pas l'enlever.
  * Le planning s'interroge PAR SALLE (`room`), il n'existe pas de « toutes
    salles ». La liste des salles est publiée dans le `__NEXT_DATA__` de
    /public/planning (company.clubs[].rooms[]), on la lit là.

API PUBLIQUE DU MODULE
----------------------
    fetch_sessions(identifiant, days=21) -> list[dict]

`identifiant` : clé de marque du catalogue (`pyra`), slug de tenant, ou URL
(`https://pyra.sportigo.club/`). Chaque dict rendu :

    {id, start, end, cours, coach, lieu, capacite, presents, waiting_list,
     club, salle, statut, source}

`start`/`end` sont des ISO-8601 locaux "YYYY-MM-DDTHH:MM:SS". Les champs
inconnus valent None — jamais une valeur inventée.

Python 3.12, stdlib uniquement.
"""
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0 Safari/537.36")

CLUB_HOST = "https://{slug}.sportigo.club"

# Tranche maximale d'interrogation. Mesuré : 60 j OK, 91 j -> [] sans erreur.
WINDOW_DAYS = 14

# Horizon par défaut : les tenants publient rarement au-delà de 3 semaines
# (mobileAppConfig.availabilityInterval = "+3 week" chez pyra).
DEFAULT_DAYS = 21


class SportigoError(RuntimeError):
    """Erreur générique de l'engine."""


class SportigoUnavailable(SportigoError):
    """Le tenant existe mais ne publie pas de planning exploitable."""


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _request(url, data=None, headers=None, timeout=30, retries=3):
    """Retourne (status, body_str). Ne lève qu'après épuisement des retries
    sur erreur réseau ; un 4xx est rendu tel quel (il porte souvent le motif)."""
    hd = {"User-Agent": UA, "Accept": "application/json, text/html, */*"}
    if headers:
        hd.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hd.setdefault("Content-Type", "application/json")
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=hd)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            payload = e.read().decode("utf-8", "ignore")
            if 400 <= e.code < 500:
                return e.code, payload
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise SportigoError(f"{url} injoignable : {last!r}")


def _get_json(url, headers=None, timeout=30):
    status, body = _request(url, headers=headers, timeout=timeout)
    try:
        return status, json.loads(body)
    except ValueError:
        return status, None


# --------------------------------------------------------------------------
# Résolution du tenant
# --------------------------------------------------------------------------
# Marques du catalogue -> slug de tenant Sportigo. Une clé absente d'ici est
# traitée comme un slug brut (`fetch_sessions("pyra")` marche sans registre).
#
# `pyra` (catalogue Pilates) et `pyra_yoga` (catalogue Yoga) sont le MÊME
# tenant : Pyra fait du reformer et du yoga dans les mêmes salles, et Sportigo
# ne sépare pas les deux plannings. Les deux clés rendent donc exactement les
# mêmes séances, dans deux stores distincts alimentant deux agrégats distincts
# (pilates_idf / yoga_idf) — pas de double comptage à l'intérieur d'un
# dashboard, mais ne pas additionner les deux.
SOURCES = {
    "pyra": {"slug": "pyra", "label": "Pyra"},
    "pyra_yoga": {"slug": "pyra", "label": "Pyra (catalogue Yoga)"},
}


def resolve_slug(identifiant):
    """Clé de marque, slug ou URL -> slug de tenant Sportigo.

    Accepte 'pyra', 'pyra.sportigo.club', 'https://pyra.sportigo.club/',
    'https://pyra.sportigo.fr/api/...'.
    """
    raw = (identifiant or "").strip()
    if not raw:
        raise SportigoError("identifiant Sportigo vide")
    if raw in SOURCES:
        return SOURCES[raw]["slug"]

    low = raw.lower()
    m = re.search(r"(?:https?://)?([a-z0-9][a-z0-9-]*)\.sportigo\.(?:club|fr)", low)
    if m:
        return m.group(1)
    if "://" in low or "/" in low:
        raise SportigoError(f"URL non Sportigo : {raw}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", low):
        raise SportigoError(f"slug Sportigo invalide : {raw}")
    return low


# --------------------------------------------------------------------------
# Company (salles, disciplines, fuseau) — publiée dans le __NEXT_DATA__
# --------------------------------------------------------------------------
_COMPANY_CACHE = {}


def company(slug):
    """Config publique du tenant : clubs, salles, disciplines, timezone.

    Lue dans le `__NEXT_DATA__` de la page publique /public/planning : c'est
    le même JSON que celui que reçoit n'importe quel navigateur.
    """
    if slug in _COMPANY_CACHE:
        return _COMPANY_CACHE[slug]

    url = CLUB_HOST.format(slug=slug) + "/public/planning"
    status, html = _request(url)
    if status != 200 or "__NEXT_DATA__" not in html:
        raise SportigoError(
            f"tenant '{slug}' : /public/planning illisible (HTTP {status}) — "
            f"slug probablement faux")
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html, re.S)
    if not m:
        raise SportigoError(f"tenant '{slug}' : __NEXT_DATA__ introuvable")
    try:
        data = json.loads(m.group(1))
    except ValueError as e:
        raise SportigoError(f"tenant '{slug}' : __NEXT_DATA__ illisible ({e})") from e

    comp = (data.get("props") or {}).get("company") or {}
    if not comp:
        raise SportigoError(f"tenant '{slug}' : bloc company absent")
    _COMPANY_CACHE[slug] = comp
    return comp


def rooms(slug):
    """[(room_id, room_name, club_name)] — les salles à interroger."""
    comp = company(slug)
    out = []
    for club in comp.get("clubs") or []:
        club_name = club.get("name")
        for room in club.get("rooms") or []:
            rid = room.get("id") or room.get("value")
            if rid is None:
                continue
            out.append((int(rid), room.get("name"), club_name))
    if not out:
        raise SportigoUnavailable(
            f"tenant '{slug}' : aucune salle publiée (company.clubs[].rooms vide)")
    return out


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------
def _planning_dx(slug, room, start, end):
    """Route publique du planning (celle de /public/planning).

    `start`/`end` : dates (datetime.date). Rend une liste, [] si vide.
    """
    qs = urllib.parse.urlencode({
        "room": room,
        "disciplines": "",
        "StartDate": f"{start.isoformat()}T00:00:00.000Z",
        "EndDate": f"{end.isoformat()}T00:00:00.000Z",
    })
    url = f"{CLUB_HOST.format(slug=slug)}/api/sportigo/planningdx?{qs}"
    status, payload = _get_json(url)
    if status != 200 or not isinstance(payload, list):
        raise SportigoError(
            f"planningdx {slug}/room={room} : HTTP {status} "
            f"({str(payload)[:160]})")
    return payload


def _planning_service(slug, room, start, end):
    """Repli : proxy REST générique du front (`/api/sportigo/service`)."""
    url = CLUB_HOST.format(slug=slug) + "/api/sportigo/service"
    status, body = _request(url, data={
        "url": "/planningdx",
        "method": "post",
        "data": {"dateStart": start.isoformat(),
                 "dateEnd": end.isoformat(),
                 "room": room},
    })
    if status != 200:
        raise SportigoError(f"service {slug}/room={room} : HTTP {status}")
    try:
        payload = json.loads(body)
    except ValueError as e:
        raise SportigoError(f"service {slug}/room={room} : réponse illisible ({e})") from e
    if not isinstance(payload, list):
        raise SportigoError(f"service {slug}/room={room} : {str(payload)[:160]}")
    return payload


def raw_events(slug, room, days=DEFAULT_DAYS, since=None):
    """Événements bruts d'une salle, en tranches de WINDOW_DAYS jours.

    La fenêtre est bornée côté serveur (au-delà de ~60 j la réponse est un
    tableau vide, sans erreur) : le découpage n'est pas une optimisation, il
    est nécessaire à la complétude.
    """
    day0 = since or dt.date.today()
    out, seen = [], set()
    cursor = day0
    horizon = day0 + dt.timedelta(days=days)
    while cursor < horizon:
        chunk_end = min(cursor + dt.timedelta(days=WINDOW_DAYS), horizon)
        try:
            events = _planning_dx(slug, room, cursor, chunk_end)
        except SportigoError:
            events = _planning_service(slug, room, cursor, chunk_end)
        for ev in events:
            eid = ev.get("id")
            if eid and eid in seen:
                continue
            if eid:
                seen.add(eid)
            out.append(ev)
        cursor = chunk_end
    return out


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------
def _iso(value):
    """'2026-08-28 08:00:00' -> '2026-08-28T08:00:00' (heure locale du club)."""
    if not value:
        return None
    txt = str(value).strip().replace(" ", "T")
    try:
        return dt.datetime.fromisoformat(txt).isoformat()
    except ValueError:
        return None


def normalize_event(ev, room_name=None, club_name=None):
    """Événement planningdx -> dict maison. Rend None si inexploitable."""
    start = _iso(ev.get("startDate"))
    if not start:
        return None
    cap = ev.get("maxMember")
    pres = ev.get("reservation")
    cap = int(cap) if isinstance(cap, (int, float)) else None
    pres = int(pres) if isinstance(pres, (int, float)) else None
    if cap is None or pres is None:
        statut = "inconnu"
    elif pres >= cap:
        statut = "complet"
    else:
        statut = "disponible"
    coach = (ev.get("coachName") or "").strip() or None
    lieu = ev.get("planning") or room_name
    return {
        "id": str(ev.get("id") or ""),
        "start": start,
        "end": _iso(ev.get("endDate")),
        "cours": (ev.get("discipline") or "").strip() or None,
        "coach": coach,
        "lieu": lieu,
        "capacite": cap,
        "presents": pres,
        "waiting_list": ev.get("waitingCount"),
        "club": club_name,
        "salle": room_name,
        "statut": statut,
        "source": "sportigo_planningdx",
    }


def fetch_sessions(identifiant, days=DEFAULT_DAYS, since=None, room=None, **_kw):
    """Créneaux normalisés d'un tenant Sportigo.

    identifiant : clé de marque (`pyra`), slug de tenant, ou URL
                  `https://<slug>.sportigo.club/`.
    days        : horizon en jours à partir d'aujourd'hui.
    room        : restreint à une salle (id Sportigo). Par défaut : toutes.

    Retourne list[dict] {id, start, end, cours, coach, lieu, capacite,
    presents, waiting_list, club, salle, statut, source}, triée par début.
    Lève SportigoError si le tenant est injoignable ; rend [] (sans lever)
    si le tenant est joignable mais ne publie aucun créneau.
    """
    slug = resolve_slug(identifiant)
    targets = rooms(slug)
    if room is not None:
        targets = [t for t in targets if t[0] == int(room)]
        if not targets:
            raise SportigoError(f"tenant '{slug}' : salle {room} inconnue")

    sessions, seen = [], set()
    for room_id, room_name, club_name in targets:
        for ev in raw_events(slug, room_id, days=days, since=since):
            norm = normalize_event(ev, room_name=room_name, club_name=club_name)
            if not norm or not norm["id"]:
                continue
            if norm["id"] in seen:
                continue
            seen.add(norm["id"])
            sessions.append(norm)
    sessions.sort(key=lambda s: (s["start"], s.get("lieu") or ""))
    return sessions


# --------------------------------------------------------------------------
# CLI de test
# --------------------------------------------------------------------------
def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: engine_sportigo.py <slug|clé|url> [--days N] [--rooms]")
        print("       marques connues :", ", ".join(sorted(SOURCES)))
        return 2
    ident = argv[0]
    days = DEFAULT_DAYS
    if "--days" in argv:
        days = int(argv[argv.index("--days") + 1])
    try:
        slug = resolve_slug(ident)
        if "--rooms" in argv:
            comp = company(slug)
            print(f"tenant {slug} — {comp.get('company')} "
                  f"(tz {comp.get('timezone')}, {len(comp.get('disciplines') or [])} disciplines)")
            for rid, rname, cname in rooms(slug):
                print(f"   salle {rid:6d}  {rname}   [{cname}]")
            return 0
        sessions = fetch_sessions(ident, days=days)
    except SportigoError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    print(f"{len(sessions)} créneaux pour {slug} (fenêtre {days} j)")
    for s in sessions[:8]:
        print("   ", json.dumps(s, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
