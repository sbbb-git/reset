#!/usr/bin/env python3
"""Sync de tous les stores (*_data.json) vers Supabase Postgres.

Cleanest : pas de patch des scrapers — ce script lit l'état complet des
fichiers JSON committés et upserts dans `brands` + `sessions` via PostgREST.

Graceful : si SUPABASE_URL ou SUPABASE_SERVICE_KEY manquent, on no-op.

Lancé via un workflow dédié (supabase-sync.yml) après les scrapes du jour.
"""
import glob
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# Métadonnées des marques (miroir de comparateur.html).
# Une seule source de vérité pour les couleurs / catégories / plateformes.
BRANDS = {
    "reset":         {"label": "Re-SET",        "color": "#d98b63", "category": "Bootcamp",        "platform": "bsport",          "price_default": 25},
    "punch":         {"label": "Punch",         "color": "#263fff", "category": "Boxe",            "platform": "Mindbody proxy",  "price_default": 18},
    "dynamo":        {"label": "Dynamo",        "color": "#faa619", "category": "Cycling",         "platform": "Mindbody proxy",  "price_default": 22},
    "riise":         {"label": "Riise",         "color": "#c25a3f", "category": "Yoga / Wellness", "platform": "Mindbody proxy",  "price_default": 21},
    "thenewme":      {"label": "The New Me",    "color": "#b8895e", "category": "Pilates",         "platform": "bsport",          "price_default": 28},
    "lecercle":      {"label": "Le Cercle",     "color": "#d4453a", "category": "Boxe",            "platform": "bsport",          "price_default": 27},
    "spacecycle":    {"label": "Space Cycle",   "color": "#16b3c6", "category": "Cycling",         "platform": "bsport",          "price_default": 28},
    "poses":         {"label": "Poses",         "color": "#b06a8f", "category": "Pilates",         "platform": "bsport",          "price_default": 30},
    "barrys":        {"label": "Barry's",       "color": "#e2231a", "category": "Bootcamp",        "platform": "Mariana Tek",     "price_default": 30},
    "episod":        {"label": "Episod",        "color": "#e8c14d", "category": "Bootcamp",        "platform": "resamania",       "price_default": 21},
    "belly":         {"label": "Belly",         "color": "#e0699b", "category": "Pilates",         "platform": "bsport",          "price_default": 30},
    "athletx":       {"label": "AthletX",       "color": "#e0322c", "category": "Bootcamp",        "platform": "bsport",          "price_default": 19},
    "senseclub":     {"label": "Sense-Club",    "color": "#9b7ff0", "category": "Pilates",         "platform": "Mindbody widget", "price_default": 45},
    "dna":           {"label": "DNA Pilates",   "color": "#c9a24b", "category": "Pilates",         "platform": "Mindbody widget", "price_default": 38},
    "le33foch":      {"label": "Le 33 Foch",    "color": "#c4a361", "category": "Bootcamp",        "platform": "Mindbody widget", "price_default": 30},
    "banote":        {"label": "Banote",        "color": "#c9a24b", "category": "Pilates",         "platform": "Mindbody widget", "price_default": 38},
    "santroch":      {"label": "Sant-Roch",     "color": "#4a7c7e", "category": "Yoga / Wellness", "platform": "Mariana Tek",     "price_default": 40},
    "snakeandtwist": {"label": "Snake & Twist", "color": "#3a7d44", "category": "Pilates",         "platform": "Arketa",          "price_default": 28},
    "burningbar":    {"label": "Burning Bar",   "color": "#ff5a1f", "category": "Pilates",         "platform": "Mindbody widget", "price_default": 35},
    "anybuddy":      {"label": "Trinquet padel","color": "#3fa796", "category": "Padel",           "platform": "Anybuddy",        "price_default": 54},
    "driphiit":      {"label": "DRIP HIIT",     "color": "#0bbfae", "category": "Bootcamp",        "platform": "bsport",          "price_default": 29},
    "kore":          {"label": "KORE Studio",   "color": "#7a6f5c", "category": "Pilates",         "platform": "bsport",          "price_default": 28},
}


def _post(path, body, prefer="resolution=merge-duplicates,return=minimal"):
    """POST upsert vers PostgREST. Retourne True/False (graceful)."""
    if not URL or not KEY:
        return False
    try:
        req = urllib.request.Request(
            URL + path,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "apikey": KEY,
                "Authorization": f"Bearer {KEY}",
                "Content-Type": "application/json",
                "Prefer": prefer,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status < 400
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", "ignore")[:300]
        print(f"  HTTP {e.code} sur {path} : {body_txt}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"  POST {path} échoué : {e}", file=sys.stderr)
        return False


def upsert_brand(key):
    meta = BRANDS.get(key)
    if not meta:
        return False
    row = {"key": key, **meta}
    return _post("/rest/v1/brands", [row])


def upsert_sessions(brand_key, records):
    """records = list de dict (valeurs du store). Filtré pour respecter
    la contrainte `date not null` + `source_id not null`."""
    rows = []
    for i, r in enumerate(records):
        if not isinstance(r, dict):
            continue
        date = r.get("date") or r.get("Date")
        if not date or len(str(date)) < 10:
            continue
        sid = str(r.get("id") or r.get("key") or i)[:240]
        rows.append({
            "brand_key": brand_key,
            "source_id": sid,
            "date": str(date)[:10],
            "jour": r.get("jour") or "",
            "heure": r.get("heure") or "",
            "fin": r.get("fin") or "",
            "lieu": r.get("lieu") or BRANDS.get(brand_key, {}).get("label", ""),
            "cours": r.get("cours") or "",
            "coach": r.get("coach") or "",
            "capacite": int(r.get("capacite") or 0),
            "presents": int(r.get("presents") or 0),
            "finie": bool(r.get("finie")),
            "releve": r.get("releve") or None,
            "raw": r,
        })
    ok = True
    BATCH = 500
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        if not _post("/rest/v1/sessions", chunk):
            ok = False
    return ok, len(rows)


def sync_brand(path):
    """Sync 1 fichier *_data.json. Retourne (ok, nb)."""
    name = os.path.basename(path).replace("_data.json", "")
    if name not in BRANDS:
        print(f"  ⏭️  {name} (pas dans BRANDS, ignoré)")
        return True, 0
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ {name}: lecture {e}")
        return False, 0
    if not isinstance(d, dict):
        return False, 0
    # injecte la clé du dict comme "id" si absent
    records = []
    for k, v in d.items():
        if isinstance(v, dict):
            v.setdefault("id", k)
            records.append(v)
    upsert_brand(name)
    ok, n = upsert_sessions(name, records)
    print(f"  {'✅' if ok else '⚠️'} {name}: {n} séances envoyées")
    return ok, n


def main():
    if not URL or not KEY:
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY absents -> sync ignorée.", file=sys.stderr)
        return
    files = sorted(glob.glob("*_data.json"))
    total = 0
    failed = 0
    for path in files:
        ok, n = sync_brand(path)
        total += n
        if not ok:
            failed += 1
    print(f"\nTotal : {total} séances synchronisées, {failed} fichiers en échec.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
