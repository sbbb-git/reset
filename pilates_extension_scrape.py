#!/usr/bin/env python3
"""Scrape unifié des 44 studios Pilates en extension.

Lit pilates_extension_resolved.json (produit par pilates_extension_discover.py)
et applique le bon engine de scrape par plateforme :

- bsport            → bsport_scrape.run() avec company_id
- mindbody (HTTP)   → engine banote-style (pour widgets BW classiques)
- mindbody_healcode → engine_mindbody_healcode.fetch_sessions()
- mindbody (SPA)    → engine reformation_evocore (Playwright)
- arketa          → engine snakeandtwist-style
- resamania       → engine_resamania (API Resamania II + proxys publics)
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


def store_mindbody_sessions(key, label, sessions):
    """Écrit les séances Mindbody brutes dans <key>_data.json (compatible heatmap).

    Format d'entrée : celui de banote_fetch.fetch_all() / du nouvel engine
    healcode — {id, start, end, cours, coach, lieu, cart}. Mindbody n'expose
    ni la capacité ni le nombre d'inscrits, on ne dérive qu'un STATUT du
    bouton panier (comme banote / Sense-Club).
    """
    import datetime as dt
    from zoneinfo import ZoneInfo
    import safestore
    PARIS = ZoneInfo("Europe/Paris")
    JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

    now = dt.datetime.now(PARIS)
    store_path = f"{key}_data.json"
    store = safestore.load(store_path)
    kept = 0
    for s in sessions:
        sid = str(s.get("id") or "")
        if not sid:
            continue
        try:
            sdt = dt.datetime.fromisoformat(s["start"]).replace(tzinfo=PARIS)
        except (ValueError, KeyError, TypeError):
            continue
        cart = (s.get("cart") or "").lower()
        statut = "complet" if any(k in cart for k in ("waitlist", "complet", "full", "attente")) else \
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
        kept += 1
    safestore.save(store, store_path)
    print(f"  ← {kept} séances vues, {len(store)} en base ({key})")


def scrape_mindbody_http(key, label, widget_id):
    """Engine HTTP pour widgets BW classiques (banote / le33foch-style).

    On passe par engine_mindbody_healcode : même endpoint load_markup, mais
    l'engine balaie plusieurs fenêtres (2 semaines par appel), espace ses
    requêtes pour ne pas déclencher le throttling Mindbody, et clé les séances
    sur le ClassID stable plutôt que sur l'id de DOM. Repli sur le33foch_fetch
    si l'engine ne rend rien, pour ne rien perdre en cas de régression.
    """
    sessions = []
    try:
        import engine_mindbody_healcode as healcode
        sessions = healcode.fetch_sessions(None, widget_id=widget_id, lieu=label)
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  engine healcode {key} échoué : {e}", file=sys.stderr)

    if not sessions:
        try:
            import le33foch_fetch
            original_widget = le33foch_fetch.WIDGET_ID
            original_lieu = le33foch_fetch.LIEU_FALLBACK
            le33foch_fetch.WIDGET_ID = widget_id
            le33foch_fetch.LIEU_FALLBACK = label
            try:
                sessions = le33foch_fetch.fetch_all()
            finally:
                le33foch_fetch.WIDGET_ID = original_widget
                le33foch_fetch.LIEU_FALLBACK = original_lieu
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️  fetch Mindbody {key} échoué : {e}", file=sys.stderr)

    if not sessions:
        print(f"  → 0 séances pour {key} (peut nécessiter Playwright)", file=sys.stderr)
        return
    store_mindbody_sessions(key, label, sessions)


def scrape_mindbody_healcode(key, label, res):
    """Engine healcode : <healcode-widget data-type="schedules" ...>.

    Le planning s'adresse par widget_id (pas par site_id) ; l'engine résout
    le widget via son registre, l'entrée resolved, ou une discovery sur le
    site de la marque. Renvoie [] sans exception si la marque n'est pas (ou
    plus) joignable en healcode — le motif part sur stderr.
    """
    import engine_mindbody_healcode as healcode
    sessions = healcode.fetch_sessions_for_brand(res, label)
    if not sessions:
        print(f"  → 0 séance pour {key} (voir stderr : widget absent, "
              f"Branded Web V2, ou plateforme changée)", file=sys.stderr)
        return False
    store_mindbody_sessions(key, label, sessions)
    return True


def scrape_resamania(key, label, res):
    """Engine Resamania II (Cercles de la Forme, OZEN HIT, ...).

    engine_resamania.fetch_sessions() tente l'API `class_events` puis, si son
    scope anonyme l'interdit (cas de cdf), retombe sur le proxy public du site
    vitrine. Il rend {id, start, end, cours, coach, lieu, capacite, presents} ;
    capacite/presents ne sont renseignés que par le chemin API authentifié —
    via un proxy ils valent None, et on écrit 0 (statut "inconnu") plutôt que
    d'inventer un remplissage.
    """
    import datetime as dt
    from zoneinfo import ZoneInfo
    import engine_resamania as resamania
    import safestore
    PARIS = ZoneInfo("Europe/Paris")
    JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

    slug = key if key in resamania.SOURCES else (res.get("slug") or key)
    try:
        sessions = resamania.fetch_sessions(slug, days=14)
    except resamania.ResamaniaAuthRequired as e:
        print(f"⏳ {key:30s} resamania — {e}", file=sys.stderr)
        return
    except resamania.ResamaniaError as e:
        print(f"  ⚠️  resamania {key} échoué : {e}", file=sys.stderr)
        return

    if not sessions:
        print(f"  → 0 séance pour {key} (resamania)", file=sys.stderr)
        return

    now = dt.datetime.now(PARIS)
    store_path = f"{key}_data.json"
    store = safestore.load(store_path)
    kept = 0
    for s in sessions:
        sid = str(s.get("id") or "")
        if not sid or not s.get("start"):
            continue
        try:
            sdt = dt.datetime.fromisoformat(s["start"])
        except (ValueError, TypeError):
            continue
        if sdt.tzinfo is None:
            sdt = sdt.replace(tzinfo=PARIS)
        cap = s.get("capacite")
        pres = s.get("presents")
        if cap is None or pres is None:
            statut = s.get("statut") or "inconnu"
        elif pres >= cap:
            statut = "complet"
        else:
            statut = "disponible"
        fin = ""
        if s.get("end"):
            try:
                fin = dt.datetime.fromisoformat(s["end"]).strftime("%H:%M")
            except (ValueError, TypeError):
                fin = ""
        store[sid] = {
            "id": sid,
            "date": sdt.date().isoformat(),
            "jour": JOURS_FR[sdt.weekday()],
            "heure": sdt.strftime("%H:%M"),
            "fin": fin,
            "lieu": (s.get("lieu") or s.get("club") or label),
            "cours": (s.get("cours") or "").strip(),
            "coach": (s.get("coach") or "").strip(),
            "capacite": cap or 0,
            "presents": pres or 0,
            "finie": now >= sdt - dt.timedelta(minutes=15),
            "statut": statut,
            "releve": now.strftime("%Y-%m-%d %H:%M"),
        }
        kept += 1
    safestore.save(store, store_path)
    src = sessions[0].get("source", "?")
    print(f"  ← {kept} séances vues ({src}), {len(store)} en base ({key})")


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

    # Mindbody healcode → engine dédié (widget <healcode-widget data-type="schedules">)
    # On y envoie aussi les mindbody « needs_playwright » : la discovery du
    # discover ne cherchait que le widget BW, pas la balise <healcode-widget>,
    # et une partie de ces marques a en réalité un planning healcode lisible en
    # HTTP (ex. Bikram Yoga Paris). Si l'engine ne trouve rien, on retombe sur
    # le message needs_playwright plus bas — aucune marque n'est perdue.
    if platform in ("mindbody_healcode", "mindbody"):
        site = res.get("site_id") or res.get("mb_site_id")
        print(f"→ {key:30s} mindbody healcode site={site or '?'}")
        if scrape_mindbody_healcode(key, label, res):
            return

    # Resamania (Resamania II / Stadline) → engine dédié
    if platform == "resamania":
        print(f"→ {key:30s} resamania slug={res.get('slug') or key}")
        scrape_resamania(key, label, res)
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


def run_scrape(brands_cfg, resolved_path, label="EXT"):
    """Scrape toutes les marques résolues d'un catalogue. Retourne (ok, erreurs)."""
    brands = load_or_die(brands_cfg)
    if not os.path.exists(resolved_path):
        print(f"⚠️ [{label}] {resolved_path} introuvable — lance la discovery d'abord.",
              file=sys.stderr)
        return 0, 0
    resolved = json.load(open(resolved_path, encoding="utf-8"))

    ok = err = 0
    for key, b in brands.items():
        if key.startswith("_"):
            continue
        try:
            scrape_brand(key, b, resolved)
            ok += 1
        except Exception as e:  # noqa: BLE001
            err += 1
            print(f"❌ {key} : {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    print(f"[{label}] {ok} marques traitées, {err} erreurs")
    return ok, err


def main():
    run_scrape(BRANDS_CFG, RESOLVED, "PILATES")


if __name__ == "__main__":
    main()
