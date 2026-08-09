#!/usr/bin/env python3
"""Engine Mindbody « healcode » — planning des studios qui embarquent un
<healcode-widget data-type="schedules" ...>.

CE QU'EST LE HEALCODE (et en quoi il diffère du BW de banote/le33foch)
---------------------------------------------------------------------
Les pages studio embarquent widgets.mindbodyonline.com/javascripts/healcode.js
et un custom element :

    <healcode-widget data-type="schedules" data-widget-partner="object"
                     data-widget-id="1020364318b6" data-widget-version="1">

Les balises `data-type="*-link"` (account-link, cart-link…) portent en plus
data-site-id / data-mb-site-id : ce sont les identifiants du STUDIO, pas du
planning. Le planning est adressé par data-widget-id, jamais par le site id —
d'où la fonction fetch_sessions(site_id, ...) qui commence par résoudre un
widget_id (registre SITES, argument explicite, ou discovery sur le site).

ENDPOINT RÉEL (lu dans le bundle officiel .../widgets/schedule/load_markup-*.js)
-------------------------------------------------------------------------------
    GET https://widgets.mindbodyonline.com/widgets/schedules/<widget_id>/load_markup
        ?options[start_date]=YYYY-MM-DD
        &options[location]=<id>     (facultatif)
        &preview=false
        &callback=<nom js>          (facultatif : voir plus bas)
    -> {"class_sessions":"<html>","calendar":"<html>","filters":{…}}
       ou <nom js>({…}) si callback est fourni

C'est le MÊME endpoint que banote_fetch / le33foch_fetch / dna_scrape : le
healcode ne se distingue pas par son URL mais par la façon dont on trouve le
widget_id (balise <healcode-widget> au lieu d'un id en dur dans le script).
Le bundle officiel l'appelle en `dataType: "jsonp"`, donc avec un `callback` ;
l'appel sans callback marche aussi et rend du JSON brut. On envoie le callback
pour coller au comportement du widget, et le parseur accepte les DEUX formes.

Trois pièges observés en production (2026-08-09) :
  · THROTTLING. Mindbody limite le débit par IP : en rafale, l'endpoint rend
    des HTTP 500 (voire 405) intermittents sur des widgets pourtant valides,
    puis reredevient normal une fois le rythme calmé. C'est ce qui fait rendre
    « 0 séance » aux scrapers Mindbody sur les runners GitHub quand ils
    s'enchaînent. D'où : une pause entre chaque fenêtre (PACE_SECONDS) et un
    backoff exponentiel sur retry. Ne pas conclure trop vite qu'un widget est
    mort sur un seul 500.
  · widget inconnu / mal configuré -> réponse 200 mais page HTML Branded Web
    (« Business owner: There is an issue with the Branded Web widget »).
    On la détecte et on lève WidgetUnavailable au lieu de rendre 0 séance.
  · nouveau widget « BW V2 » (<div class="mindbody-widget" data-widget-type=
    "Schedules" data-widget-id="…">, iframe go.mindbodyonline.com) : ces
    widget_id ne vivent PAS dans le namespace healcode. discover_widgets() les
    remonte séparément sous la clé "bw_v2" — ils demandent Playwright.

Sortie : liste de dicts au même format que banote_fetch.fetch_all()
    {id, start, end, cours, coach, lieu, cart, canceled, widget}

Usage CLI :
    python3 engine_mindbody_healcode.py fetch 42787
    python3 engine_mindbody_healcode.py widget 1020364318b6
    python3 engine_mindbody_healcode.py discover https://www.yujparis.com/
"""
import datetime as dt
import html as _html
import json
import re
import ssl
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

BASE = "https://widgets.mindbodyonline.com/widgets"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0 Safari/537.36")

# Pause entre deux appels load_markup. Mindbody rend des 500/405 intermittents
# quand on enchaîne les requêtes sans respirer ; 1,5 s a suffi à tenir 6 appels
# d'affilée sans une seule erreur là où la rafale en produisait à chaque tour.
PACE_SECONDS = 1.5
_last_call = [0.0]


def _pace():
    delta = time.time() - _last_call[0]
    if 0 < delta < PACE_SECONDS:
        time.sleep(PACE_SECONDS - delta)
    _last_call[0] = time.time()

# Contexte SSL tolérant, comme banote_fetch : la sandbox a parfois une horloge
# décalée -> certificat « not yet valid ». Utilisé seulement en repli.
_LAX_SSL = ssl.create_default_context()
_LAX_SSL.check_hostname = False
_LAX_SSL.verify_mode = ssl.CERT_NONE


class WidgetUnavailable(RuntimeError):
    """Le widget existe côté Mindbody mais ne rend pas de planning."""


# ---------------------------------------------------------------- registre --
# site_id healcode (= data-site-id, celui qu'on retrouve dans les URLs
# cart.mindbodyonline.com/sites/<id>/) -> widgets planning constatés en live.
# `platform` sert à documenter les studios Mindbody qui ne sont PAS/PLUS
# joignables en healcode : on préfère un message franc à un 0 silencieux.
SITES = {
    "42787": {                                   # YUJ Paris (5 studios)
        "widget_ids": ["1020364318b6"],
        "referer": "https://www.yujparis.com/",
        "lieu": "YUJ Paris",
        "pages": ["https://www.yujparis.com/pages/planning-des-studios"],
    },
    "39353": {                                   # Reformation Pilates
        "widget_ids": [],
        "referer": "https://www.reformation-pilates.com/",
        "lieu": "Reformation Pilates",
        "pages": ["https://www.reformation-pilates.com/planning-reservation"],
        "engine": "bw_v2",
        "note": ("migré sur le widget Branded Web V2 (iframe "
                 "go.mindbodyonline.com/book/widgets/schedules/view/<id>/schedule, "
                 "Next.js + server actions) — pas de load_markup, Playwright requis"),
    },
    "128181": {                                  # Studio On (ex-Mindbody)
        "widget_ids": [],
        "referer": "https://studioon.fr/",
        "lieu": "Studio On",
        "pages": ["https://studioon.fr/reserver/paris17/"],
        "engine": "sportigo",
        "note": ("a quitté Mindbody — la page /reserver/ tape désormais "
                 "standalone.api.sportigo.fr ; le widget 6434318df2d du "
                 "resolved est mort. À rebasculer sur l'engine sportigo."),
    },
}

# Pages où un widget planning a le plus de chances de vivre. Testées dans
# l'ordre, on s'arrête au premier hit.
CANDIDATE_PATHS = (
    "", "/planning", "/planning-reservation", "/reservation", "/reserver",
    "/booking", "/horaires", "/cours", "/classes", "/schedule",
    "/pages/planning-des-studios", "/pages/planning", "/planning/",
)

# Les studios rangent souvent leur planning sous une URL maison
# (/pages/planning-des-studios chez YUJ…). Quand les chemins standards ne
# donnent rien, on suit les liens internes dont l'URL sent le planning.
_LINK_HINT = re.compile(
    r"planning|reserv|booking|horaire|schedule|cours|classes", re.I)
_LINK_STRONG = re.compile(r"planning|reserv|booking|horaire|schedule", re.I)
_HREF = re.compile(r'href="([^"#?]+)"', re.I)


# ------------------------------------------------------------------- HTTP --
def http_get(url, referer=None, accept="text/html,*/*", retries=3, timeout=45):
    """GET tolérant. Renvoie le corps décodé, ou None si tout a échoué."""
    headers = {"User-Agent": UA, "Accept": accept}
    if referer:
        headers["Referer"] = referer
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            ctx = _LAX_SSL if attempt else None   # 1er essai = vérif normale
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:                    # noqa: BLE001
            last = e
            if attempt < retries - 1:
                # Backoff 2s, 4s, 8s… Mindbody throttle les IP de CI.
                time.sleep(2 ** (attempt + 1))
    print(f"  (GET échoué {url} : {last})", file=sys.stderr)
    return None


# -------------------------------------------------------------- discovery --
_HEALCODE_TAG = re.compile(r"<healcode-widget\b[^>]*>", re.I)
_BW_V2_TAG = re.compile(
    r'<div\b[^>]*class="[^"]*\bmindbody-widget\b[^"]*"[^>]*>', re.I)
_ATTR = re.compile(r'\b(data-[a-z0-9-]+)\s*=\s*"([^"]*)"', re.I)


def parse_widget_tags(html_str):
    """Extrait les <healcode-widget> et les <div class="mindbody-widget"> (V2).

    Renvoie (healcode_tags, bw_v2_tags) : deux listes de dicts d'attributs.
    """
    healcode, bw_v2 = [], []
    for m in _HEALCODE_TAG.finditer(html_str or ""):
        healcode.append(dict(_ATTR.findall(m.group(0))))
    for m in _BW_V2_TAG.finditer(html_str or ""):
        bw_v2.append(dict(_ATTR.findall(m.group(0))))
    return healcode, bw_v2


def discover_widgets(url, extra_paths=(), max_pages=8):
    """Sonde le site d'une marque et remonte ses widgets Mindbody.

    Renvoie {"schedules": [ids healcode], "bw_v2": [ids V2], "site_id": …,
             "mb_site_id": …, "referer": …, "pages": [urls sondées]}.
    """
    root = (url or "").rstrip("/")
    if not root:
        return {"schedules": [], "bw_v2": [], "pages": []}

    cands, seen_u = [], set()
    for p in tuple(extra_paths) + CANDIDATE_PATHS:
        u = p if p.startswith("http") else root + p
        if u not in seen_u:
            seen_u.add(u)
            cands.append(u)

    out = {"schedules": [], "bw_v2": [], "site_id": None, "mb_site_id": None,
           "referer": root + "/", "pages": []}
    queue, budget = cands[:max_pages], max_pages + 4
    # seen_u couvrait TOUS les chemins standards, y compris ceux tronqués par
    # max_pages : on le recale sur ce qu'on va réellement sonder, sinon un lien
    # interne pointant vers un chemin tronqué serait écarté à tort.
    seen_u = set(queue)
    hinted, followed = [], False
    while budget > 0:
        if not queue:
            if followed or not hinted:
                break
            # « planning »/« réserver » avant « cours »/« classes » : bien plus
            # souvent la page qui porte réellement le widget.
            hinted.sort(key=lambda u: 0 if _LINK_STRONG.search(u) else 1)
            followed, queue = True, hinted[:4]
        cand = queue.pop(0)
        budget -= 1
        html_str = http_get(cand, referer=root + "/", retries=1)
        if not html_str:
            continue
        out["pages"].append(cand)
        healcode, bw_v2 = parse_widget_tags(html_str)
        for t in healcode:
            out["site_id"] = out["site_id"] or t.get("data-site-id")
            out["mb_site_id"] = out["mb_site_id"] or t.get("data-mb-site-id")
            wid = t.get("data-widget-id")
            if wid and t.get("data-type") == "schedules" and wid not in out["schedules"]:
                out["schedules"].append(wid)
        for t in bw_v2:
            wid = t.get("data-widget-id")
            if (wid and (t.get("data-widget-type") or "").lower() == "schedules"
                    and wid not in out["bw_v2"]):
                out["bw_v2"].append(wid)
        if out["schedules"]:
            break
        # Rien sur les chemins standards : on mémorise les liens internes qui
        # ressemblent à un planning, sondés en second tour si besoin.
        if not followed and "healcode" in html_str:
            for href in _HREF.findall(html_str):
                if not _LINK_HINT.search(href):
                    continue
                u = href if href.startswith("http") else \
                    root + "/" + href.lstrip("/")
                if u.startswith(root) and u not in seen_u:
                    seen_u.add(u)
                    hinted.append(u)
        time.sleep(0.4)
    return out


# ------------------------------------------------------------- load_markup --
_ERROR_MARKERS = (
    "There is an issue with the Branded Web widget",
    "Widget not found",
)


def _visible_text(body):
    """Texte lisible d'une page d'erreur Mindbody (sans <script>/<style>)."""
    t = re.sub(r"<script.*?</script>", " ", body or "", flags=re.S | re.I)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip()


def _callback_name():
    """Nom de callback JSONP, dans le style de ce que jQuery génère."""
    return "hcjq%d" % int(time.time() * 1000 % 10_000_000_000)


def _strip_jsonp(body, cb):
    """Renvoie le dict JSON, que la réponse soit du JSONP ou du JSON brut.

    L'endpoint rend du JSON nu quand on n'envoie pas de callback, et du
    `cb({...})` quand on en envoie un — on ne suppose ni l'un ni l'autre.
    """
    body = (body or "").strip()
    if body.startswith("{"):
        return json.loads(body)
    if body.startswith(cb) and "(" in body and body.endswith(")"):
        return json.loads(body[body.index("(") + 1: body.rindex(")")])
    head = _visible_text(body)
    for marker in _ERROR_MARKERS:
        if marker in body:
            raise WidgetUnavailable(f"widget refusé par Mindbody ({head[:140]})")
    raise WidgetUnavailable(f"réponse inattendue ({head[:140]})")


def load_markup(widget_id, start_date=None, location=None, referer=None,
                retries=4):
    """Appelle load_markup pour un widget healcode. Renvoie le dict JSON.

    Lève WidgetUnavailable si Mindbody répond une page d'erreur, ou la
    dernière exception réseau si tout a échoué.
    """
    cb = _callback_name()
    params = {"preview": "false", "callback": cb}
    if start_date:
        params["options[start_date]"] = start_date
    if location is not None:
        params["options[location]"] = location
    url = (f"{BASE}/schedules/{widget_id}/load_markup?"
           + urllib.parse.urlencode(params))
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer

    last = None
    for attempt in range(retries):
        try:
            _pace()
            req = urllib.request.Request(url, headers=headers)
            ctx = _LAX_SSL if attempt else None
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                return _strip_jsonp(r.read().decode("utf-8", "ignore"), cb)
        except WidgetUnavailable:
            raise                                  # inutile d'insister
        except Exception as e:                     # noqa: BLE001
            last = e
            if attempt < retries - 1:
                # Backoff 3s, 6s, 12s : Mindbody rend des 500 intermittents et
                # throttle les IP GitHub Actions quand plusieurs scrapers
                # Mindbody s'enchaînent.
                time.sleep(3 * (2 ** attempt))
    raise last


# ------------------------------------------------------------------ parse --
def _clean(m):
    if not m:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()


def parse_sessions(markup, fallback_lieu=""):
    """Parse le HTML `class_sessions` -> liste de séances.

    Même schéma que banote_fetch.parse_sessions (id/start/end/cours/coach/
    lieu/cart/canceled) pour rester interchangeable avec les autres engines.

    ⚠️ IDENTIFIANT : on clé sur `data-bw-widget-mbo-class-id` (le ClassID
    Mindbody, celui repris dans l'URL panier item[mbo_id]), PAS sur
    `data-bw-widget-id`. Vérifié le 2026-08-09 sur le widget YUJ : la même
    séance du 12/08 15h30 porte data-bw-widget-id=27124660, 75179120 ou
    77478840 selon la fenêtre demandée (start_date=08, 09 ou 10/08) — c'est
    un id de DOM régénéré à chaque rendu. Le mbo-class-id, lui, ne bouge pas
    (464527 dans les trois cas) et reste unique (211/211 sur 6 semaines).
    Une clé instable ferait regonfler le store à chaque passage.
    """
    markup = _html.unescape(markup or "")
    out = []
    for block in re.split(r'(?=<div class="bw-session" )', markup):
        if 'class="bw-session"' not in block:
            continue
        dom_id = re.search(r'data-bw-widget-id="(\d+)"', block)
        mbo_id = re.search(r'data-bw-widget-mbo-class-id="(\d+)"', block)
        loc_id = re.search(r'data-bw-widget-location="(\d+)"', block)
        sdt = re.search(r'<time class="hc_starttime" datetime="([^"]+)"', block)
        edt = re.search(r'<time class="hc_endtime" datetime="([^"]+)"', block)
        if not sdt or not (mbo_id or dom_id):
            continue
        nm = re.search(r'<div class="bw-session__name">(.*?)</div>', block, re.S)
        staff = re.search(r'<div class="bw-session__staff"[^>]*>(.*?)</div>', block, re.S)
        locn = re.search(r'<div class="bw-session__location"[^>]*>(.*?)</div>', block, re.S)
        cart = _clean(re.search(r'<span class="bw-widget__cart_button">(.*?)</span>',
                                block, re.S))
        # NB : .bw-session__canceled « Annulé » est un gabarit présent sur
        # CHAQUE séance (affiché par JS seulement si réellement annulée) ->
        # ce n'est pas un indicateur exploitable. Même constat que banote.
        cours = _clean(nm)
        cours = re.sub(r"^[A-Za-z]+\s*[-–]\s*", "", cours).strip() or cours
        # Repli si Mindbody omet le mbo-class-id : clé métier déterministe
        # (début + salle + cours), stable elle aussi d'un run à l'autre.
        stable = (mbo_id.group(1) if mbo_id else
                  "%s|%s|%s" % (sdt.group(1),
                                loc_id.group(1) if loc_id else "",
                                cours))
        out.append({
            "id": stable,
            "dom_id": dom_id.group(1) if dom_id else "",
            "start": sdt.group(1),
            "end": edt.group(1) if edt else "",
            "cours": cours,
            "coach": _clean(staff),
            "lieu": _clean(locn) or fallback_lieu,
            "cart": cart,
            "canceled": False,
        })
    return out


_CAL_DAY = re.compile(r'data-bw-startdate="(\d{4}-\d{2}-\d{2})"')


def _calendar_last_day(payload):
    """Dernier jour couvert par la fenêtre renvoyée (via le mini-calendrier)."""
    days = _CAL_DAY.findall(payload.get("calendar") or "")
    return max(days) if days else None


# ------------------------------------------------------------------ fetch --
def fetch_widget(widget_id, referer=None, lieu="", start_date=None,
                 days=56, location=None, max_windows=8):
    """Balaie l'horizon d'un widget healcode fenêtre par fenêtre.

    load_markup ne rend qu'une quinzaine de jours autour de start_date ; on
    enchaîne les fenêtres en repartant du lendemain du dernier jour couvert
    (lu dans le calendrier renvoyé), avec un repli à +7 jours.
    """
    start = dt.date.fromisoformat(start_date) if start_date else dt.date.today()
    horizon = start + dt.timedelta(days=days)
    seen, cursor = {}, start
    for _ in range(max_windows):
        if cursor > horizon:
            break
        payload = load_markup(widget_id, cursor.isoformat(), location, referer)
        fresh = 0
        for s in parse_sessions(payload.get("class_sessions"), lieu):
            if s["id"] not in seen:
                s["widget"] = widget_id
                seen[s["id"]] = s
                fresh += 1
        last = _calendar_last_day(payload)
        nxt = (dt.date.fromisoformat(last) + dt.timedelta(days=1)) if last \
            else cursor + dt.timedelta(days=7)
        if nxt <= cursor:                          # garde anti-boucle infinie
            nxt = cursor + dt.timedelta(days=7)
        cursor = nxt
    return list(seen.values())


def _site_cfg(site_id):
    return SITES.get(str(site_id or "")) or {}


def fetch_sessions(site_id, widget_id=None, referer=None, lieu=None,
                   start_date=None, days=56, location=None, url=None):
    """Séances d'un studio healcode. Point d'entrée de l'engine.

    site_id : identifiant healcode du studio (data-site-id, alias mb_site_id
              dans *_extension_resolved.json). Sert à retrouver le ou les
              widget_id via SITES, sinon on tente une discovery sur `url`.
    widget_id : court-circuite la résolution (str ou liste).

    Renvoie [] — jamais d'exception — si le studio n'est pas joignable en
    healcode ; le motif part sur stderr.
    """
    cfg = _site_cfg(site_id)
    referer = referer or cfg.get("referer") or url
    lieu = lieu if lieu is not None else cfg.get("lieu", "")

    if widget_id:
        widgets = [widget_id] if isinstance(widget_id, str) else list(widget_id)
    else:
        widgets = list(cfg.get("widget_ids") or [])

    explained = []          # motifs déjà signalés, pour ne pas les répéter

    def _discover():
        """Sonde le site de la marque. Renvoie (widget_ids, referer)."""
        page = url or (cfg.get("pages") or [None])[0]
        if not page:
            return [], referer
        found = discover_widgets(page, extra_paths=cfg.get("pages", ()))
        if not found.get("schedules") and found.get("bw_v2"):
            explained.append("bw_v2")
            print(f"  ⚠️  site {site_id} : widget Branded Web V2 "
                  f"({', '.join(found['bw_v2'])}) — pas de load_markup, "
                  f"Playwright requis", file=sys.stderr)
        return found.get("schedules") or [], (referer or found.get("referer"))

    discovered = False
    if not widgets:
        widgets, referer = _discover()
        discovered = True

    if not widgets:
        if cfg.get("engine") not in explained:
            note = cfg.get("note") or "aucun widget planning healcode trouvé"
            print(f"  ⚠️  site {site_id} : {note}", file=sys.stderr)
        return []

    def _harvest(ids):
        found, seen = [], set()
        for wid in ids:
            try:
                got = fetch_widget(wid, referer=referer, lieu=lieu,
                                   start_date=start_date, days=days,
                                   location=location)
            except WidgetUnavailable as e:
                print(f"  ⚠️  widget {wid} indisponible : {e}", file=sys.stderr)
                continue
            except Exception as e:                 # noqa: BLE001
                print(f"  ⚠️  widget {wid} : {e}", file=sys.stderr)
                continue
            for s in got:
                if s["id"] not in seen:
                    seen.add(s["id"])
                    found.append(s)
        return found

    sessions = _harvest(widgets)
    if not sessions and not discovered:
        # widget_id du resolved périmé (le studio a refait son site / changé
        # de widget) -> on retente une fois via discovery avant d'abandonner.
        fresh, referer = _discover()
        fresh = [w for w in fresh if w not in widgets]
        if fresh:
            print(f"  ↻ widget périmé, redécouverte : {', '.join(fresh)}",
                  file=sys.stderr)
            sessions = _harvest(fresh)
    if not sessions and cfg.get("note") and cfg.get("engine") not in explained:
        print(f"  ⚠️  site {site_id} : {cfg['note']}", file=sys.stderr)
    return sessions


def _fold(s):
    """Minuscules sans accents, pour comparer un libellé de marque à un lieu."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def filter_by_label(sessions, label):
    """Ne garde que le studio de la marque quand un widget couvre plusieurs lieux.

    Un même widget healcode sert souvent tout un réseau : celui de Bikram Yoga
    Paris rend Marais ET Grands Boulevards, qui sont deux marques distinctes du
    catalogue. Sans filtre, chaque store récupérerait les séances de l'autre et
    l'agrégat compterait double. On ne garde que les séances dont le lieu est
    cité dans le libellé de la marque — et seulement si ça matche vraiment,
    sinon on rend tout (cas YUJ : « YUJ Paris » vs « YUJ Paris 7ème »).
    """
    lieux = {s.get("lieu") for s in sessions if s.get("lieu")}
    if len(lieux) < 2 or not label:
        return sessions
    folded = _fold(label)
    keep = [s for s in sessions
            if _fold(s.get("lieu")) and _fold(s["lieu"]) in folded]
    return keep or sessions


def fetch_sessions_for_brand(res, label="", days=56):
    """Adaptateur pour une entrée de *_extension_resolved.json.

    Tolère les deux conventions de nommage du fichier : `site_id` et
    `mb_site_id` y désignent tous deux, selon les marques, l'id healcode.
    """
    site_id = res.get("site_id") or res.get("mb_site_id")
    widget_id = (res.get("healcode_widget_ids") or res.get("schedules_widget_id")
                 or res.get("widget_id"))
    sessions = fetch_sessions(
        site_id,
        widget_id=widget_id or None,
        lieu=label,
        url=res.get("booking_url") or res.get("url"),
        days=days,
    )
    return filter_by_label(sessions, label)


# -------------------------------------------------------------------- CLI --
USAGE = ("usage: engine_mindbody_healcode.py "
         "{discover <url>|widget <widget_id> [lieu]|fetch <site_id> [lieu]}")


def _main(argv):
    if len(argv) < 2:
        print(USAGE, file=sys.stderr)
        return 2
    cmd = argv[1]
    if cmd == "discover" and len(argv) > 2:
        json.dump(discover_widgets(argv[2]), sys.stdout, ensure_ascii=False, indent=1)
    elif cmd == "widget" and len(argv) > 2:
        json.dump(fetch_widget(argv[2], lieu=argv[3] if len(argv) > 3 else ""),
                  sys.stdout, ensure_ascii=False)
    elif cmd == "fetch" and len(argv) > 2:
        json.dump(fetch_sessions(argv[2], lieu=argv[3] if len(argv) > 3 else None),
                  sys.stdout, ensure_ascii=False)
    else:
        print(USAGE, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
