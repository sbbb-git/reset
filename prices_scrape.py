#!/usr/bin/env python3
"""Scraper factuel des grilles tarifaires (drop-in, packs, abos) par marque.

Source de vérité pour le prix moyen utilisé dans le comparateur et le CA estimé.
Stratégie :
  - Monday Group (Punch/Dynamo/Riise) : API WP Mindbody `/wp-json/mindbody/v1/sale/services`
    + `/contracts` → grille exhaustive et factuelle.
  - Autres marques : valeurs vérifiées par recherche manuelle (sources URLs explicites),
    à compléter quand on automatisera leur scrape (bsport offer endpoint, Arketa, etc.).

Sortie :
  - brand_prices.json  (fichier local committable, structure JSON unique)
  - upsert dans Supabase `brand_prices` si SUPABASE_URL + SUPABASE_SERVICE_KEY définis.

Schéma par marque :
  {
    "source_url": "https://...",
    "confiance": "haute" | "moyenne" | "basse",
    "drop_in": 29,
    "packs": [{"name":"Pack 10","size":10,"prix_total":249,"prix_unitaire":24.9}, ...],
    "abos":  [{"name":"...","prix_mensuel":189,"seances_inclus":8,"engagement_mois":12}, ...],
    "offres":[{"name":"Offre d'essai","prix":15,"seances":1}, ...],
    "note":   "..."
  }
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120"


def fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ----------- Monday Group (Punch / Dynamo / Riise) -----------
# 3 sites WordPress avec le même plugin Mindbody. La grille des packs est exposée
# via /wp-json/mindbody/v1/sale/services. Les abos via /sale/contracts (les prix
# ne sont pas tous dans le champ "Price" — on tente plusieurs champs).

PACK_RE = re.compile(r"^\s*(\d+)\s*[Ss]éances?\s*Monday\s*$")
PACK_BIENVENUE_RE = re.compile(r"[Bb]ienvenue\s*(\d+)")
ESSAI_KEYS = ("essai", "offre d'essai", "offre d essai")


def _is_pack_session(name):
    """Renvoie le nb de séances si le service est un pack 'N séances Monday'."""
    m = PACK_RE.match(name or "")
    return int(m.group(1)) if m else None


def _is_drop_in(name):
    n = (name or "").lower()
    return "1 séance" in n or "1 seance" in n


def _is_essai(name):
    n = (name or "").lower()
    return any(k in n for k in ESSAI_KEYS)


def _is_bienvenue(name):
    return bool(PACK_BIENVENUE_RE.search(name or ""))


def scrape_monday(host, key, label):
    """Récupère la grille Monday Group via l'API WP Mindbody."""
    base = f"https://{host}"
    try:
        services = fetch_json(f"{base}/wp-json/mindbody/v1/sale/services")
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ {key}: services indispo : {e}", file=sys.stderr)
        return None
    drop_in = None
    packs = []
    offres = []
    for s in services if isinstance(services, list) else []:
        nm = s.get("Name") or s.get("name") or ""
        price = s.get("Price") or s.get("OnlinePrice") or 0
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if not price:
            continue
        n_sess = _is_pack_session(nm)
        if _is_drop_in(nm):
            drop_in = price
        elif n_sess:
            packs.append({"name": nm.strip(), "size": n_sess,
                          "prix_total": price, "prix_unitaire": round(price / n_sess, 2)})
        elif _is_essai(nm):
            offres.append({"name": nm.strip(), "prix": price, "seances": 1})
        elif _is_bienvenue(nm):
            m = PACK_BIENVENUE_RE.search(nm)
            n = int(m.group(1)) if m else 3
            offres.append({"name": nm.strip(), "prix": price, "seances": n,
                           "prix_unitaire": round(price / n, 2)})
    packs.sort(key=lambda p: p["size"])

    # Contracts (abos) : l'API expose le nom mais pas toujours le prix.
    abos = []
    try:
        contracts = fetch_json(f"{base}/wp-json/mindbody/v1/sale/contracts")
        for c in contracts if isinstance(contracts, list) else []:
            nm = c.get("Name") or c.get("name") or ""
            price = c.get("FirstAutopayAmount") or c.get("RecurringPaymentAmountSubtotal") \
                or c.get("Price") or 0
            try:
                price = float(price or 0)
            except (TypeError, ValueError):
                price = 0
            m_seances = re.search(r"(\d+)\s*séances?", nm, re.IGNORECASE)
            m_engag = re.search(r"(\d+)\s*mois", nm, re.IGNORECASE)
            if m_seances and m_engag:
                abos.append({
                    "name": nm.strip(),
                    "prix_mensuel": price or None,
                    "seances_inclus": int(m_seances.group(1)),
                    "engagement_mois": int(m_engag.group(1)),
                })
    except Exception as e:  # noqa: BLE001
        print(f"  (contracts {key} indispo : {e})", file=sys.stderr)

    return {
        "source_url": f"{base}/reservation/#/tarifs",
        "confiance": "haute",
        "drop_in": drop_in,
        "packs": packs,
        "abos": abos,
        "offres": offres,
        "note": f"Grille Monday Group factuelle depuis /wp-json/mindbody/v1/sale/services",
    }


# ----------- Données vérifiées manuellement (agent + corrections) -----------
# À termes, à remplacer par des scrapers dédiés (bsport offer endpoint, Mariana Tek,
# Mindbody widget, Arketa, resamania, Anybuddy). Confiance reflète la fiabilité actuelle.

MANUAL = {
    "thenewme": {
        "source_url": "https://thenewmeparis.com/pages/studio-reformer-wagram",
        "confiance": "haute",
        "drop_in": 45,
        "packs": [
            {"name": "Pack 3", "size": 3, "prix_total": 100, "prix_unitaire": 33.33},
            {"name": "Pack 5", "size": 5, "prix_total": 200, "prix_unitaire": 40.0},
            {"name": "Pack 10", "size": 10, "prix_total": 380, "prix_unitaire": 38.0},
            {"name": "Pack 20", "size": 20, "prix_total": 700, "prix_unitaire": 35.0},
            {"name": "Pack 50", "size": 50, "prix_total": 1450, "prix_unitaire": 29.0},
        ],
        "abos": [],
        "offres": [],
        "note": "Pilates reformer (Wagram + 26 studios). Pas d'abo mensuel.",
    },
    # Sanctuary Group — grille partagée (Le Cercle / Space Cycle / Poses / DRIP HIIT)
    "lecercle": {
        "source_url": "https://www.lecercle-boxing.com/tarifs/",
        "confiance": "haute",
        "drop_in": 35,
        "packs": [
            {"name": "Pack 5", "size": 5, "prix_total": 149, "prix_unitaire": 29.8},
            {"name": "Pack 10", "size": 10, "prix_total": 279, "prix_unitaire": 27.9},
            {"name": "Pack 20", "size": 20, "prix_total": 499, "prix_unitaire": 24.95},
            {"name": "Pack 40", "size": 40, "prix_total": 799, "prix_unitaire": 19.98},
        ],
        "abos": [
            {"name": "Abo 2×/sem 6 mois", "prix_mensuel": 169, "seances_inclus": 8, "engagement_mois": 6},
        ],
        "offres": [{"name": "Essai", "prix": 19, "seances": 1}],
        "note": "Sanctuary Pass.",
    },
    "spacecycle": {
        "source_url": "https://www.space-cycle.com/tarifs/",
        "confiance": "haute",
        "drop_in": 35,
        "packs": [
            {"name": "Pack 5", "size": 5, "prix_total": 149, "prix_unitaire": 29.8},
            {"name": "Pack 10", "size": 10, "prix_total": 279, "prix_unitaire": 27.9},
            {"name": "Pack 20", "size": 20, "prix_total": 499, "prix_unitaire": 24.95},
            {"name": "Pack 40", "size": 40, "prix_total": 799, "prix_unitaire": 19.98},
        ],
        "abos": [
            {"name": "Abo 2×/sem 6 mois", "prix_mensuel": 169, "seances_inclus": 8, "engagement_mois": 6},
        ],
        "offres": [{"name": "Essai", "prix": 19, "seances": 1}],
        "note": "Sanctuary Pass (même grille que Le Cercle / Poses / DRIP).",
    },
    "poses": {
        "source_url": "https://www.poses-studio.com/tarifs/",
        "confiance": "haute",
        "drop_in": 35,
        "packs": [
            {"name": "Pack 5", "size": 5, "prix_total": 149, "prix_unitaire": 29.8},
            {"name": "Pack 10", "size": 10, "prix_total": 279, "prix_unitaire": 27.9},
            {"name": "Pack 20", "size": 20, "prix_total": 499, "prix_unitaire": 24.95},
            {"name": "Pack 40", "size": 40, "prix_total": 799, "prix_unitaire": 19.98},
        ],
        "abos": [
            {"name": "Abo Pilates 2×/sem 6 mois", "prix_mensuel": 217, "seances_inclus": 8, "engagement_mois": 6},
        ],
        "offres": [{"name": "Essai", "prix": 19, "seances": 1}],
        "note": "Sanctuary Pass — abo Pilates plus cher que les autres concepts.",
    },
    "driphiit": {
        "source_url": "https://www.drip-hiit.com/en/pricing/",
        "confiance": "haute",
        "drop_in": 35,
        "packs": [
            {"name": "Pack 5", "size": 5, "prix_total": 149, "prix_unitaire": 29.8},
            {"name": "Pack 10", "size": 10, "prix_total": 279, "prix_unitaire": 27.9},
            {"name": "Pack 20", "size": 20, "prix_total": 499, "prix_unitaire": 24.95},
            {"name": "Pack 40", "size": 40, "prix_total": 799, "prix_unitaire": 19.98},
        ],
        "abos": [
            {"name": "Abo 2×/sem 6 mois", "prix_mensuel": 169, "seances_inclus": 8, "engagement_mois": 6},
        ],
        "offres": [{"name": "Essai", "prix": 19, "seances": 1}],
        "note": "Sanctuary Pass.",
    },
    "episod": {
        "source_url": "https://www.episod.com/nos-tarifs/",
        "confiance": "haute",
        "drop_in": 30,
        "packs": [
            {"name": "Pack 3", "size": 3, "prix_total": 87, "prix_unitaire": 29.0},
            {"name": "Pack 5", "size": 5, "prix_total": 100, "prix_unitaire": 20.0},
            {"name": "Pack 10", "size": 10, "prix_total": 190, "prix_unitaire": 19.0},
            {"name": "Pack 20", "size": 20, "prix_total": 480, "prix_unitaire": 24.0},
            {"name": "Pack 30", "size": 30, "prix_total": 630, "prix_unitaire": 21.0},
            {"name": "Pack 50", "size": 50, "prix_total": 1000, "prix_unitaire": 20.0},
        ],
        "abos": [],
        "offres": [
            {"name": "Essai 2 séances", "prix": 39, "seances": 2, "prix_unitaire": 19.5},
            {"name": "Semaine illimitée", "prix": 79},
        ],
        "note": "Pas d'abo mensuel, juste des packs et la semaine illimitée à 79€.",
    },
    "belly": {
        "source_url": "https://studiobelly.com/",
        "confiance": "haute",
        "drop_in": 50,
        "packs": [
            {"name": "Pack 3 découverte", "size": 3, "prix_total": 95, "prix_unitaire": 31.67},
            {"name": "Pack 10", "size": 10, "prix_total": 270, "prix_unitaire": 27.0},
        ],
        "abos": [],
        "offres": [],
        "note": "5 studios Pilates reformer ; pas d'abo identifié.",
    },
    "athletx": {
        "source_url": "https://athletxrebel.com/tarifs-paris/",
        "confiance": "haute",
        "drop_in": 34,
        "packs": [
            {"name": "Pack 5", "size": 5, "prix_total": 111, "prix_unitaire": 22.2},
            {"name": "Pack 10", "size": 10, "prix_total": 207, "prix_unitaire": 20.7},
            {"name": "Pack 20", "size": 20, "prix_total": 367, "prix_unitaire": 18.35},
            {"name": "Pack 40", "size": 40, "prix_total": 615, "prix_unitaire": 15.38},
        ],
        "abos": [
            {"name": "Abo 8 séances/mois 12 mois", "prix_mensuel": 149, "seances_inclus": 8, "engagement_mois": 12},
            {"name": "Abo illimité 12 mois", "prix_mensuel": 219, "seances_inclus": None, "engagement_mois": 12},
        ],
        "offres": [{"name": "Essai", "prix": 15, "seances": 1}],
    },
    "senseclub": {
        "source_url": "https://www.sense-club.fr/tarifs",
        "confiance": "haute",
        "drop_in": 55,
        "packs": [
            {"name": "Pack 3", "size": 3, "prix_total": 115, "prix_unitaire": 38.33},
            {"name": "Pack 5", "size": 5, "prix_total": 240, "prix_unitaire": 48.0},
            {"name": "Pack 10", "size": 10, "prix_total": 440, "prix_unitaire": 44.0},
            {"name": "Pack 20", "size": 20, "prix_total": 840, "prix_unitaire": 42.0},
            {"name": "Pack 50", "size": 50, "prix_total": 1750, "prix_unitaire": 35.0},
        ],
        "abos": [
            {"name": "Abo 2 cours/sem (~8/mois)", "prix_mensuel": 300, "seances_inclus": 8, "engagement_mois": None},
        ],
        "offres": [],
    },
    "dna": {
        "source_url": "https://dnapilatesparis.com/shop",
        "confiance": "haute",
        "drop_in": 50,
        "packs": [
            {"name": "Pack 5", "size": 5, "prix_total": 220, "prix_unitaire": 44.0},
            {"name": "Pack 10", "size": 10, "prix_total": 400, "prix_unitaire": 40.0},
            {"name": "Pack 20", "size": 20, "prix_total": 760, "prix_unitaire": 38.0},
        ],
        "abos": [
            {"name": "Abo 4 cours/mois", "prix_mensuel": 150, "seances_inclus": 4, "engagement_mois": None},
            {"name": "Abo 8 cours/mois", "prix_mensuel": 250, "seances_inclus": 8, "engagement_mois": None},
            {"name": "Abo 12 cours/mois", "prix_mensuel": 340, "seances_inclus": 12, "engagement_mois": None},
        ],
        "offres": [],
    },
    "le33foch": {
        "source_url": "https://le33foch.fr/tarifs-membres/",
        "confiance": "haute",
        "drop_in": 30,
        "packs": [
            {"name": "Pack 3", "size": 3, "prix_total": 60, "prix_unitaire": 20.0},
            {"name": "Pack 10", "size": 10, "prix_total": 260, "prix_unitaire": 26.0},
            {"name": "Pack 20", "size": 20, "prix_total": 480, "prix_unitaire": 24.0},
            {"name": "Pack 33", "size": 33, "prix_total": 726, "prix_unitaire": 22.0},
            {"name": "Pack 66", "size": 66, "prix_total": 1320, "prix_unitaire": 20.0},
        ],
        "abos": [
            {"name": "Abo illimité (hors Pilates Reformer/Lagree) 12 mois", "prix_mensuel": 220, "seances_inclus": None, "engagement_mois": 12},
            {"name": "Abo étudiant", "prix_mensuel": 120, "seances_inclus": None, "engagement_mois": 12},
        ],
        "offres": [],
    },
    "banote": {
        "source_url": "https://www.banoteclub.com/",
        "confiance": "haute",
        "drop_in": 60,
        "packs": [
            {"name": "Pack 3 welcome", "size": 3, "prix_total": 110, "prix_unitaire": 36.67},
            {"name": "Pack 5 welcome", "size": 5, "prix_total": 160, "prix_unitaire": 32.0},
            {"name": "Pack 10", "size": 10, "prix_total": 450, "prix_unitaire": 45.0},
            {"name": "Pack 20", "size": 20, "prix_total": 800, "prix_unitaire": 40.0},
            {"name": "Pack 30", "size": 30, "prix_total": 1150, "prix_unitaire": 38.33},
            {"name": "Pack 40", "size": 40, "prix_total": 1400, "prix_unitaire": 35.0},
            {"name": "Pack 50", "size": 50, "prix_total": 1650, "prix_unitaire": 33.0},
        ],
        "abos": [
            {"name": "Abo 10 cours/mois 12 mois", "prix_mensuel": 350, "seances_inclus": 10, "engagement_mois": 12},
            {"name": "Abo illimité 12 mois", "prix_mensuel": 499, "seances_inclus": None, "engagement_mois": 12},
        ],
        "offres": [],
    },
    "kore": {
        "source_url": "https://www.kore-studio.com/tarifs",
        "confiance": "haute",
        "drop_in": 45,
        "packs": [
            {"name": "Pack 3", "size": 3, "prix_total": 125, "prix_unitaire": 41.67},
            {"name": "Pack 5", "size": 5, "prix_total": 200, "prix_unitaire": 40.0},
            {"name": "Pack 10", "size": 10, "prix_total": 380, "prix_unitaire": 38.0},
            {"name": "Pack 20", "size": 20, "prix_total": 690, "prix_unitaire": 34.5},
            {"name": "Pack 30", "size": 30, "prix_total": 890, "prix_unitaire": 29.67},
        ],
        "abos": [
            {"name": "Abo 4 cours/mois", "prix_mensuel": 160, "seances_inclus": 4, "engagement_mois": None},
            {"name": "Abo 8 cours/mois", "prix_mensuel": 290, "seances_inclus": 8, "engagement_mois": None},
            {"name": "Abo 12 cours/mois", "prix_mensuel": 380, "seances_inclus": 12, "engagement_mois": None},
            {"name": "Abo illimité off-peak", "prix_mensuel": 250, "seances_inclus": None, "engagement_mois": None},
        ],
        "offres": [
            {"name": "Essai", "prix": 35, "seances": 1},
            {"name": "Starter pack 3", "prix": 95, "seances": 3, "prix_unitaire": 31.67},
        ],
    },
    "burningbar": {
        "source_url": "https://burningbar.fr/en/packs-cours/",
        "confiance": "haute",
        "drop_in": 40,
        "packs": [
            {"name": "Pack 3 welcome", "size": 3, "prix_total": 60, "prix_unitaire": 20.0},
            {"name": "BB Silver 5", "size": 5, "prix_total": 190, "prix_unitaire": 38.0},
            {"name": "BB Silver 8", "size": 8, "prix_total": 288, "prix_unitaire": 36.0},
            {"name": "BB Gold 12", "size": 12, "prix_total": 384, "prix_unitaire": 32.0},
            {"name": "BB Platinium 20", "size": 20, "prix_total": 600, "prix_unitaire": 30.0},
            {"name": "BB Diamond 50", "size": 50, "prix_total": 1375, "prix_unitaire": 27.5},
        ],
        "abos": [],
        "offres": [],
        "note": "Pas d'abo mensuel — uniquement des packs.",
    },
    "snakeandtwist": {
        "source_url": "https://www.snakeandtwist.fr/",
        "confiance": "moyenne",
        "drop_in": 52,
        "packs": [
            {"name": "Pack 5 reformer", "size": 5, "prix_total": 210, "prix_unitaire": 42.0},
            {"name": "Pack 10 reformer", "size": 10, "prix_total": 390, "prix_unitaire": 39.0},
            {"name": "Pack 20 reformer", "size": 20, "prix_total": 700, "prix_unitaire": 35.0},
            {"name": "Pack 50 reformer", "size": 50, "prix_total": 1450, "prix_unitaire": 29.0},
        ],
        "abos": [],
        "offres": [{"name": "Drop-in Hot Yoga", "prix": 33}],
        "note": "Prix reformer ; cours hot yoga moins chers (33€ drop-in, packs 160-280€). Source : blog tiers.",
    },
    "santroch": {
        "source_url": "https://sant-roch.com/",
        "confiance": "moyenne",
        "drop_in": 45,
        "packs": [],
        "abos": [
            {"name": "Abo illimité", "prix_mensuel": 180, "seances_inclus": None, "engagement_mois": None},
        ],
        "offres": [],
        "note": "Sauna / bain froid (contrast therapy), pas yoga. Drop-in 45€ pour 75 min. Pack non publié.",
    },
    "anybuddy": {
        "source_url": "https://www.anybuddyapp.com/fr/club/trinquet-village-paris",
        "confiance": "haute",
        "drop_in": 54,
        "packs": [],
        "abos": [],
        "offres": [],
        "note": "Tarif par court 60 min (54€), 4 joueurs typiques. Le scraper compte des terrains-heure → CA = présents × 54€.",
    },
    "reset": {
        "source_url": "https://www.re-set.fr/",
        "confiance": "basse",
        "drop_in": None,
        "packs": [],
        "abos": [],
        "offres": [],
        "note": "URL fournie inadaptée ; pas de grille tarifaire factuelle. À renseigner manuellement.",
    },
    "barrys": {
        "source_url": "https://www.barrys.com/pricing/paris",
        "confiance": "basse",
        "drop_in": None,
        "packs": [],
        "abos": [],
        "offres": [],
        "note": "Page tarifs verrouillée derrière login/app ; pas de grille factuelle accessible publiquement.",
    },
}


def supabase_upsert(rows):
    if not URL or not KEY:
        print("(SUPABASE_URL / SUPABASE_SERVICE_KEY absents — sync ignorée)", file=sys.stderr)
        return False
    try:
        req = urllib.request.Request(
            URL + "/rest/v1/brand_prices",
            data=json.dumps(rows, ensure_ascii=False).encode("utf-8"),
            headers={
                "apikey": KEY,
                "Authorization": f"Bearer {KEY}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status < 400
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode('utf-8','ignore')[:300]}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"  upsert échoué : {e}", file=sys.stderr)
        return False


def main():
    out = {}
    # Monday Group factuel via WP API
    for key, host, label in [
        ("punch", "punch-studios.com", "Punch"),
        ("dynamo", "dynamo-cycling.com", "Dynamo"),
        ("riise", "riise-studios.com", "Riise"),
    ]:
        d = scrape_monday(host, key, label)
        if d:
            out[key] = d
            print(f"  ✅ {key}: drop-in {d['drop_in']}€, {len(d['packs'])} packs, {len(d['abos'])} abos")

    # Marques avec données vérifiées manuellement
    for key, d in MANUAL.items():
        out[key] = d
        print(f"  📋 {key}: confiance {d['confiance']}, {len(d['packs'])} packs, {len(d['abos'])} abos")

    # Fichier local committable
    with open("brand_prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n-> brand_prices.json ({len(out)} marques)")

    # Push Supabase
    rows = [
        {
            "brand_key": k,
            "source_url": v.get("source_url"),
            "confiance": v.get("confiance", "moyenne"),
            "drop_in": v.get("drop_in"),
            "packs": v.get("packs") or [],
            "abos": v.get("abos") or [],
            "offres": v.get("offres") or [],
            "note": v.get("note"),
        }
        for k, v in out.items()
    ]
    ok = supabase_upsert(rows)
    if ok:
        print(f"-> Supabase brand_prices upsert OK ({len(rows)} lignes)")


if __name__ == "__main__":
    main()
