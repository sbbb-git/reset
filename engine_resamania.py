#!/usr/bin/env python3
"""Engine Resamania (Resamania II / Stadline) — récupération des créneaux.

CE QU'EST VRAIMENT RESAMANIA
----------------------------
Resamania II est une API REST API-Platform (JSON-LD / Hydra), une instance par
client, sur le patron :

    https://api.<clientToken>.resamania.com/<clientToken>/<ressource>
    https://member.<clientToken>.resamania.com/<clientToken>/   (SPA React)
    https://groot.<clientToken>.resamania.com/api/<clientToken>/resa2-member/config/default/1.0

La ressource qui sert les créneaux est `class_events` :

    GET /<clientToken>/class_events?startedAt[after]=YYYY-MM-DD&order[startedAt]=asc&page=N

Un ClassEvent porte tout ce qu'on veut : startedAt / endedAt, activity.name,
coach, studio.name, club.name, attendingLimit (capacité), bookedAttendees[]
(réservés) et queuedAttendees[] (liste d'attente).

AUTHENTIFICATION — LE POINT DUR (lire avant de s'énerver)
--------------------------------------------------------
Tout l'API est derrière un JWT (`{"code":401,"message":"JWT Token not found"}`).
Le SPA membre embarque en clair, dans son bundle JS, quatre jeux de
credentials OAuth2 (client_id/client_secret) :

  * client "principal"  → grant_type=password  (compte membre réel requis)
  * client "anonymous"  → grant_type=client_credentials → JWT ROLE_DESK
  * client "totem"      → grant_type=client_credentials → JWT ROLE_ANONYMOUS

Le token anonyme s'obtient sans compte, MAIS son scope est restreint côté
serveur, par client, endpoint par endpoint. Mesuré sur le tenant `cdf`
(Cercles de la Forme, 2026-08-09) :

    /cdf/clubs               → 200  (28 clubs)
    /cdf/class_events        → 403  api.error.scope.unauthorized-endpoint
    /cdf/studios, /coaches   → 403
    /cdf/showcase_activities → 403

Autrement dit : **sur cdf, les créneaux ne sont PAS accessibles anonymement**.
Il faut un JWT de membre (grant_type=password, identifiants d'un compte du
club). Le code ci-dessous sait le faire — voir `member_token()` et les
variables d'env RESAMANIA_<TOKEN>_USER / RESAMANIA_<TOKEN>_PASS — mais aucun
compte n'est fourni par défaut, donc le chemin API reste dormant tant qu'on
n'en a pas un. Le scope étant configuré par tenant, un autre client Resamania
peut très bien laisser class_events ouvert : `fetch_sessions` tente toujours
l'anonyme d'abord et ne bascule sur le fallback qu'en cas de 403/401.

FALLBACKS PUBLICS (ce qui tourne réellement aujourd'hui)
--------------------------------------------------------
Les sites vitrine proxifient eux-mêmes une partie du planning Resamania, sans
auth. Deux adaptateurs sont implémentés :

  * `cdf_wp`      Cercles de la Forme — plugin WP `cdlf-planning-resamania`,
                  POST /wp-admin/admin-ajax.php action=search_event.
                  Rend la grille récurrente (semaine courante + suivante) :
                  jour, heure, durée, activité, salle, club. PAS de coach,
                  PAS de capacité/présents (le WP ne synchronise que la grille).
  * `ozenhit_html` OZEN HIT — planning rendu côté serveur sur /planning/.
                  Horaires + durée + studio, mais le nom du cours n'existe QUE
                  sous forme de logo image : `cours` reste None.

Les deux Montgolfière sont derrière un espace membre (`resamania_login` en
proxy WP) : rien de public, `fetch_sessions` lève ResamaniaAuthRequired.

API PUBLIQUE DU MODULE
----------------------
    fetch_sessions(slug_ou_id) -> list[dict]

Chaque dict : {id, start, end, cours, coach, lieu, capacite, presents}
(+ `club`, `source`, `statut` en bonus). `start`/`end` sont des ISO-8601
locaux "YYYY-MM-DDTHH:MM:SS". Les champs inconnus valent None — jamais une
valeur inventée.

Python 3.12, stdlib uniquement.
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0 Safari/537.36")

API_HOST = "https://api.{token}.resamania.com"
MEMBER_HOST = "https://member.{token}.resamania.com"
GROOT_HOST = "https://groot.{token}.resamania.com"

JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


class ResamaniaError(RuntimeError):
    """Erreur générique de l'engine."""


class ResamaniaAuthRequired(ResamaniaError):
    """La source demande une authentification qu'on n'a pas."""


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _request(url, data=None, headers=None, timeout=30, retries=3):
    """GET/POST avec retries. Retourne (status, body_str). Ne lève que sur
    épuisement des retries pour les erreurs réseau."""
    hd = {"User-Agent": UA, "Accept": "application/ld+json, application/json, text/html, */*"}
    if headers:
        hd.update(headers)
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data, doseq=True).encode()
        hd.setdefault("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=hd)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            # 4xx : réponse utile, on la rend telle quelle (pas de retry)
            payload = e.read().decode("utf-8", "ignore")
            if 400 <= e.code < 500:
                return e.code, payload
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise ResamaniaError(f"{url} injoignable : {last!r}")


def _get_json(url, headers=None, timeout=30):
    status, body = _request(url, headers=headers, timeout=timeout)
    try:
        return status, json.loads(body)
    except ValueError:
        return status, {"_raw": body[:400]}


# --------------------------------------------------------------------------
# Découverte du tenant : credentials OAuth publiés dans le bundle du SPA
# --------------------------------------------------------------------------
_TENANT_CACHE = {}

_ENV_PATTERNS = {
    "api_base": r"REACT_APP_API_BASEURL:[^?]*?\?\?\s*[`'\"]([^`'\"]+)",
    "oauth_base": r"REACT_APP_OAUTH_BASEURL:[^?]*?\?\?\s*[`'\"]([^`'\"]+)",
    "client_id": r"REACT_APP_OAUTH_CLIENTID:[^?]*?\?\?\s*[`'\"]([^`'\"]+)",
    "client_secret": r"REACT_APP_OAUTH_CLIENTSECRET:[^?]*?\?\?\s*[`'\"]([^`'\"]+)",
    "anon_client_id": r"REACT_APP_OAUTH_ANONYMOUS_CLIENTID:[^?]*?\?\?\s*[`'\"]([^`'\"]+)",
    "anon_client_secret": r"REACT_APP_OAUTH_ANONYMOUS_CLIENTSECRET:[^?]*?\?\?\s*[`'\"]([^`'\"]+)",
}


def discover_tenant(client_token):
    """Lit le bundle du SPA membre pour en extraire base API + clients OAuth.

    Ces valeurs sont servies publiquement par le site du club lui-même (elles
    sont dans le JS que tout navigateur télécharge) ; on ne fait que lire ce
    que la page publie.
    """
    if client_token in _TENANT_CACHE:
        return _TENANT_CACHE[client_token]

    base = MEMBER_HOST.format(token=client_token)
    status, index = _request(f"{base}/{client_token}/")
    if status != 200 or "rsg-root" not in index:
        raise ResamaniaError(
            f"tenant '{client_token}' : SPA membre introuvable sur {base} "
            f"(status {status}) — clientToken probablement faux")

    m = re.search(r'src="(/assets/index-[^"]+\.js)"', index)
    if not m:
        raise ResamaniaError(f"tenant '{client_token}' : bundle JS introuvable")
    status, bundle = _request(base + m.group(1), timeout=90)
    if status != 200:
        raise ResamaniaError(f"tenant '{client_token}' : bundle HTTP {status}")

    cfg = {"client_token": client_token}
    for key, pat in _ENV_PATTERNS.items():
        mm = re.search(pat, bundle)
        cfg[key] = mm.group(1) if mm else None
    cfg.setdefault("api_base", None)
    if not cfg["api_base"]:
        cfg["api_base"] = API_HOST.format(token=client_token)
    if not cfg["oauth_base"]:
        cfg["oauth_base"] = cfg["api_base"]
    _TENANT_CACHE[client_token] = cfg
    return cfg


def groot_config(client_token):
    """Config publique du tenant (nom du client, planning:allow, filtres...)."""
    url = (GROOT_HOST.format(token=client_token)
           + f"/api/{client_token}/resa2-member/config/default/1.0")
    status, data = _get_json(url)
    if status != 200:
        raise ResamaniaError(f"groot {client_token} : HTTP {status}")
    return data


# --------------------------------------------------------------------------
# OAuth2
# --------------------------------------------------------------------------
def _oauth(cfg, payload):
    url = f"{cfg['oauth_base']}/{cfg['client_token']}/oauth/v2/token"
    status, body = _request(url, data=payload, headers={"Accept": "application/json"})
    try:
        data = json.loads(body)
    except ValueError:
        raise ResamaniaError(f"oauth {cfg['client_token']} : réponse illisible ({status})")
    if status != 200 or "access_token" not in data:
        raise ResamaniaAuthRequired(
            f"oauth {cfg['client_token']} : {data.get('error')} "
            f"— {data.get('error_description')}")
    return data["access_token"]


def anonymous_token(client_token):
    """JWT ROLE_DESK, sans compte. Scope restreint (souvent /clubs seulement)."""
    cfg = discover_tenant(client_token)
    if not cfg.get("anon_client_id"):
        raise ResamaniaAuthRequired(f"{client_token} : pas de client anonyme publié")
    return _oauth(cfg, {"grant_type": "client_credentials",
                        "client_id": cfg["anon_client_id"],
                        "client_secret": cfg["anon_client_secret"]})


def member_token(client_token, username=None, password=None):
    """JWT membre (grant_type=password). Nécessite un compte réel du club.

    Identifiants lus dans l'env si non passés :
        RESAMANIA_<CLIENTTOKEN>_USER / RESAMANIA_<CLIENTTOKEN>_PASS
    """
    env = client_token.upper().replace("-", "_")
    username = username or os.environ.get(f"RESAMANIA_{env}_USER")
    password = password or os.environ.get(f"RESAMANIA_{env}_PASS")
    if not (username and password):
        raise ResamaniaAuthRequired(
            f"{client_token} : compte membre requis "
            f"(RESAMANIA_{env}_USER / RESAMANIA_{env}_PASS non définis)")
    cfg = discover_tenant(client_token)
    return _oauth(cfg, {"grant_type": "password",
                        "client_id": cfg["client_id"],
                        "client_secret": cfg["client_secret"],
                        "username": username, "password": password})


def get_token(client_token, username=None, password=None):
    """Membre si des identifiants existent, sinon anonyme."""
    try:
        return member_token(client_token, username, password), "member"
    except ResamaniaAuthRequired:
        return anonymous_token(client_token), "anonymous"


# --------------------------------------------------------------------------
# API : class_events
# --------------------------------------------------------------------------
def api_get(client_token, path, params=None, bearer=None):
    cfg = discover_tenant(client_token)
    url = f"{cfg['api_base']}/{client_token}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    headers = {"Accept": "application/ld+json"}
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    status, data = _get_json(url, headers=headers)
    if status in (401, 403):
        desc = data.get("hydra:description") or data.get("message") or ""
        raise ResamaniaAuthRequired(f"{path} : HTTP {status} — {desc[:180]}")
    if status != 200:
        raise ResamaniaError(f"{path} : HTTP {status}")
    return data


def _name_of(node, *fields):
    """Un champ hydra est soit un objet embarqué, soit une IRI string."""
    if isinstance(node, dict):
        for f in fields:
            v = node.get(f)
            if v:
                return str(v).strip()
        return None
    return None


def _coach_name(coach):
    if not isinstance(coach, dict):
        return None
    alt = coach.get("alternateName")
    if alt:
        return str(alt).strip()
    given = (coach.get("givenName") or "").strip()
    family = (coach.get("familyName") or "").strip()
    full = f"{given} {family}".strip()
    return full or None


def normalize_class_event(ev):
    """ClassEvent Hydra -> dict normalisé de l'engine."""
    booked = ev.get("bookedAttendees")
    queued = ev.get("queuedAttendees")
    presents = len(booked) if isinstance(booked, list) else ev.get("currentAttending")
    capacite = ev.get("attendingLimit")
    if capacite in (None, 0):
        capacite = ev.get("onlineLimit") or ev.get("defaultOnlineLimit") or None

    start = ev.get("startedAt")
    end = ev.get("endedAt")
    if start and not end and ev.get("duration"):
        try:
            end = (dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
                   + dt.timedelta(minutes=int(ev["duration"]))).isoformat()
        except (ValueError, TypeError):
            end = None

    lieu = _name_of(ev.get("studio"), "name") or _name_of(ev.get("club"), "name")
    return {
        "id": str(ev.get("@id") or ev.get("id") or "").rsplit("/", 1)[-1] or None,
        "start": start,
        "end": end,
        "cours": _name_of(ev.get("activity"), "name") or (ev.get("name") or None),
        "coach": _coach_name(ev.get("coach")),
        "lieu": lieu,
        "capacite": capacite,
        "presents": presents,
        "club": _name_of(ev.get("club"), "name"),
        "queued": len(queued) if isinstance(queued, list) else None,
        "statut": "annule" if ev.get("canceled") else None,
        "source": "resamania_api",
    }


def api_sessions(client_token, days=14, club=None, bearer=None, max_pages=60):
    """Créneaux via l'API Resamania. Lève ResamaniaAuthRequired si le scope
    du token ne couvre pas class_events."""
    if bearer is None:
        bearer, _kind = get_token(client_token)
    today = dt.date.today()
    params_base = {
        "startedAt[after]": today.isoformat(),
        "startedAt[before]": (today + dt.timedelta(days=days)).isoformat(),
        "order[startedAt]": "asc",
    }
    if club:
        params_base["club"] = club

    out, page = [], 1
    while page <= max_pages:
        params = dict(params_base, page=page)
        data = api_get(client_token, "/class_events", params, bearer)
        members = data.get("hydra:member") or []
        if not members:
            break
        out.extend(normalize_class_event(e) for e in members)
        if not (data.get("hydra:view") or {}).get("hydra:next"):
            break
        page += 1
    return out


# --------------------------------------------------------------------------
# Adaptateur public 1 — Cercles de la Forme (proxy WordPress)
# --------------------------------------------------------------------------
CDF_SITE = "https://www.cerclesdelaforme.com"
CDF_AJAX = CDF_SITE + "/wp-admin/admin-ajax.php"
CDF_PLANNING = CDF_SITE + "/horaires/"


def _strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _cdf_club_names():
    """Map club_id -> libellé, publiée dans le JS de /horaires/."""
    status, page = _request(CDF_PLANNING)
    if status != 200:
        return {}
    m = re.search(r"var\s+list_club_id\s*=\s*(\{.*?\});", page, re.S)
    if not m:
        return {}
    try:
        raw = json.loads(m.group(1))
    except ValueError:
        return {}
    return {k: _strip_tags(v) for k, v in raw.items()}


def _week_monday(week):
    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())
    return monday + dt.timedelta(days=7) if week == "next" else monday


def cdf_wp_sessions(days=14, club=None, max_pages=200):
    """Grille Resamania proxifiée par le plugin WP de Cercles de la Forme.

    Renvoie des séances datées (semaine courante + suivante). capacite et
    presents sont None : le proxy ne les expose pas.
    """
    clubs = _cdf_club_names()
    seen, page = {}, 0
    while page < max_pages:
        payload = {"action": "search_event", "adulte": 1, "enfant": 1,
                   "club": club or "", "active": "", "jour": "", "duree": "",
                   "start": "", "end": "", "page": page, "type": "main",
                   "colorder": ""}
        status, body = _request(CDF_AJAX, data=payload, headers={
            "X-Requested-With": "XMLHttpRequest", "Referer": CDF_PLANNING,
            "Accept": "application/json, */*"})
        if status != 200:
            raise ResamaniaError(f"cdf search_event : HTTP {status}")
        try:
            data = json.loads(body)
        except ValueError:
            raise ResamaniaError("cdf search_event : réponse non JSON")
        rows = data.get("list") or []
        if not rows:
            break
        before = len(seen)
        for e in rows:
            seen[(e.get("eve_id"), e.get("week"))] = e
        if len(seen) == before:          # plus rien de neuf -> fin réelle
            break
        page += 1

    horizon = dt.date.today() + dt.timedelta(days=days)
    out = []
    for (eve_id, week), e in seen.items():
        try:
            jour = int(e["jour"])                 # 1 = lundi ... 7 = dimanche
            h, mi = int(e["h_start"]), int(e.get("m_start") or 0)
            duree = int(e.get("duree") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        day = _week_monday(week) + dt.timedelta(days=jour - 1)
        if day > horizon:
            continue
        start = dt.datetime.combine(day, dt.time(hour=h % 24, minute=mi))
        end = start + dt.timedelta(minutes=duree) if duree else None
        club_lbl = clubs.get(str(e.get("club_id"))) or _strip_tags(e.get("clubtitle"))
        out.append({
            "id": f"cdf-{eve_id}-{day.isoformat()}",
            "start": start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds") if end else None,
            "cours": (e.get("titre") or "").strip() or None,
            "coach": None,
            "lieu": (e.get("salle") or "").strip() or club_lbl or None,
            "capacite": None,
            "presents": None,
            "club": club_lbl or None,
            "queued": None,
            "statut": None,
            "source": "cdf_wp_proxy",
        })
    out.sort(key=lambda s: s["start"])
    return out


# --------------------------------------------------------------------------
# Adaptateur public 2 — OZEN HIT (planning rendu côté serveur)
# --------------------------------------------------------------------------
OZEN_PLANNING = "https://ozenhit.com/planning/"

_OZ_TAB = re.compile(
    r'tabs__nav__link[^>]*>(?P<label>[^<]*)</a>')
_OZ_PANE = re.compile(
    r'tabs__content__item[^>]*data-studio-id="(?P<sid>[^"]*)"(?P<body>.*?)(?=tabs__content__item|\Z)',
    re.S)
_OZ_DAY = re.compile(
    r'day__name">(?P<label>[^<]*)</div>(?P<body>.*?)(?=day__name"|\Z)', re.S)
_OZ_EVENT = re.compile(
    r'event__time">(?P<t>\d{1,2}:\d{2})\s*-\s*(?P<t2>\d{1,2}:\d{2})</div>'
    r'(?P<rest>.*?)(?=event__time"|\Z)', re.S)


def ozenhit_sessions(days=14, club=None, **_kw):  # noqa: ARG001 (signature commune)
    """Planning OZEN HIT. Le nom du cours n'est publié que sous forme de logo
    image -> `cours` reste None (on ne l'invente pas)."""
    status, page = _request(OZEN_PLANNING)
    if status != 200:
        raise ResamaniaError(f"ozenhit planning : HTTP {status}")

    labels = [html.unescape(m.group("label")).strip()
              for m in _OZ_TAB.finditer(page)]
    studios = {}
    panes = []
    for i, pm in enumerate(_OZ_PANE.finditer(page)):
        sid = pm.group("sid").strip("'\" ")
        label = labels[i] if i < len(labels) else sid
        studios[sid] = label
        panes.append((sid, label, pm.group("body")))
    if not panes:
        raise ResamaniaError("ozenhit : structure de planning non reconnue")

    today = dt.date.today()
    horizon = today + dt.timedelta(days=days)
    out = []
    for sid, label, body in panes:
        for dm in _OZ_DAY.finditer(body):
            dl = re.sub(r"\s+", " ", html.unescape(dm.group("label"))).strip()
            mday = re.search(r"(\d{1,2})\s*$", dl)
            if not mday:
                continue
            dom = int(mday.group(1))
            day = _resolve_day_of_month(today, dom)
            if day > horizon:
                continue
            for em in _OZ_EVENT.finditer(dm.group("body")):
                h1, m1 = (int(x) for x in em.group("t").split(":"))
                h2, m2 = (int(x) for x in em.group("t2").split(":"))
                start = dt.datetime.combine(day, dt.time(h1, m1))
                end = dt.datetime.combine(day, dt.time(h2, m2))
                if end <= start:
                    end += dt.timedelta(days=1)
                logo = re.search(r'data-src=[\'"]?([^\'" >]+)', em.group("rest"))
                out.append({
                    "id": f"ozenhit-{sid}-{start.isoformat(timespec='minutes')}",
                    "start": start.isoformat(timespec="seconds"),
                    "end": end.isoformat(timespec="seconds"),
                    "cours": None,
                    "coach": None,
                    "lieu": label,
                    "capacite": None,
                    "presents": None,
                    "club": "OZEN HIT",
                    "queued": None,
                    "statut": None,
                    "source": "ozenhit_html",
                    "logo": logo.group(1) if logo else None,
                })
    out.sort(key=lambda s: s["start"])
    return out


def _resolve_day_of_month(today, dom):
    """Le planning n'affiche que le quantième : on choisit l'occurrence la plus
    proche dans la fenêtre [-3 j, +14 j]."""
    best = None
    for delta_month in (0, 1, -1):
        y, m = today.year, today.month + delta_month
        y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
        try:
            cand = dt.date(y, m, dom)
        except ValueError:
            continue
        if best is None or abs((cand - today).days) < abs((best - today).days):
            best = cand
    return best or today


# --------------------------------------------------------------------------
# Registre des sources
# --------------------------------------------------------------------------
SOURCES = {
    # Cercles de la Forme : API tenant `cdf` (membre requis) + proxy WP public.
    "cdf": {"client_token": "cdf", "fallback": cdf_wp_sessions,
            "label": "Cercles de la Forme"},
    "cercles_forme": {"alias": "cdf"},
    "cerclesdelaforme": {"alias": "cdf"},

    # OZEN HIT : tenant Resamania non exposé, planning public HTML.
    "ozenhit": {"client_token": None, "fallback": ozenhit_sessions,
                "label": "OZEN HIT"},
    "ozen": {"alias": "ozenhit"},

    # La Montgolfière : sessions derrière l'espace membre (proxy WP
    # resamania_login). Aucun accès public.
    "montgolfiere_toudic": {"client_token": None, "fallback": None,
                            "label": "La Montgolfière Toudic",
                            "note": "espace membre uniquement (resamania_login)"},
    "montgolfiere_lamarck": {"alias": "montgolfiere_toudic"},
}


def resolve_source(slug_or_id):
    """slug/clé de marque -> descripteur. Un clientToken inconnu du registre
    est traité comme un tenant Resamania direct."""
    key = (slug_or_id or "").strip().lower()
    seen = set()
    while key in SOURCES and "alias" in SOURCES[key]:
        if key in seen:
            break
        seen.add(key)
        key = SOURCES[key]["alias"]
    if key in SOURCES:
        return key, SOURCES[key]
    # tenant inconnu : on tente l'API directement
    return key, {"client_token": key, "fallback": None, "label": key}


def fetch_sessions(slug_or_id, days=14, club=None, prefer="auto", **kwargs):
    """Créneaux normalisés d'une marque Resamania.

    slug_or_id : clé de marque du catalogue (`cercles_forme`, `ozenhit`, ...)
                 ou clientToken Resamania brut (`cdf`).
    prefer     : "auto" (API puis fallback), "api", "public".

    Retourne list[dict] {id, start, end, cours, coach, lieu, capacite, presents}
    (+ club, source, statut). Lève ResamaniaAuthRequired si aucune source
    accessible.
    """
    key, src = resolve_source(slug_or_id)
    client_token = src.get("client_token")
    fallback = src.get("fallback")
    errors = []

    if prefer in ("auto", "api") and client_token:
        try:
            sessions = api_sessions(client_token, days=days, club=club, **kwargs)
            if sessions:
                return sessions
            errors.append("API : 0 créneau")
        except ResamaniaAuthRequired as e:
            errors.append(f"API : {e}")
        except ResamaniaError as e:
            errors.append(f"API : {e}")
        if prefer == "api":
            raise ResamaniaAuthRequired(f"{key} — {' | '.join(errors)}")

    if prefer in ("auto", "public") and fallback:
        return fallback(days=days, club=club)

    note = src.get("note") or ""
    raise ResamaniaAuthRequired(
        f"{key} : aucune source de créneaux accessible. {note} "
        + (" | ".join(errors) if errors else ""))


# --------------------------------------------------------------------------
# CLI de test
# --------------------------------------------------------------------------
def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        print("usage: engine_resamania.py <slug|clientToken> [--days N] [--prefer auto|api|public]")
        print("       sources connues :", ", ".join(sorted(SOURCES)))
        return 2
    slug = argv[0]
    days = 14
    prefer = "auto"
    if "--days" in argv:
        days = int(argv[argv.index("--days") + 1])
    if "--prefer" in argv:
        prefer = argv[argv.index("--prefer") + 1]
    try:
        sessions = fetch_sessions(slug, days=days, prefer=prefer)
    except ResamaniaError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    print(f"{len(sessions)} créneaux pour {slug} (fenêtre {days} j)")
    srcs = {}
    for s in sessions:
        srcs[s["source"]] = srcs.get(s["source"], 0) + 1
    print("  sources :", srcs)
    for s in sessions[:5]:
        print("   ", json.dumps(s, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
