#!/usr/bin/env python3
"""Agrégat Pilates IDF — fusion des marques Pilates / Reformer / Lagree
déjà scrapées en un store unique pour le dashboard pilates_idf.html.

Marques agrégées :
- reset, thenewme, dna, banote, senseclub, snakeandtwist, le33foch, poses,
  kore, burningbar (partie Reformer Room — heuristique sur la salle/cours).

Sortie : pilates_idf_data.json (structure {brand: {meta:{...}, sessions:[...]}}).
"""
import datetime as dt
import json
import os
import re
import sys

# Mapping brand → (fichier data, catégorie technique, GPS du studio principal)
# GPS approximatifs pour les markers carte (centre arrondissement si studio
# multi-lieux).
PILATES_BRANDS = {
    "reset": {
        "data": "reset_data.json",
        "label": "Re-SET",
        "type": "Reformer",
        "plateforme": "bsport",
        "lieux": [
            {"nom": "Re-SET Saint-Honoré", "cp": "75008", "lat": 48.8702, "lng": 2.3175},
        ],
    },
    "thenewme": {
        "data": "thenewme_data.json",
        "label": "The New Me",
        "type": "Reformer",
        "plateforme": "bsport",
        "lieux": [
            {"nom": "The New Me Wagram", "cp": "75017", "lat": 48.8842, "lng": 2.3020},
        ],
    },
    "dna": {
        "data": "dna_data.json",
        "label": "DNA Pilates",
        "type": "Reformer",
        "plateforme": "Mindbody",
        "lieux": [
            {"nom": "DNA Saint-Germain", "cp": "75006", "lat": 48.8530, "lng": 2.3349},
            {"nom": "DNA Madeleine", "cp": "75008", "lat": 48.8704, "lng": 2.3245},
        ],
    },
    "banote": {
        "data": "banote_data.json",
        "label": "Banote",
        "type": "Lagree / Mégaformer",
        "plateforme": "Mindbody",
        "lieux": [
            {"nom": "Banote Paris 16e", "cp": "75016", "lat": 48.8634, "lng": 2.2812},
            {"nom": "Banote Charenton", "cp": "94220", "lat": 48.8210, "lng": 2.4135},
            {"nom": "Banote Le Collectionneur", "cp": "75017", "lat": 48.8775, "lng": 2.2970},
        ],
    },
    "senseclub": {
        "data": "senseclub_data.json",
        "label": "Sense-Club",
        "type": "Mégaformer",
        "plateforme": "Mindbody",
        "lieux": [
            {"nom": "Sense-Club", "cp": "75009", "lat": 48.8742, "lng": 2.3387},
        ],
    },
    "snakeandtwist": {
        "data": "snakeandtwist_data.json",
        "label": "Snake & Twist",
        "type": "Reformer + Yoga",
        "plateforme": "Arketa",
        "lieux": [
            {"nom": "Snake & Twist", "cp": "75007", "lat": 48.8595, "lng": 2.3095},
        ],
    },
    "le33foch": {
        "data": "le33foch_data.json",
        "label": "Le 33 Foch",
        "type": "Reformer + Disciplines",
        "plateforme": "Mindbody",
        "lieux": [
            {"nom": "Le 33 Foch", "cp": "75116", "lat": 48.8721, "lng": 2.2873},
            {"nom": "32 Tilsitt", "cp": "75017", "lat": 48.8754, "lng": 2.2935},
        ],
    },
    "poses": {
        "data": "poses_data.json",
        "label": "Poses",
        "type": "Reformer",
        "plateforme": "bsport",
        "lieux": [
            {"nom": "Poses Studio", "cp": "75002", "lat": 48.8682, "lng": 2.3422},
        ],
    },
    "kore": {
        "data": "kore_data.json",
        "label": "KORE Studio",
        "type": "Lagree hybride",
        "plateforme": "bsport",
        "lieux": [
            {"nom": "KORE Studio", "cp": "75008", "lat": 48.8732, "lng": 2.3128},
        ],
    },
    "burningbar": {
        "data": "burningbar_data.json",
        "label": "Burning Bar (Reformer Room)",
        "type": "Reformer + Hot Room",
        "plateforme": "Mindbody",
        "lieux": [
            {"nom": "Burning Bar — The Reformer Room", "cp": "75008", "lat": 48.8744, "lng": 2.3056},
        ],
        # Filtre uniquement les cours "Reformer" (salle Reformer Room)
        "filter_salle": ["reformer"],
    },
}

OUT = "pilates_idf_data.json"
BRANDS_EXT_CFG = "pilates_extension_brands.json"


def load_extension_brands():
    """Ajoute dynamiquement les brands extension qui ont déjà un *_data.json."""
    if not os.path.exists(BRANDS_EXT_CFG):
        return
    try:
        ext = json.load(open(BRANDS_EXT_CFG, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    for key, b in ext.items():
        if key in PILATES_BRANDS:
            continue
        data_path = f"{key}_data.json"
        if not os.path.exists(data_path):
            continue
        # CP → lat/lng approximatif Paris (centre arrond moyen, pour la carte)
        # On utilisera juste un GPS fallback ; les vraies coords pourraient être
        # backfilled plus tard via pilates_geocode_backfill.py.
        cp = (b.get("cp") or ["75008"])[0]
        try:
            cp_int = int(cp[:2] if len(cp) >= 5 else cp)
        except ValueError:
            cp_int = 75
        PILATES_BRANDS[key] = {
            "data": data_path,
            "label": b.get("label") or key,
            "type": b.get("type") or "Pilates",
            "plateforme": b.get("platform_guess") or "extension",
            "lieux": [{
                "nom": b.get("label") or key,
                "cp": cp,
                # GPS Paris-centre par défaut, à raffiner via geocode
                "lat": 48.8566 + 0.005 * (cp_int % 7 - 3),
                "lng": 2.3522 + 0.008 * ((cp_int * 3) % 5 - 2),
            }],
        }


def is_reformer_session(brand, row):
    """Pour burningbar : ne garder que les séances de la Reformer Room."""
    cfg = PILATES_BRANDS[brand]
    flt = cfg.get("filter_salle")
    if not flt:
        return True
    salle = (row.get("salle") or row.get("lieu") or "").lower()
    return any(k in salle for k in flt)


def load_brand_sessions(brand_key, cfg):
    path = cfg["data"]
    if not os.path.exists(path):
        return []
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    rows = d if isinstance(d, list) else list(d.values())
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if not is_reformer_session(brand_key, r):
            continue
        # Normalisation cross-brand
        out.append({
            "date": r.get("date"),
            "jour": r.get("jour"),
            "heure": r.get("heure"),
            "lieu": r.get("lieu") or r.get("salle"),
            "cours": r.get("cours"),
            "coach": r.get("coach", ""),
            "presents": r.get("presents", 0) or 0,
            "capacite": r.get("capacite", 0) or 0,
            "statut": r.get("statut", ""),
            "finie": bool(r.get("finie") or r.get("locked")),
            "releve": r.get("releve", ""),
        })
    return out


def main():
    load_extension_brands()
    store = {}
    total_sessions = 0
    for brand, cfg in PILATES_BRANDS.items():
        sessions = load_brand_sessions(brand, cfg)
        total_sessions += len(sessions)
        store[brand] = {
            "meta": {
                "label": cfg["label"],
                "type": cfg["type"],
                "plateforme": cfg["plateforme"],
                "lieux": cfg["lieux"],
                "n_sessions": len(sessions),
            },
            "sessions": sessions,
        }
        print(f"  {brand:18s} {len(sessions):>6d} séances")

    payload = {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n_brands": len(store),
        "n_sessions": total_sessions,
        "brands": store,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"-> {OUT}  ({total_sessions:,} séances Pilates IDF agrégées)")


if __name__ == "__main__":
    main()
