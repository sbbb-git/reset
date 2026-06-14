#!/usr/bin/env python3
"""Scrape unifié des 44 studios Pilates en extension.

Lit pilates_extension_resolved.json (produit par pilates_extension_discover.py)
et applique le bon engine de scrape par plateforme :

- bsport          → bsport_scrape.run() avec company_id
- mindbody (HTTP) → engine banote-style (pour widgets healcode classiques)
- mindbody (SPA)  → engine reformation_evocore (Playwright)
- arketa          → engine snakeandtwist-style
- clubready       → TODO (Club Pilates franchise, API privée)
- wordpress       → TODO case-by-case
- unknown         → skip avec log

Chaque brand produit son propre <key>_data.json + <key>.html via le moteur
correspondant. L'agrégat Pilates IDF (pilates_idf_compute.py) inclura
automatiquement ces brands au prochain run.
"""
import importlib
import json
import os
import sys
import traceback

RESOLVED = "pilates_extension_resolved.json"
BRANDS_CFG = "pilates_extension_brands.json"


def load_or_die(path):
    if not os.path.exists(path):
        print(f"❌ {path} manquant — lance pilates_extension_discover.py d'abord", file=sys.stderr)
        sys.exit(1)
    return json.load(open(path, encoding="utf-8"))


def scrape_bsport(key, label, company_id):
    """Réutilise bsport_scrape.run avec un cfg minimal."""
    import bsport_scrape
    cfg = {
        "key": key,
        "brand": label.upper(),
        "companies": [int(company_id)],
        "host": "extension",
        "store": f"{key}_data.json",
        "html": f"{key}.html",
        "csv": f"{key}_seances.csv",
        "price": 30,
        "accent": "#b07ff0",
        "accent2": "#d4b8ff",
        "methode": f"<b>{label}</b> — extension auto-discovered (bsport company {company_id})",
    }
    bsport_scrape.run(cfg)


def scrape_mindbody_http(key, label, widget_id):
    """Engine HTTP simple pour widgets healcode classiques.
    Calque sur banote_fetch / le33foch_fetch."""
    # Re-utilise le33foch_fetch comme template — c'est le plus générique
    # avec fallback Playwright.
    import datetime as dt
    from zoneinfo import ZoneInfo
    PARIS = ZoneInfo("Europe/Paris")

    try:
        import le33foch_fetch
        # Override le widget_id
        original_widget = le33foch_fetch.WIDGET_ID
        original_lieu = le33foch_fetch.LIEU_FALLBACK
        le33foch_fetch.WIDGET_ID = widget_id
        le33foch_fetch.LIEU_FALLBACK = label
        sessions = le33foch_fetch.fetch_all()
        le33foch_fetch.WIDGET_ID = original_widget
        le33foch_fetch.LIEU_FALLBACK = original_lieu
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  fetch Mindbody {key} échoué : {e}", file=sys.stderr)
        sessions = []

    if not sessions:
        print(f"  → 0 séances pour {key} (peut nécessiter Playwright)", file=sys.stderr)
        return

    # Store + html basique (compatible heatmap)
    import safestore
    JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    now = dt.datetime.now(PARIS)
    store_path = f"{key}_data.json"
    store = safestore.load(store_path)
    for s in sessions:
        sid = str(s.get("id") or "")
        try:
            sdt = dt.datetime.fromisoformat(s["start"]).replace(tzinfo=PARIS)
        except (ValueError, KeyError):
            continue
        cart = (s.get("cart") or "").lower()
        statut = "complet" if any(k in cart for k in ("waitlist", "complet", "full")) else \
                 "disponible" if any(k in cart for k in ("book", "reserv", "réserv")) else "inconnu"
        cap = 12
        store[sid] = {
            "id": sid,
            "date": sdt.date().isoformat(),
            "jour": JOURS_FR[sdt.weekday()],
            "heure": sdt.strftime("%H:%M"),
            "fin": s.get("end", "")[:16][-5:] if s.get("end") else "",
            "lieu": (s.get("lieu") or label),
            "cours": (s.get("cours") or "").strip(),
            "coach": (s.get("coach") or "").strip(),
            "capacite": cap if statut in ("complet", "disponible") else 0,
            "presents": cap if statut == "complet" else 0,
            "finie": now >= sdt - dt.timedelta(minutes=15),
            "statut": statut,
            "releve": now.strftime("%Y-%m-%d %H:%M"),
        }
    safestore.save(store, store_path)
    print(f"  ← {len(sessions)} séances vues, {len(store)} en base ({key})")


def scrape_brand(key, brand_cfg, resolved):
    label = brand_cfg.get("label") or key
    res = resolved.get(key) or {}
    platform = res.get("platform")

    status = res.get("status", "")

    # Statuts terminaux : skip silencieux
    if status in ("skip", "defunct"):
        return
    if platform in ("defunct", "not_live"):
        return

    # Bsport résolu → scrape immédiat
    if platform == "bsport" and res.get("company_id") and res["company_id"] != "TODO":
        print(f"→ {key:30s} bsport company={res['company_id']}")
        scrape_bsport(key, label, res["company_id"])
        return

    # Mindbody BW widget (le33foch-style) → scrape HTTP
    if platform == "mindbody" and res.get("widget_id") and not res.get("needs_playwright"):
        print(f"→ {key:30s} mindbody BW widget={res['widget_id']}")
        scrape_mindbody_http(key, label, res["widget_id"])
        return

    # Mindbody healcode → fetcher dédié pas encore codé
    if platform in ("mindbody_healcode", "mindbody") and (res.get("mb_site_id") or res.get("site_id")):
        site = res.get("mb_site_id") or res.get("site_id")
        print(f"⏳ {key:30s} mindbody healcode site={site} — fetcher dédié à coder (skipped)")
        return

    # Sportigo, ClubReady, Arketa, etc. → TODO
    if platform in ("sportigo", "clubready", "arketa"):
        print(f"⏳ {key:30s} {platform} — engine à coder (skipped)")
        return

    # needs_playwright (Corpoz, Elevate, SPA) → reformation_evocore pattern
    if res.get("needs_playwright"):
        print(f"⏳ {key:30s} needs_playwright — déléguer à pattern reformation_evocore (TODO)")
        return

    # Retry au prochain discover
    if status == "retry":
        print(f"↻ {key:30s} {res.get('note', 'retry')}")
        return

    print(f"⊘ {key:30s} platform={platform or 'unknown'} status={status} — skip")


def main():
    brands = load_or_die(BRANDS_CFG)
    if not os.path.exists(RESOLVED):
        print(f"⚠️ {RESOLVED} introuvable — aucune brand résolue encore. Lance pilates_extension_discover.py.",
              file=sys.stderr)
        sys.exit(0)
    resolved = json.load(open(RESOLVED, encoding="utf-8"))

    ok = 0
    err = 0
    for key, b in brands.items():
        try:
            scrape_brand(key, b, resolved)
            ok += 1
        except Exception as e:  # noqa: BLE001
            err += 1
            print(f"❌ {key} : {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    print(f"\nDone : {ok} brands traitées, {err} erreurs")


if __name__ == "__main__":
    main()
