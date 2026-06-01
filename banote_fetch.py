#!/usr/bin/env python3
"""Récupère le planning Mindbody (healcode/brandedweb) des 3 widgets Banote.

Les widgets Mindbody chargent leurs séances via l'endpoint JSONP public
  https://widgets.mindbodyonline.com/widgets/schedules/<widget_id>/load_markup
qui renvoie {"class_sessions": "<html>", "calendar":..., "filters":...}.
Le HTML expose : id de séance, datetime début/fin, nom du cours, coach, nom du
lieu, et le bouton panier (« RESERVER »/« Book » = places dispo,
« Liste d'attente »/« Complet » = plein). Mindbody n'expose PAS le nombre exact
d'inscrits ni la capacité chiffrée -> on ne récupère qu'un STATUT (comme
Sense-Club). Aucun JS/Playwright requis : appel HTTP direct.

Sortie : JSON sur stdout -> liste de séances brutes.
"""
import datetime as dt
import html as _html
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

# Contexte SSL tolérant : la sandbox a parfois une horloge décalée -> certificat
# "not yet valid". On retombe dessus uniquement si la vérification standard échoue.
_LAX_SSL = ssl.create_default_context()
_LAX_SSL.check_hostname = False
_LAX_SSL.verify_mode = ssl.CERT_NONE

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120"
BASE = "https://widgets.mindbodyonline.com/widgets/schedules"

# 3 lieux Banote. (widget_id, location_filter, libellé court de secours)
WIDGETS = [
    ("2d207237ef5e", "1", "Banote Club Paris 16e"),
    ("2d219395ef5e", "2", "Banote Club Charenton"),
    ("2d52240ef5e", None, "Banote x Collectionneur"),
]


def load_markup(widget_id, start_date, location=None, retries=5):
    params = {"options[start_date]": start_date, "preview": "false"}
    if location is not None:
        params["options[location]"] = location
    url = f"{BASE}/{widget_id}/load_markup?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "application/json",
                "Referer": "https://www.banoteclub.com/"})
            ctx = _LAX_SSL if attempt else None  # 1er essai = vérif normale
            with urllib.request.urlopen(req, timeout=45, context=ctx) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001  (500 intermittents / SSL -> retry)
            last = e
            # Backoff exponentiel 3s,6s,12s,24s,48s. Mindbody throttle parfois.
            time.sleep(3 * (2 ** attempt))
    raise last


def load_week(widget_id, monday, location=None):
    """Renvoie le HTML d'une semaine ; essaie plusieurs jours de la semaine
    (le widget renvoie toujours la semaine entière) si l'ancre échoue."""
    base = dt.date.fromisoformat(monday)
    errs = []
    for off in (0, 3):
        d = (base + dt.timedelta(days=off)).isoformat()
        try:
            return load_markup(widget_id, d, location).get("class_sessions") or ""
        except Exception as e:  # noqa: BLE001
            errs.append(f"{d}:{e}")
    print(f"  (semaine {monday} indispo {widget_id}: {errs[-1]})", file=sys.stderr)
    return ""


def _clean(m):
    if not m:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()


def parse_sessions(html_str, fallback_lieu):
    html_str = _html.unescape(html_str or "")
    out = []
    for block in re.split(r'(?=<div class="bw-session" )', html_str):
        if 'class="bw-session"' not in block:
            continue
        sid = re.search(r'data-bw-widget-id="(\d+)"', block)
        sdt = re.search(r'<time class="hc_starttime" datetime="([^"]+)"', block)
        edt = re.search(r'<time class="hc_endtime" datetime="([^"]+)"', block)
        if not sid or not sdt:
            continue
        nm = re.search(r'<div class="bw-session__name">(.*?)</div>', block, re.S)
        staff = re.search(r'<div class="bw-session__staff"[^>]*>(.*?)</div>', block, re.S)
        locn = re.search(r'<div class="bw-session__location"[^>]*>(.*?)</div>', block, re.S)
        cart = _clean(re.search(r'<span class="bw-widget__cart_button">(.*?)</span>', block, re.S))
        # NB : le div .bw-session__canceled "Annulé" est un gabarit présent sur
        # CHAQUE séance (affiché par JS uniquement si réellement annulée) ->
        # ce n'est PAS un indicateur d'annulation. Mindbody n'expose pas l'état
        # annulé dans ce markup statique, on ne le déduit donc pas.
        canceled = False
        cours = _clean(nm)
        cours = re.sub(r"^[A-Za-z]+\s*[-–]\s*", "", cours).strip() or cours
        out.append({
            "id": sid.group(1),
            "start": sdt.group(1),
            "end": edt.group(1) if edt else "",
            "cours": cours,
            "coach": _clean(staff),
            "lieu": _clean(locn) or fallback_lieu,
            "cart": cart,
            "canceled": canceled,
        })
    return out


def fetch_all():
    # Un seul appel par widget : load_markup renvoie déjà le planning courant
    # (semaine en cours + suivantes). 3 requêtes -> rapide.
    today = dt.date.today().isoformat()
    seen, sessions = set(), []
    for widget_id, location, fallback in WIDGETS:
        try:
            hs = load_markup(widget_id, today, location).get("class_sessions") or ""
        except Exception as e:  # noqa: BLE001
            print(f"  (widget {widget_id} indispo : {e})", file=sys.stderr)
            continue
        for s in parse_sessions(hs, fallback):
            if s["id"] in seen:
                continue
            seen.add(s["id"])
            s["widget"] = widget_id
            sessions.append(s)
    return sessions


if __name__ == "__main__":
    json.dump(fetch_all(), sys.stdout, ensure_ascii=False)
