#!/usr/bin/env python3
"""4PADEL Paris 20 (anybuddy slug 4padel-paris-20) — voir anybuddy_scrape.py.

Marque 4PADEL = leader français du padel (~30 clubs en France). Paris 20e
ouverture 2022, ~4 terrains indoor.
"""
from anybuddy_scrape import run

CONFIG = {
    "key": "fourpadel_paris20", "brand": "4PADEL PARIS 20",
    "slug": "4padel-paris-20",
    "activities": ["padel"],
    "host": "anybuddyapp.com",
    "store": "fourpadel_paris20_data.json", "html": "fourpadel_paris20.html",
    "csv": "fourpadel_paris20_creneaux.csv", "price": 60,
    "accent": "#ff6b35", "accent2": "#ffa370",
    "courts": {},  # auto-named au fil de l'eau
}

if __name__ == "__main__":
    run(CONFIG)
