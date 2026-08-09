#!/usr/bin/env python3
"""Engine « ClubReady » — en réalité Glofox — pour Club Pilates France.

POURQUOI CE NOM DE FICHIER MENT UN PEU (à lire en premier)
----------------------------------------------------------
Le catalogue et `pilates_extension_discover.detect_clubready()` taguent les
Club Pilates en `clubready`, parce que Club Pilates est une franchise
Xponential et qu'aux États-Unis Xponential tourne effectivement sur ClubReady
(API fermée, réservée aux franchisés).

Ce n'est PAS le cas en France. Vérifié le 2026-08-09 sur les 4 clubs
franciliens : chaque page `clubpilates.fr/location/<slug>` embarque une iframe

    https://app.glofox.com/portal/#/branch/<branchId>/classes-day-view

Club Pilates France (namespace Glofox `clubpilatesfrance`) tourne donc sur
**Glofox** (ABC Fitness), dont le portail web a une API publique. Le tag
`clubready` est conservé pour ne pas casser le routage existant ; le module
répond aussi bien à `platform="glofox"`.

Corollaire : si un jour une marque tourne vraiment sur ClubReady US, cet engine
ne l'aidera pas — `resolve_branch_id()` lèvera ClubReadyError en le disant.

L'API GLOFOX PORTAL, TELLE QU'ELLE EST RÉELLEMENT APPELÉE
---------------------------------------------------------
Tout est sur `https://api.glofox.com/2.0/`. Le portail web s'authentifie en
INVITÉ — pas de compte, pas de carte, c'est le mode nominal pour un visiteur
qui consulte le planning avant de réserver :

    POST /2.0/login   {"branch_id": "<branchId>", "login":"GUEST", "password":"GUEST"}
      -> {"token": "<JWT>", "branch": {...}, "user": {"type":"GUEST"}}

    GET  /2.0/events?start=<epoch>&end=<epoch>&include=trainers,facility,program
                     &sort_by=time_start&private=false&page=<n>
      en-têtes : Authorization: Bearer <JWT>
      -> {"data":[...], "page":1, "limit":50, "has_more":true, "total_count":72}

PIÈGES MESURÉS (2026-08-09)
---------------------------
  * **Le JWT porte la branche, pas l'en-tête.** Réutiliser le token du club A
    avec `x-glofox-branch-id: <club B>` renvoie le planning de A. Il faut donc
    UN login invité PAR CLUB — d'où le cache par branchId ci-dessous.
  * **Pagination obligatoire** : `limit` vaut 50 et `total_count` monte à 252
    sur 6 semaines. Sans boucle sur `page`, on perd silencieusement la queue.
  * **`time_start` est un epoch UTC vrai**, à formater dans le fuseau du club
    (`branch.address.timezone_id`). Vérifié contre les horaires d'ouverture
    publiés sur clubpilates.fr : à Saint-Maur le dernier cours du samedi finit
    à 12:30 et la boutique ferme à 12:30 en heure de Paris — l'interprétation
    « epoch = heure locale » décalerait tout de 2 h.
  * `size` = capacité, `booked` = réservés, `waiting` = liste d'attente réelle
    (0..n, à ne pas confondre avec `features.booking.waiting_list` qui est le
    plafond). Aucune donnée nominative de membre n'est renvoyée à un invité, et
    on ne demande pas l'include `users_booked` qui pourrait en exposer.

API PUBLIQUE DU MODULE
----------------------
    fetch_sessions(identifiant, days=21) -> list[dict]

`identifiant` : clé de marque du catalogue (`club_pilates_convention`),
branchId Glofox brut, URL du portail Glofox, ou URL de la page
`clubpilates.fr/location/<slug>` (le branchId y est extrait à la volée).

Chaque dict rendu :

    {id, start, end, cours, coach, lieu, capacite, presents, waiting,
     niveau, club, statut, source}

`start`/`end` sont des ISO-8601 locaux "YYYY-MM-DDTHH:MM:SS" dans le fuseau du
club. Les champs inconnus valent None — jamais une valeur inventée.

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
from zoneinfo import ZoneInfo

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0 Safari/537.36")

API = "https://api.glofox.com/2.0"
PORTAL = "https://app.glofox.com/portal"

# `limit` côté serveur ; sert de garde-fou à la boucle de pagination.
PAGE_LIMIT = 50
MAX_PAGES = 40

DEFAULT_DAYS = 21
DEFAULT_TZ = "Europe/Paris"

_BRANCH_RE = re.compile(r"app\.glofox\.com/portal/#/branch/([a-f0-9]{24})", re.I)
_OBJECTID_RE = re.compile(r"^[a-f0-9]{24}$", re.I)


class ClubReadyError(RuntimeError):
    """Erreur générique de l'engine."""


class ClubReadyAuthRequired(ClubReadyError):
    """La source demande une authentification qu'on n'a pas."""


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _request(url, data=None, headers=None, timeout=30, retries=3):
    """Retourne (status, body_str). Ne lève qu'après épuisement des retries
    sur erreur réseau ; un 4xx est rendu tel quel (il porte le motif)."""
    hd = {"User-Agent": UA, "Accept": "application/json, text/html, */*",
          "x-glofox-source": "webportal"}
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
    raise ClubReadyError(f"{url} injoignable : {last!r}")


def _json(url, data=None, headers=None, timeout=30):
    status, body = _request(url, data=data, headers=headers, timeout=timeout)
    try:
        return status, json.loads(body)
    except ValueError:
        return status, None


# --------------------------------------------------------------------------
# Registre des marques
# --------------------------------------------------------------------------
# branchId Glofox relevés le 2026-08-09 dans l'iframe des pages
# clubpilates.fr/location/<slug>. `page` permet de les re-vérifier (et de
# récupérer automatiquement un branchId qui changerait) sans re-fouiller le
# site : voir `refresh_branch_id()`.
SOURCES = {
    "club_pilates_saint_maur": {
        "branch": "68d5055eb315a90e1b00241b",
        "label": "Club Pilates Saint-Maur",
        "page": "https://www.clubpilates.fr/location/saint-maur",
    },
    "club_pilates_convention": {
        "branch": "6790cf423e7ecf3afd022950",
        "label": "Club Pilates Paris Convention",
        "page": "https://www.clubpilates.fr/location/paris-convention",
    },
    "club_pilates_pompe": {
        "branch": "67e172cab1cb05268406fbcb",
        "label": "Club Pilates Paris Rue de la Pompe",
        "page": "https://www.clubpilates.fr/location/paris-rue-de-la-pompe",
    },
    "club_pilates_champ": {
        "branch": "6901df6bfbe8599d4a03d132",
        "label": "Club Pilates Paris Champ-de-Mars",
        "page": "https://www.clubpilates.fr/location/paris-champ-de-mars",
    },
}


def branch_id_from_page(url):
    """Extrait le branchId Glofox d'une page web (iframe du portail).

    Rend None si la page ne contient aucune iframe Glofox — c'est le cas des
    clubs annoncés mais pas encore ouverts (ex. Paris Monceau au 2026-08-09).
    """
    status, html = _request(url)
    if status != 200:
        raise ClubReadyError(f"{url} : HTTP {status}")
    m = _BRANCH_RE.search(html)
    return m.group(1) if m else None


def refresh_branch_id(key):
    """Re-résout le branchId d'une marque du registre depuis sa page publique.

    À utiliser si un club migre : le registre sert de cache, la page fait foi.
    """
    src = SOURCES.get(key)
    if not src or not src.get("page"):
        raise ClubReadyError(f"marque '{key}' inconnue du registre ClubReady/Glofox")
    branch = branch_id_from_page(src["page"])
    if not branch:
        raise ClubReadyError(
            f"{key} : aucune iframe Glofox sur {src['page']} — club pas encore "
            f"ouvert, ou passé sur une autre plateforme")
    return branch


def resolve_branch_id(identifiant):
    """Clé de marque, branchId, URL portail ou URL de page club -> branchId."""
    raw = (identifiant or "").strip()
    if not raw:
        raise ClubReadyError("identifiant ClubReady/Glofox vide")
    if raw in SOURCES:
        return SOURCES[raw]["branch"]
    if _OBJECTID_RE.match(raw):
        return raw.lower()
    m = _BRANCH_RE.search(raw)
    if m:
        return m.group(1)
    if raw.lower().startswith(("http://", "https://")):
        branch = branch_id_from_page(raw)
        if branch:
            return branch
        raise ClubReadyError(
            f"aucun branchId Glofox sur {raw} — cette marque n'est pas (ou "
            f"plus) sur Glofox ; si elle est sur ClubReady US, son API est "
            f"fermée et cet engine ne peut rien pour elle")
    raise ClubReadyError(f"identifiant ClubReady/Glofox non résolu : {raw}")


# --------------------------------------------------------------------------
# Session invité (une par club — le JWT porte la branche)
# --------------------------------------------------------------------------
_GUEST_CACHE = {}


def guest_session(branch_id):
    """(token, branch) pour un club. Login invité public, mis en cache."""
    if branch_id in _GUEST_CACHE:
        return _GUEST_CACHE[branch_id]
    status, payload = _json(f"{API}/login", data={
        "branch_id": branch_id, "login": "GUEST", "password": "GUEST"})
    if status != 200 or not isinstance(payload, dict) or not payload.get("token"):
        msg = ""
        if isinstance(payload, dict):
            msg = payload.get("message") or ""
        raise ClubReadyAuthRequired(
            f"branch {branch_id} : login invité refusé (HTTP {status}) {msg}".strip())
    out = (payload["token"], payload.get("branch") or {})
    _GUEST_CACHE[branch_id] = out
    return out


def branch_info(branch_id):
    """Métadonnées publiques du club (nom, adresse, fuseau, horaires)."""
    return guest_session(branch_id)[1]


def branch_tz(branch):
    tzid = ((branch.get("address") or {}).get("timezone_id")) or DEFAULT_TZ
    try:
        return ZoneInfo(tzid)
    except Exception:  # noqa: BLE001 — fuseau inconnu du système
        return ZoneInfo(DEFAULT_TZ)


# --------------------------------------------------------------------------
# Événements
# --------------------------------------------------------------------------
def raw_events(branch_id, start_epoch, end_epoch, max_pages=MAX_PAGES):
    """Tous les événements publics du club sur [start, end], pagination incluse."""
    token, _ = guest_session(branch_id)
    headers = {"Authorization": f"Bearer {token}",
               "x-glofox-branch-id": branch_id}
    out, seen = [], set()
    for page in range(1, max_pages + 1):
        qs = urllib.parse.urlencode({
            "start": int(start_epoch),
            "end": int(end_epoch),
            # NB : pas de `users_booked` — inutile ici et potentiellement nominatif.
            "include": "trainers,facility,program",
            "sort_by": "time_start",
            "private": "false",
            "page": page,
        })
        status, payload = _json(f"{API}/events?{qs}", headers=headers)
        if status != 200 or not isinstance(payload, dict):
            raise ClubReadyError(
                f"events branch={branch_id} page={page} : HTTP {status} "
                f"({str(payload)[:160]})")
        data = payload.get("data") or []
        for ev in data:
            eid = ev.get("_id")
            if eid and eid in seen:
                continue
            if eid:
                seen.add(eid)
            out.append(ev)
        if not payload.get("has_more") or not data:
            break
    return out


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------
def _coach_of(ev):
    for tr in ev.get("trainers_obj") or []:
        name = (tr.get("name") or
                " ".join(x for x in (tr.get("first_name"), tr.get("last_name")) if x))
        name = (name or "").strip()
        if name:
            return name
    return None


def normalize_event(ev, tz, lieu=None):
    """Événement Glofox -> dict maison. Rend None si inexploitable."""
    ts = ev.get("time_start")
    if not isinstance(ts, (int, float)):
        return None
    start = dt.datetime.fromtimestamp(int(ts), tz)
    duration = ev.get("duration")
    end = (start + dt.timedelta(minutes=int(duration))
           if isinstance(duration, (int, float)) else None)

    cap = ev.get("size")
    pres = ev.get("booked")
    cap = int(cap) if isinstance(cap, (int, float)) else None
    pres = int(pres) if isinstance(pres, (int, float)) else None
    if cap is None or pres is None:
        statut = "inconnu"
    elif pres >= cap:
        statut = "complet"
    else:
        statut = "disponible"

    return {
        "id": str(ev.get("_id") or ""),
        "start": start.replace(tzinfo=None).isoformat(),
        "end": end.replace(tzinfo=None).isoformat() if end else None,
        "cours": (ev.get("name") or "").strip() or None,
        "coach": _coach_of(ev),
        "lieu": lieu,
        "capacite": cap,
        "presents": pres,
        "waiting": ev.get("waiting"),
        "niveau": ev.get("level") or None,
        "club": lieu,
        "statut": statut,
        "source": "glofox_portal",
    }


def fetch_sessions(identifiant, days=DEFAULT_DAYS, since=None, past_days=0, **_kw):
    """Créneaux normalisés d'un club Club Pilates France (Glofox).

    identifiant : clé de marque (`club_pilates_convention`), branchId Glofox,
                  URL du portail, ou URL `clubpilates.fr/location/<slug>`.
    days        : horizon en jours à partir d'aujourd'hui.
    past_days   : jours de passé à inclure (0 par défaut).

    Retourne list[dict] {id, start, end, cours, coach, lieu, capacite,
    presents, waiting, niveau, club, statut, source}, triée par début.
    Lève ClubReadyError si le club est injoignable ou n'est pas sur Glofox ;
    rend [] (sans lever) s'il est joignable mais ne publie aucun cours.
    """
    branch_id = resolve_branch_id(identifiant)
    branch = branch_info(branch_id)
    tz = branch_tz(branch)
    lieu = branch.get("name") or None

    day0 = since or dt.datetime.now(tz).date()
    start = dt.datetime.combine(day0 - dt.timedelta(days=past_days),
                                dt.time(0, 0), tzinfo=tz)
    end = dt.datetime.combine(day0 + dt.timedelta(days=days),
                              dt.time(0, 0), tzinfo=tz)

    sessions, seen = [], set()
    for ev in raw_events(branch_id, start.timestamp(), end.timestamp()):
        norm = normalize_event(ev, tz, lieu=lieu)
        if not norm or not norm["id"] or norm["id"] in seen:
            continue
        seen.add(norm["id"])
        sessions.append(norm)
    sessions.sort(key=lambda s: s["start"])
    return sessions


# --------------------------------------------------------------------------
# CLI de test
# --------------------------------------------------------------------------
def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: engine_clubready.py <clé|branchId|url> [--days N] [--info]")
        print("       marques connues :", ", ".join(sorted(SOURCES)))
        return 2
    ident = argv[0]
    days = DEFAULT_DAYS
    if "--days" in argv:
        days = int(argv[argv.index("--days") + 1])
    try:
        branch_id = resolve_branch_id(ident)
        if "--info" in argv:
            b = branch_info(branch_id)
            addr = b.get("address") or {}
            print(f"branch {branch_id} — {b.get('name')} / namespace "
                  f"{b.get('namespace')}")
            print(f"   {addr.get('street')}, {addr.get('postal_code')} "
                  f"{addr.get('city')} (tz {addr.get('timezone_id')})")
            return 0
        sessions = fetch_sessions(ident, days=days)
    except ClubReadyError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    print(f"{len(sessions)} créneaux pour {ident} → branch {branch_id} "
          f"(fenêtre {days} j)")
    for s in sessions[:8]:
        print("   ", json.dumps(s, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
