#!/usr/bin/env python3
"""Scrape centres Urban Foot IDF — mêmes engines que l'extension Pilates.

Le dispatch par plateforme (bsport, Mindbody, …) vit dans
pilates_extension_scrape.run_scrape : un seul endroit à corriger.
"""
from pilates_extension_scrape import run_scrape

BRANDS_CFG = "urbanfoot_extension_brands.json"
RESOLVED = "urbanfoot_extension_resolved.json"


def main():
    run_scrape(BRANDS_CFG, RESOLVED, "URBANFOOT")


if __name__ == "__main__":
    main()
