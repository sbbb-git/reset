#!/usr/bin/env python3
"""Scrape Yoga IDF — réutilise les engines de pilates_extension_scrape."""
import json
import os
import sys
import traceback

import pilates_extension_scrape as pes

BRANDS_CFG = "yoga_extension_brands.json"
RESOLVED = "yoga_extension_resolved.json"


def main():
    if not os.path.exists(BRANDS_CFG):
        print(f"❌ {BRANDS_CFG} introuvable", file=sys.stderr); sys.exit(1)
    brands = json.load(open(BRANDS_CFG, encoding="utf-8"))
    resolved = json.load(open(RESOLVED, encoding="utf-8")) if os.path.exists(RESOLVED) else {}
    ok = err = 0
    for key, b in brands.items():
        if key.startswith("_"):
            continue
        try:
            pes.scrape_brand(key, b, resolved)
            ok += 1
        except Exception as e:  # noqa: BLE001
            err += 1
            print(f"❌ {key} : {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
    print(f"\n[YOGA] Done : {ok} brands traitées, {err} erreurs")


if __name__ == "__main__":
    main()
