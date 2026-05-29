#!/usr/bin/env python3
"""Space Cycle (bsport company 2440) — voir bsport_scrape.py."""
from bsport_scrape import run
CONFIG = {"key":"spacecycle","brand":"SPACE CYCLE","company":2440,
          "host":"space-cycle.com","store":"spacecycle_data.json",
          "html":"spacecycle.html","csv":"spacecycle_seances.csv","price":20,
          "accent":"#16b3c6","accent2":"#5fd6e2",
          "methode":"<b>Space Cycle &middot; plateforme bsport.</b> Présents = réservations confirmées ; capacité = effectif. Fenêtre glissante d'environ 7 jours → historique accumulé à chaque relevé (MAJ toutes les 5 min). Décompte <b>arrêté à la 1re séance à venir</b>. Bouton &laquo; Mettre à jour &raquo; = lecture en direct de l'API bsport."}
if __name__ == "__main__":
    run(CONFIG)
