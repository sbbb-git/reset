#!/usr/bin/env python3
"""Auto-discovery Yoga IDF — réutilise les détecteurs de
pilates_extension_discover.py mais sur le catalogue yoga.
"""
import json
import os
import sys

# Hérite des détecteurs et de la logique investigate() du module Pilates
import pilates_extension_discover as ped

BRANDS_CFG = "yoga_extension_brands.json"
RESOLVED = "yoga_extension_resolved.json"


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

    import datetime as dt
    n_new = n_skip = n_unknown = 0
    for key, b in brands.items():
        if key.startswith("_"):
            continue
        if key in resolved and resolved[key].get("platform") not in (None, "unknown"):
            n_skip += 1
            continue
        url = b.get("url")
        if not url:
            continue
        print(f"→ {key:30s} ({url})")
        res = ped.investigate(url)
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

    print(f"\n[YOGA] Discovery : {n_new} nouveaux + {n_skip} déjà résolus + {n_unknown} unknown / {len([k for k in brands if not k.startswith('_')])} brands")


if __name__ == "__main__":
    main()
