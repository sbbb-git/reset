#!/usr/bin/env python3
"""Aquaboulevard de Paris (anybuddy slug aquaboulevard-de-paris) — voir anybuddy_scrape.py.

Complexe Forest Hill XV : padel + tennis + squash. Un des plus gros sites
multi-raquette de Paris intra-muros.
"""
from anybuddy_scrape import run

CONFIG = {
    "key": "aquaboulevard", "brand": "AQUABOULEVARD PARIS",
    "slug": "aquaboulevard-de-paris",
    "activities": ["padel", "tennis", "squash"],
    "host": "anybuddyapp.com",
    "store": "aquaboulevard_data.json", "html": "aquaboulevard.html",
    "csv": "aquaboulevard_creneaux.csv", "price": 48,
    "accent": "#0099cc", "accent2": "#5fbde0",
    "courts": {},
}

if __name__ == "__main__":
    run(CONFIG)
