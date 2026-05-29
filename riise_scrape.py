#!/usr/bin/env python3
"""Riise Studios (Mindbody) — voir studio_scrape.py."""
from studio_scrape import run

CONFIG = {
    "key": "riise", "brand": "RIISE STUDIOS", "prefix": "riise",
    "host": "riise-studios.com",
    "api": "https://riise-studios.com/wp-json/mindbody/v1/class",
    "store": "riise_data.json", "html": "riise.html", "csv": "riise_seances.csv",
    "price": 20, "accent": "#c25a3f", "accent2": "#e0936f",
    "methode": "<b>Riise Studios &middot; plateforme Mindbody.</b> Présents = personnes pointées (TotalSignedIn) ; réservés = TotalBooked ; <b>no-show</b> = réservés &minus; présents. La plateforme n'expose qu'une fenêtre glissante d'environ 7 jours → l'historique s'accumule à chaque relevé (MAJ chaque soir ~23h). Décompte <b>arrêté à la 1re séance à venir</b>. Bouton &laquo; Mettre à jour &raquo; = lecture en direct de l'API Mindbody.",
}

if __name__ == "__main__":
    run(CONFIG)
