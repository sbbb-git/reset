#!/usr/bin/env python3
"""KORE Studio (bsport company 2021) — voir bsport_scrape.py.

Studio Reformer Paris 2 (103 rue Réaumur) + Paris 11 (15 bd Richard-Lenoir).
12 reformers hybrides, classe 50 min, membership 2021. Ouverture 2023.
"""
from bsport_scrape import run
CONFIG = {"key":"kore","brand":"KORE STUDIO","company":2021,
          "host":"kore-studio.com","store":"kore_data.json",
          "html":"kore.html","csv":"kore_seances.csv","price":28,
          "accent":"#7a6f5c","accent2":"#bfae8e",
          "methode":"<b>KORE Studio &middot; plateforme bsport.</b> Présents = réservations confirmées ; capacité = effectif. Fenêtre glissante d'environ 7 jours → historique accumulé à chaque relevé (MAJ toutes les 5 min). Décompte <b>arrêté à la 1re séance à venir</b>. Bouton &laquo; Mettre à jour &raquo; = lecture en direct de l'API bsport."}
if __name__ == "__main__":
    run(CONFIG)
