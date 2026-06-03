#!/usr/bin/env python3
"""Récupère le planning Mindbody (healcode) du widget Le 33 Foch.

Le widget healcode du site charge ses séances via l'endpoint JSONP public
  https://widgets.mindbodyonline.com/widgets/schedules/<widget_id>/load_markup
qui renvoie {"class_sessions": "<html>", "calendar":..., "filters":...}.
On y trouve : id de séance, datetime début/fin, nom du cours, coach, lieu
("CERCLE DU 33 AVENUE FOCH" — un seul lieu), et le bouton panier
(« RÉSERVER »/« Book » = places dispo, « Liste d'attente »/« Complet » = plein).
Mindbody n'expose PAS le nombre exact d'inscrits ni la capacité chiffrée -> on
ne récupère qu'un STATUT (comme Banote / Sense-Club). Aucun JS requis.

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

_LAX_SSL = ssl.create_default_context()
_LAX_SSL.check_hostname = False
_LAX_SSL.verify_mode = ssl.CERT_NONE

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0"
BASE = "https://widgets.mindbodyonline.com/widgets/schedules"

# Le 33 Foch : un seul widget (extrait de https://le33foch.fr/accueil/planning-et-reservations).
WIDGET_ID = "071882500180"
LIEU_FALLBACK = "Le 33 Foch"


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
                "Referer": "https://le33foch.fr/"})
            ctx = _LAX_SSL if attempt else None
            with urllib.request.urlopen(req, timeout=45, context=ctx) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            # Backoff exponentiel 3s,6s,12s,24s,48s. Mindbody throttle parfois
            # sur l'IP runner GitHub quand on enchaîne plusieurs scrapers Mindbody.
            time.sleep(3 * (2 ** attempt))
    raise last


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
    today = dt.date.today().isoformat()
    sessions = []
    try:
        hs = load_markup(WIDGET_ID, today).get("class_sessions") or ""
    except Exception as e:  # noqa: BLE001
        print(f"  (widget {WIDGET_ID} indispo : {e})", file=sys.stderr)
        return sessions
    seen = set()
    for s in parse_sessions(hs, LIEU_FALLBACK):
        if s["id"] in seen:
            continue
        seen.add(s["id"])
        s["widget"] = WIDGET_ID
        sessions.append(s)
    return sessions


if __name__ == "__main__":
    json.dump(fetch_all(), sys.stdout, ensure_ascii=False)
