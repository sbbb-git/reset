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
    m = re.search(r"arketa\.com/[^'\"]*/(?:company|business)/([a-z0-9-]+)", html, re.I)
    if m:
        return {"platform": "arketa", "slug": m.group(1)}
    if "arketa.com" in html:
        return {"platform": "arketa", "slug": None}
    return None


def detect_clubready(html):
    if "clubready.com" in html.lower() or "club-pilates" in html.lower():
        m = re.search(r"clubready\.com/api[^'\"]*club[^'\"]*=(\d+)", html)
        return {"platform": "clubready", "club_id": m.group(1) if m else None}
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


DETECTORS = [
    detect_bsport, detect_mindbody, detect_arketa,
    detect_clubready, detect_resamania, detect_doinsport,
]


def investigate(url):
    """Fetch + tente de détecter la plateforme. Vérifie aussi /reservation,
    /booking, /planning, /classes — les pages de booking ont souvent l'iframe."""
    candidates = [url]
    for path in ("/reservation", "/booking", "/planning", "/classes", "/cours"):
        if not url.endswith("/"):
            candidates.append(url.rstrip("/") + path)
        candidates.append(url.rstrip("/") + path + "/")

    seen_html = ""
    for cand in candidates[:6]:
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


def main():
    if not os.path.exists(BRANDS_CFG):
        print(f"❌ {BRANDS_CFG} introuvable", file=sys.stderr)
        sys.exit(1)
    brands = json.load(open(BRANDS_CFG, encoding="utf-8"))
    resolved = {}
    if os.path.exists(RESOLVED):
        try:
            resolved = json.load(open(RESOLVED, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            resolved = {}

    n_new = n_skip = n_unknown = 0
    for key, b in brands.items():
        if key in resolved and resolved[key].get("platform") not in (None, "unknown"):
            n_skip += 1
            continue
        url = b.get("url")
        if not url:
            continue
        print(f"→ {key:30s} ({url})")
        res = investigate(url)
        res["checked_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        res["url"] = url
        resolved[key] = res
        print(f"  ← {res.get('platform')} | {res.get('company_id') or res.get('widget_id') or res.get('slug') or 'no-id'}")
        if res.get("platform") in (None, "unknown"):
            n_unknown += 1
        else:
            n_new += 1

    with open(RESOLVED, "w", encoding="utf-8") as f:
        json.dump(resolved, f, ensure_ascii=False, indent=1)

    print(f"\nDiscovery : {n_new} nouveaux + {n_skip} déjà résolus + {n_unknown} unknown / {len(brands)} brands")
    print(f"-> {RESOLVED}")


if __name__ == "__main__":
    main()
