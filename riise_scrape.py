#!/usr/bin/env python3
"""Scrap fréquentation RIISE Studios (Mindbody) — voir studio_scrape.py."""
from studio_scrape import run

CONFIG = {
    "key": "riise",
    "brand": "RIISE STUDIOS",
    "prefix": "riise",
    "host": "riise-studios.com",
    "api": "https://riise-studios.com/wp-json/mindbody/v1/class",
    "store": "riise_data.json",
    "html": "riise.html",
    "csv": "riise_seances.csv",
    "price": 20,
    "accent": "#c25a3f",
    "accent2": "#e0936f",
}

if __name__ == "__main__":
    run(CONFIG)
