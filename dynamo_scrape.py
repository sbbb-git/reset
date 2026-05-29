#!/usr/bin/env python3
"""Dynamo Cycling (Mindbody) — voir studio_scrape.py."""
from studio_scrape import run

CONFIG = {
    "key": "dynamo", "brand": "DYNAMO CYCLING", "prefix": "dynamo",
    "host": "dynamo-cycling.com",
    "api": "https://dynamo-cycling.com/wp-json/mindbody/v1/class",
    "store": "dynamo_data.json", "html": "dynamo.html", "csv": "dynamo_seances.csv",
    "price": 20, "accent": "#faa619", "accent2": "#ffc766",
    "methode": "<b>Dynamo Cycling &middot; plateforme Mindbody.</b> Présents = personnes pointées (TotalSignedIn) ; réservés = TotalBooked ; <b>no-show</b> = réservés &minus; présents. La plateforme n'expose qu'une fenêtre glissante d'environ 7 jours → l'historique s'accumule à chaque relevé (MAJ chaque soir ~23h). Décompte <b>arrêté à la 1re séance à venir</b>. Bouton &laquo; Mettre à jour &raquo; = lecture en direct de l'API Mindbody.",
}

if __name__ == "__main__":
    run(CONFIG)
