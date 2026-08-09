#!/usr/bin/env python3
"""Auto-discovery de la plateforme de booking pour les studios Pilates
en extension. Pour chaque studio listé dans pilates_extension_brands.json,
fetch son site, détecte la plateforme (bsport / Mindbody / Arketa / ...)
et extrait l'identifiant technique (company ID / widget ID).

Résultats persistés dans pilates_extension_resolved.json — réutilisé
ensuite par pilates_extension_scrape.py.

Idempotent : ne ré-investigue pas les brands déjà 'resolved'.
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BRANDS_CFG = "pilates_extension_brands.json"
RESOLVED = "pilates_extension_resolved.json"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0 Safari/537.36")


def http_get(url, retries=2):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "text/html,*/*"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", "ignore")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            time.sleep(2 ** attempt)
        except Exception:  # noqa: BLE001
            return None
    return None


def detect_bsport(html):
    """Cherche un company_id bsport dans le HTML.
    Patterns : company=NNNN, companyId":NNNN, data-company-id="NNNN".
    """
    for pat in [
        r"company[_-]?[iI]d['\"]?\s*[:=]\s*['\"]?(\d{3,6})",
        r"company=(\d{3,6})",
        r"bsport\.io/[^'\"]+company=(\d{3,6})",
        r"data-company-id=['\"]?(\d{3,6})",
        r"production\.bsport\.io[^'\"]*company['\":\s]+(\d{3,6})",
    ]:
        m = re.search(pat, html)
        if m:
            return {"platform": "bsport", "company_id": m.group(1)}
    if "bsport" in html.lower():
        return {"platform": "bsport", "company_id": None, "note": "bsport detected mais ID introuvable"}
    return None


def detect_mindbody(html):
    """Cherche un widget_id Mindbody. Patterns:
    healcode.com/.../widget_keys/HASH ou widgets.mindbodyonline.com/widgets/schedules/NNNNNNN
    """
    for pat in [
        r"mindbodyonline\.com/widgets/schedules/(\d{10,15})",
        r"healcode\.com/[^'\"]*widget_keys?/([a-f0-9]{16,32})",
        r"data-bw-widget-id=['\"]?(\d{10,15})",
        r"widgetId['\":\s]+['\"]?(\d{10,15})",
    ]:
        m = re.search(pat, html)
        if m:
            return {"platform": "mindbody", "widget_id": m.group(1)}
    if "mindbodyonline.com" in html or "healcode.com" in html:
        return {"platform": "mindbody", "widget_id": None,
                "note": "Mindbody detected mais widget_id introuvable — probablement SPA, Playwright requis",
                "needs_playwright": True}
    return None


def detect_arketa(html):
    # Arketa sert son widget depuis arketa.co ET arketa.com : ne chercher que
    # le .com laissait passer des studios (ex. Sculpt Reformer Club), qui
    # retombaient alors sur un détecteur plus laxiste.
    m = re.search(r"arketa\.co(?:m)?/[^'\"]*/(?:company|business)/([a-z0-9-]+)",
                  html, re.I)
    if m:
        return {"platform": "arketa", "slug": m.group(1)}
    if re.search(r"\barketa\.co(?:m)?\b", html, re.I):
        return {"platform": "arketa", "slug": None}
    return None


def detect_glofox(html):
    """Portail Glofox (ABC Fitness) intégré en iframe.

    C'est la plateforme réelle de Club Pilates France — le tag `clubready`
    historique était faux (cf. engine_clubready.py). Le branchId suffit à
    scraper : engine_clubready.fetch_sessions() ne demande rien d'autre.
    """
    m = re.search(r"app\.glofox\.com/portal/#/branch/([a-f0-9]{24})", html, re.I)
    if m:
        return {"platform": "glofox", "branch_id": m.group(1)}
    if "glofox.com" in html.lower():
        return {"platform": "glofox", "branch_id": None,
                "note": "Glofox détecté mais branchId introuvable — chercher "
                        "l'iframe /portal/#/branch/<id> sur la page de réservation"}
    return None


# Sous-domaines de service de Sportigo : ce ne sont pas des tenants clients.
_SPORTIGO_NON_TENANTS = {"www", "app", "api", "demo", "static", "cdn", "blog",
                         "admin", "backend", "assets", "media"}


def detect_sportigo(html):
    """Tenant Sportigo : sous-domaine <slug>.sportigo.club (ou .fr)."""
    for m in re.finditer(r"\b([a-z0-9][a-z0-9-]*)\.sportigo\.(?:club|fr)\b",
                         html, re.I):
        slug = m.group(1).lower()
        if slug in _SPORTIGO_NON_TENANTS:
            continue
        return {"platform": "sportigo", "slug": slug,
                "booking_url": f"https://{slug}.sportigo.club/"}
    return None


def detect_clubready(html):
    """Vrai ClubReady (Xponential US). API fermée : on tague, on ne scrape pas.

    Attention : ce détecteur matchait autrefois la simple chaîne
    "club-pilates", ce qui taguait `clubready` n'importe quelle page citant la
    marque — dont un studio Arketa et les Club Pilates FR, qui sont sur Glofox.
    On n'accepte donc plus qu'une signature clubready.com explicite.
    """
    if "clubready.com" in html.lower():
        m = re.search(r"clubready\.com/api[^'\"]*club[^'\"]*=(\d+)", html)
        return {"platform": "clubready", "club_id": m.group(1) if m else None,
                "note": "ClubReady US — API réservée aux franchisés, "
                        "aucun accès public"}
    return None


def detect_resamania(html):
    if "resamania" in html.lower():
        m = re.search(r"resamania\.com/([a-z0-9_-]+)", html.lower())
        return {"platform": "resamania", "slug": m.group(1) if m else None}
    return None


def detect_doinsport(html):
    if "doinsport" in html.lower():
        m = re.search(r"doinsport\.club/[^'\"]*club[/_-]?id[='\"]?([a-f0-9-]{20,})", html)
        return {"platform": "doinsport", "club_id": m.group(1) if m else None}
    return None


def detect_eversports(html):
    """Eversports : widget de planning embarqué, ou lien vers le widget hébergé.

    Trois formes rencontrées en production :
      · <div data-eversports-widget-id="<uuid>">      (widget inline)
      · eversports.fr/org/widget/overview?companyId=<uuid>
      · eversports.fr/widget/w/<code>                 (widget court)

    Volontairement SANS repli sur le simple mot-clé : `investigate()` retourne
    au premier détecteur qui rend un dict, et plusieurs studios citent
    eversports sur leur home (classe CSS, logo, lien vers l'app mobile) alors
    que l'identifiant ne vit que sur /planning. Un repli sans ID gèlerait donc
    la détection sur la home et ferait perdre le widget — cas constaté sur
    Reformer Raum. Pas d'ID → on laisse la page suivante répondre.
    """
    for pat, field in (
        (r"data-eversports-widget-id=['\"]([0-9a-f]{8}-[0-9a-f-]{20,30})", "widget_id"),
        (r"eversports\.[a-z]{2,3}/org/widget/[^'\"]*companyId=([0-9a-f]{8}-[0-9a-f-]{20,30})",
         "company_id"),
        (r"eversports\.[a-z]{2,3}/widget/w/([A-Za-z0-9]{4,16})", "slug"),
    ):
        m = re.search(pat, html, re.I)
        if m:
            return {"platform": "eversports", field: m.group(1)}
    return None


# Sous-domaines de service Deciplus : pas des tenants clients.
_DECIPLUS_NON_TENANTS = {"www", "api", "app", "admin", "static", "cdn", "member",
                         "member-app", "back", "backend", "demo", "assets"}


def detect_deciplus(html):
    """Deciplus : portail de réservation des salles FR, en iframe.

    Deux formes : le portail mutualisé member-app.deciplus.pro/<tenant>/... et
    le sous-domaine dédié <tenant>.deciplus.pro. On teste le portail d'abord :
    son hôte matcherait aussi la forme sous-domaine et rendrait « member-app »,
    qui n'est pas un tenant.
    """
    m = re.search(r"member-app\.deciplus\.pro/(?:#/)?([a-z0-9][a-z0-9_-]*)", html, re.I)
    if m and m.group(1).lower() not in _DECIPLUS_NON_TENANTS:
        slug = m.group(1).lower()
        return {"platform": "deciplus", "slug": slug,
                "booking_url": f"https://member-app.deciplus.pro/{slug}/calendar"}
    for m in re.finditer(r"\b([a-z0-9][a-z0-9-]*)\.deciplus\.(?:pro|fr|com)\b", html, re.I):
        slug = m.group(1).lower()
        if slug in _DECIPLUS_NON_TENANTS:
            continue
        return {"platform": "deciplus", "slug": slug,
                "booking_url": f"https://{slug}.deciplus.pro/"}
    return None


_HELLORESA_NON_TENANTS = {"www", "app", "api", "static", "cdn", "admin"}


def detect_helloresa(html):
    """helloresa : tenant en sous-domaine <slug>.helloresa.com."""
    for m in re.finditer(r"\b([a-z0-9][a-z0-9-]*)\.helloresa\.com\b", html, re.I):
        slug = m.group(1).lower()
        if slug in _HELLORESA_NON_TENANTS:
            continue
        return {"platform": "helloresa", "slug": slug,
                "booking_url": f"https://{slug}.helloresa.com/"}
    return None


_SIMPLYBOOK_NON_TENANTS = {"www", "secure", "app", "api", "admin", "static", "cdn"}


def detect_simplybook(html):
    """SimplyBook.me : tenant en sous-domaine <slug>.simplybook.it/.me."""
    for m in re.finditer(r"\b([a-z0-9][a-z0-9-]*)\.simplybook\.(?:it|me)\b", html, re.I):
        slug = m.group(1).lower()
        if slug in _SIMPLYBOOK_NON_TENANTS:
            continue
        return {"platform": "simplybook", "slug": slug,
                "booking_url": f"https://{slug}.simplybook.it/v2/"}
    return None


def detect_acuity(html):
    """Acuity Scheduling (aussi vendu en Squarespace Scheduling).

    L'identifiant de compte est le paramètre `owner` du lien de planning.
    """
    m = re.search(r"(?:acuityscheduling|squarespacescheduling)\.com/schedule\.php"
                  r"[^'\"]*owner=(\d+)", html, re.I)
    if m:
        return {"platform": "acuity", "club_id": m.group(1),
                "booking_url": f"https://app.acuityscheduling.com/schedule.php?owner={m.group(1)}"}
    return None


def detect_fresha(html):
    """Fresha : place de marché beauté/bien-être, pages /a/ (annonce) et /lvp/.

    Le slug porte un suffixe court propre au commerce, il identifie la fiche.
    """
    m = re.search(r"fresha\.com/(?:[a-z]{2}/)?(?:a|lvp|p)/([a-z0-9][a-z0-9-]{4,})",
                  html, re.I)
    if m:
        return {"platform": "fresha", "slug": m.group(1).lower()}
    return None


def detect_calendly(html):
    """Calendly — DÉLIBÉRÉMENT LE DERNIER DÉTECTEUR.

    Calendly est un outil de rendez-vous 1:1, pas un planning de cours : des
    studios qui réservent réellement ailleurs y renvoient pour un « appel
    découverte » ou un essai. Le taguer trop tôt volerait la marque à sa vraie
    plateforme — Apogée Paris, par exemple, porte sur la même page un lien
    Calendly, un lien Fresha et son vrai tenant Sportigo.

    On exige un lien de prise de rendez-vous complet (calendly.com/<compte>) :
    un simple <link rel="dns-prefetch" href="https://calendly.com"> ne prouve
    rien et ne doit pas résoudre la marque (cas de Studio SVB).
    """
    m = re.search(r"calendly\.com/([a-z0-9][a-z0-9_-]{2,})(?:/([a-z0-9][a-z0-9_-]*))?",
                  html, re.I)
    if not m or m.group(1).lower() in ("assets", "static", "app", "api", "embed"):
        return None
    return {"platform": "calendly", "slug": m.group(1).lower(),
            "note": "Calendly = rendez-vous individuel, pas de planning de "
                    "cours exploitable — aucun engine, marque taguée pour "
                    "arrêter de la resonder"}


# Ordre = priorité. Les signatures spécifiques (sous-domaine de tenant, iframe
# de portail) passent avant les détecteurs à base de simple mot-clé, qui sont
# les plus prompts aux faux positifs.
#
# Le bloc historique reste en tête, inchangé : toute marque déjà résolue doit
# retomber sur le même verdict qu'avant (cf. la non-régression rejouée sur les
# 47 marques résolues). Les nouveaux détecteurs sont ajoutés derrière, du plus
# spécifique au plus générique :
#   eversports/deciplus/helloresa/simplybook/acuity  → domaine éditeur dédié,
#       qui n'apparaît que si c'est bien le moteur de réservation ;
#   fresha    → place de marché, peut être un simple lien partenaire ;
#   calendly  → outil de RDV 1:1, le plus ambigu, donc bon dernier.
#
# Deux détecteurs candidats ont été écartés après mesure, pas par prudence de
# principe :
#   · healcode (<healcode-widget data-site-id>) : la signature est présente sur
#     4 marques déjà résolues en `mindbody` (Mucho Pilates, Miraj, Pilates
#     Social Club, L'Atelier Reformer). Le placer avant detect_mindbody les
#     ferait toutes changer de plateforme ; le placer après le rend inerte, car
#     le repli mot-clé de detect_mindbody capte déjà ces pages. Mindbody n'est
#     donc pas une plateforme « manquante » : elle est déjà couverte.
#   · Wix Bookings (bookings.wixapps.net) : la signature apparaît sur
#     wunder_barre (bsport) et studio_pilates_bm_kremlin (mindbody) — elle
#     décrit l'app installée sur le site Wix, pas le moteur qui sert le
#     planning. Aucun identifiant exploitable en prime : faux positif net.
DETECTORS = [
    detect_bsport, detect_mindbody, detect_glofox, detect_sportigo,
    detect_arketa, detect_clubready, detect_resamania, detect_doinsport,
    detect_eversports, detect_deciplus, detect_helloresa, detect_simplybook,
    detect_acuity, detect_fresha, detect_calendly,
]


# Pages sondées. Les 6 premières (home + pages de réservation usuelles) sont
# inchangées et le restent : c'est sur elles que reposent toutes les marques
# déjà résolues. /tarifs et /horaires sont ajoutées APRÈS ce bloc, jamais
# dedans — un verdict rendu sur les 6 premières pages ne peut donc pas changer.
# Chacune est là parce qu'elle débloque des marques mesurées, pas par principe :
#   /tarifs   → Now Reformer (×2 studios) n'expose sa balise healcode que là ;
#               rattrape aussi Perspectives Studio, dont la signature bsport a
#               quitté la home.
#   /horaires → L'Atelier R (Saint-Maur) n'y expose son planning Acuity que là.
# /reserver a été testée puis écartée : elle ne débloque aucune marque et ne
# ferait qu'ajouter une requête par marque non résolue et par run.
PROBE_PATHS = ("/reservation", "/booking", "/planning", "/classes", "/cours")
PROBE_LIMIT = 6
EXTRA_PROBE_PATHS = ("/tarifs", "/horaires")


def investigate(url):
    """Fetch + tente de détecter la plateforme. Vérifie aussi /reservation,
    /booking, /planning, /classes — les pages de booking ont souvent l'iframe."""
    candidates = [url]
    for path in PROBE_PATHS:
        if not url.endswith("/"):
            candidates.append(url.rstrip("/") + path)
        candidates.append(url.rstrip("/") + path + "/")
    candidates = candidates[:PROBE_LIMIT] + [
        url.rstrip("/") + p for p in EXTRA_PROBE_PATHS]

    seen_html = ""
    for cand in candidates:
        h = http_get(cand)
        if not h:
            continue
        seen_html = h
        for d in DETECTORS:
            r = d(h)
            if r and r.get("platform"):
                r["detected_via"] = cand
                return r
        time.sleep(0.5)

    return {"platform": "unknown",
            "note": f"Aucune signature détectée sur {url} (homepage + 5 paths)"}


# Budget d'un run. La discovery interrogeait tout le catalogue à chaque
# passage : à 138 marques (Pilates) elle dépassait le timeout du workflow et
# emportait le scrape avec elle. On borne donc le temps, et on abandonne les
# marques dont la plateforme résiste à MAX_ATTEMPTS sondages successifs.
MAX_SECONDS = 18 * 60
MAX_ATTEMPTS = 3


def _todo(brands, resolved):
    """Marques à sonder, jamais-testées d'abord puis unknown les plus anciens.

    Une marque résolue, abandonnée ou explicitement 'skip' n'est plus resondée.
    """
    never, retry = [], []
    for key, b in brands.items():
        if key.startswith("_") or not b.get("url"):
            continue
        res = resolved.get(key)
        if res is None:
            never.append(key)
            continue
        if res.get("platform") not in (None, "unknown"):
            continue                      # déjà résolu
        if res.get("status") in ("skip", "gave_up"):
            continue                      # abandonné, inutile d'insister
        retry.append(key)
    retry.sort(key=lambda k: resolved[k].get("checked_at") or "")
    return never + retry


def run_discovery(brands_cfg, resolved_path, label="EXT"):
    """Sonde un catalogue par tranches. Retourne (résolus, unknown, abandons)."""
    if not os.path.exists(brands_cfg):
        print(f"❌ {brands_cfg} introuvable", file=sys.stderr)
        sys.exit(1)
    brands = json.load(open(brands_cfg, encoding="utf-8"))
    resolved = {}
    if os.path.exists(resolved_path):
        try:
            resolved = json.load(open(resolved_path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            resolved = {}

    todo = _todo(brands, resolved)
    total = len([k for k in brands if not k.startswith("_")])
    already = sum(1 for k, v in resolved.items()
                  if not k.startswith("_") and isinstance(v, dict)
                  and v.get("platform") not in (None, "unknown"))
    print(f"[{label}] catalogue={total}  déjà résolus={already}  à sonder={len(todo)}")

    started = time.time()
    n_new = n_unknown = n_gaveup = 0
    done = 0
    for key in todo:
        if time.time() - started > MAX_SECONDS:
            print(f"[{label}] budget {MAX_SECONDS//60} min atteint — "
                  f"{len(todo) - done} marques reportées au prochain run")
            break
        url = brands[key]["url"]
        prev = resolved.get(key) or {}
        attempts = prev.get("attempts", 0) + 1
        print(f"→ {key:34s} (essai {attempts}) {url}")
        res = investigate(url)
        res["checked_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        res["url"] = url
        res["attempts"] = attempts
        if res.get("platform") in (None, "unknown"):
            n_unknown += 1
            if attempts >= MAX_ATTEMPTS:
                res["status"] = "gave_up"
                n_gaveup += 1
        else:
            n_new += 1
        resolved[key] = res
        ident = (res.get("company_id") or res.get("widget_id")
                 or res.get("club_id") or res.get("slug") or "no-id")
        flag = " ⛔ abandon" if res.get("status") == "gave_up" else ""
        print(f"  ← {res.get('platform')} | {ident}{flag}")
        done += 1

    with open(resolved_path, "w", encoding="utf-8") as f:
        json.dump(resolved, f, ensure_ascii=False, indent=1)

    print(f"[{label}] sondées={done}  résolues={n_new}  unknown={n_unknown}"
          f"  abandons={n_gaveup}  -> {resolved_path}")
    return n_new, n_unknown, n_gaveup


def main():
    run_discovery(BRANDS_CFG, RESOLVED, "PILATES")


if __name__ == "__main__":
    main()
