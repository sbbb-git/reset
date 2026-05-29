#!/usr/bin/env python3
"""Scrap fréquentation Dynamo Cycling (Mindbody) — voir studio_scrape.py."""
from studio_scrape import run

CONFIG = {
    "key": "dynamo",
    "brand": "DYNAMO CYCLING",
    "prefix": "dynamo",
    "host": "dynamo-cycling.com",
    "api": "https://dynamo-cycling.com/wp-json/mindbody/v1/class",
    "store": "dynamo_data.json",
    "html": "dynamo.html",
    "csv": "dynamo_seances.csv",
    "price": 20,
    "accent": "#faa619",
    "accent2": "#ffc766",
}

if __name__ == "__main__":
    run(CONFIG)
