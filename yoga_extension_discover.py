#!/usr/bin/env python3
"""Auto-discovery Yoga IDF — même moteur que la discovery Pilates.

La boucle (budget temps, priorisation, abandon après N essais) vit dans
pilates_extension_discover.run_discovery : un seul endroit à corriger.
"""
from pilates_extension_discover import run_discovery

BRANDS_CFG = "yoga_extension_brands.json"
RESOLVED = "yoga_extension_resolved.json"


def main():
    run_discovery(BRANDS_CFG, RESOLVED, "YOGA")


if __name__ == "__main__":
    main()
