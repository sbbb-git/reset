#!/usr/bin/env python3
"""Agrégat Yoga IDF — calque sur pilates_idf_compute.py mais cible
les brands Yoga (issues de yoga_extension_brands.json + les brands
historiques où Yoga est significatif).
"""
import datetime as dt
import json
import os
import sys

# Brands "core" qui ont une composante Yoga avérée (à filtrer en intra
# si la donnée distingue par type de cours).
YOGA_BRANDS = {
    # snakeandtwist : Yoga + Pilates → on garde les séances de Yoga
    "snakeandtwist": {
        "data": "snakeandtwist_data.json",
        "label": "Snake & Twist (Yoga)",
        "type": "Reformer + Yoga",
        "plateforme": "Arketa",
        "lieux": [{"nom": "Snake & Twist", "cp": "75007", "lat": 48.8595, "lng": 2.3095}],
        "filter_cours_contains": ["yoga", "flow", "vinyasa", "yin", "hatha", "stretch"],
    },
    # le33foch : Reformer + Yoga + Stretch
    "le33foch_yoga": {
        "data": "le33foch_data.json",
        "label": "Le 33 Foch (Yoga)",
        "type": "Yoga + Stretch",
        "plateforme": "Mindbody",
        "lieux": [
            {"nom": "Le 33 Foch", "cp": "75116", "lat": 48.8721, "lng": 2.2873},
            {"nom": "32 Tilsitt", "cp": "75017", "lat": 48.8754, "lng": 2.2935},
        ],
        "filter_cours_contains": ["yoga", "flow", "vinyasa", "yin", "hatha", "stretch"],
    },
    # burningbar Hot Room : c'est du Hot Power Yoga
    "burningbar_yoga": {
        "data": "burningbar_data.json",
        "label": "Burning Bar (Hot Yoga)",
        "type": "Hot Power Yoga",
        "plateforme": "Mindbody",
        "lieux": [{"nom": "Burning Bar — Hot Room", "cp": "75008", "lat": 48.8744, "lng": 2.3056}],
        # Le studio est passé de 2 salles (Hot / Reformer) à 2 adresses
        # (Paris 16 / Paris 7) : « hot » a disparu des libellés de salle, donc
        # filter_salle_contains ne rendait plus rien. On prend le complément du
        # reformer, ce qui reproduit l'intention d'origine (« tout ce qui se
        # passe en salle chaude ») en s'appuyant sur le cours, seul champ
        # resté stable. Corrige au passage 237 séances de reformer qui étaient
        # comptées ici en Hot Yoga.
        "filter_cours_exclut": ["reformer"],
    },
}

OUT = "yoga_idf_data.json"
BRANDS_EXT_CFG = "yoga_extension_brands.json"


def is_yoga_session(brand_cfg, row):
    cours = (row.get("cours") or "").lower()
    salle = (row.get("salle") or row.get("lieu") or "").lower()
    flt_c = brand_cfg.get("filter_cours_contains")
    flt_s = brand_cfg.get("filter_salle_contains")
    # Exclusion par cours : sert aux studios mixtes où c'est l'intitulé du
    # cours, et non la salle, qui sépare les disciplines (cf. burningbar).
    excl_c = brand_cfg.get("filter_cours_exclut")
    if flt_c and not any(k in cours for k in flt_c):
        return False
    if flt_s and not any(k in salle for k in flt_s):
        return False
    if excl_c and any(k in cours for k in excl_c):
        return False
    return True


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
        if not is_yoga_session(cfg, r):
            continue
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


def load_extension_brands():
    if not os.path.exists(BRANDS_EXT_CFG):
        return
    try:
        ext = json.load(open(BRANDS_EXT_CFG, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    for key, b in ext.items():
        if key.startswith("_") or key in YOGA_BRANDS:
            continue
        data_path = f"{key}_data.json"
        if not os.path.exists(data_path):
            continue
        cp = (b.get("cp") or ["75008"])[0]
        try:
            cp_int = int(cp[:2] if len(cp) >= 5 else cp)
        except ValueError:
            cp_int = 75
        YOGA_BRANDS[key] = {
            "data": data_path,
            "label": b.get("label") or key,
            "type": b.get("type") or "Yoga",
            "plateforme": b.get("platform_guess") or "extension",
            "lieux": [{
                "nom": b.get("label") or key,
                "cp": cp,
                "lat": 48.8566 + 0.005 * (cp_int % 7 - 3),
                "lng": 2.3522 + 0.008 * ((cp_int * 3) % 5 - 2),
            }],
        }


def main():
    load_extension_brands()
    store = {}
    total = 0
    for brand, cfg in YOGA_BRANDS.items():
        sessions = load_brand_sessions(brand, cfg)
        total += len(sessions)
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
        print(f"  {brand:25s} {len(sessions):>6d} séances")
    payload = {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n_brands": len(store),
        "n_sessions": total,
        "brands": store,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"-> {OUT}  ({total:,} séances Yoga IDF agrégées)")


if __name__ == "__main__":
    main()
