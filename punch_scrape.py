#!/usr/bin/env python3
"""Scrap fréquentation Punch Studios (Mindbody) — voir studio_scrape.py."""
from studio_scrape import run

CONFIG = {
    "key": "punch",
    "brand": "PUNCH STUDIOS",
    "prefix": "punch",
    "host": "punch-studios.com",
    "api": "https://punch-studios.com/wp-json/mindbody/v1/class",
    "store": "punch_data.json",
    "html": "punch.html",
    "csv": "punch_seances.csv",
    "price": 20,
    "accent": "#263fff",
    "accent2": "#6f82ff",
}

if __name__ == "__main__":
    run(CONFIG)
