#!/usr/bin/env python3
"""Punch Studios (Mindbody) — voir studio_scrape.py."""
from studio_scrape import run

CONFIG = {
    "key": "punch", "brand": "PUNCH STUDIOS", "prefix": "punch",
    "host": "punch-studios.com",
    "api": "https://punch-studios.com/wp-json/mindbody/v1/class",
    "store": "punch_data.json", "html": "punch.html", "csv": "punch_seances.csv",
    "price": 20, "accent": "#263fff", "accent2": "#6f82ff",
    "methode": "<b>Punch Studios &middot; plateforme Mindbody.</b> Présents = personnes pointées (TotalSignedIn) ; réservés = TotalBooked ; <b>no-show</b> = réservés &minus; présents. La plateforme n'expose qu'une fenêtre glissante d'environ 7 jours → l'historique s'accumule à chaque relevé (MAJ chaque soir ~23h). Décompte <b>arrêté à la 1re séance à venir</b>. Bouton &laquo; Mettre à jour &raquo; = lecture en direct de l'API Mindbody.",
}

if __name__ == "__main__":
    run(CONFIG)
